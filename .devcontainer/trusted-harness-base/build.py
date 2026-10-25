"""Build one approved Linux amd64 recipe to a local OCI archive; never deploy."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parent
CONTEXT = ("Dockerfile", "dependencies.lock.json", "install.py", "build.py",
           "entrypoint.sh", "profile.sh", "sshd.conf")


def context_files(root: Path = ROOT) -> dict[str, bytes]:
    result = {}
    for name in CONTEXT:
        path = root / name
        if path.is_symlink() or path.resolve().parent != root.resolve():
            raise ValueError("Recipe files must be regular, local files")
        result[name] = path.read_bytes().replace(b"\r\n", b"\n")
    return result


def context_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def validate_inputs(inputs: object, digest: str) -> dict:
    if not isinstance(inputs, dict) or set(inputs) != {
        "schema_version", "source_revision", "context_sha256", "builder", "timeout_seconds",
    }:
        raise ValueError("Exactly the versioned approval fields are required")
    if type(inputs["schema_version"]) is not int or inputs["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    for name, length in (("source_revision", 40), ("context_sha256", 64)):
        if not isinstance(inputs[name], str) or not re.fullmatch(
            f"[0-9a-f]{{{length}}}", inputs[name],
        ):
            raise ValueError("Invalid " + name)
    if inputs["context_sha256"] != digest:
        raise ValueError("Approved context differs from this recipe")
    timeout = inputs["timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("An explicit bounded build timeout is required")
    selected = inputs["builder"]
    if not isinstance(selected, dict) or set(selected) != {
        "name", "container_id", "max_memory_bytes", "max_cpus",
    }:
        raise ValueError("An explicitly admitted bounded builder is required")
    for key, pattern in (("name", r"[a-z][a-z0-9_.-]{0,62}"),
                         ("container_id", r"[0-9a-f]{64}")):
        if not isinstance(selected[key], str) or not re.fullmatch(pattern, selected[key]):
            raise ValueError("Invalid builder " + key)
    for key, minimum in (("max_memory_bytes", 134217728), ("max_cpus", 1)):
        if type(selected[key]) is not int or selected[key] < minimum:
            raise ValueError("Invalid builder bound")
    return inputs


def validate_age(lock: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if lock["minimum_age_seconds"] != 604800 or lock["platform"] != "linux/amd64":
        raise ValueError("The platform and seven-day minimum are fixed")
    for artifact in (lock["base"], lock["apt"], *lock["archives"].values()):
        published = datetime.fromisoformat(artifact["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None or (now - published).total_seconds() < 604800:
            raise ValueError("Missing, future or unseasoned dependency evidence")
    snapshot = re.search(r"/(\d{8}T\d{6}Z)/", lock["apt"]["snapshot"])
    if not snapshot:
        raise ValueError("A fixed Debian snapshot is required")
    captured = datetime.strptime(snapshot[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if (now - captured).total_seconds() < 604800:
        raise ValueError("The frozen dependency index must also be seasoned")


CLEANUP_TIMEOUT = 1


class ChildCustodyError(subprocess.SubprocessError):
    """The CLI outcome or invocation-owned cleanup cannot be confirmed."""


@contextmanager
def windows_job():
    """Contain only this invocation, before its gated launcher can spawn the CLI."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                    ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())

        def assign(process):
            if not kernel.AssignProcessToJobObject(job, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())

        def terminate():
            if not kernel.TerminateJobObject(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())

        yield assign, terminate
    finally:
        kernel.CloseHandle(job)


def observe_unreaped(process):
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL or process.returncode is not None:
        raise ChildCustodyError("CLI child custody was released")
    try:
        status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except OSError as error:
        raise ChildCustodyError("CLI child custody is unconfirmed") from error
    if status is not None and status.si_pid != process.pid:
        raise ChildCustodyError("CLI child identity changed")
    return status


def communicate_unreaped(process, argv, timeout):
    deadline = time.monotonic() + timeout
    output = [bytearray(), bytearray()]

    def remaining():
        duration = deadline - time.monotonic()
        if duration <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        return duration

    with selectors.DefaultSelector() as selector:
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            for key, _ in selector.select(remaining()):
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if chunk:
                    output[key.data].extend(chunk)
                else:
                    selector.unregister(key.fileobj)
    while observe_unreaped(process) is None:
        time.sleep(min(0.01, remaining()))
    return tuple(bytes(data).decode().replace("\r\n", "\n").replace("\r", "\n") for data in output)


def execute(argv: list[str], *, timeout: float = 30, env=None) -> str:
    if os.name == "nt":
        with windows_job() as (assign, terminate):
            # The job assignment precedes the byte that allows any CLI descendant.
            launcher = (
                "import json,subprocess,sys; "
                "gate=sys.stdin.buffer.read(1); "
                "sys.exit(subprocess.call(json.loads(sys.argv[1]), stdin=subprocess.DEVNULL, "
                "creationflags=subprocess.CREATE_NO_WINDOW) if gate == b'1' else 1)"
            )
            return execute_contained(
                [sys.executable, "-B", "-c", launcher, json.dumps(argv)], argv,
                timeout, assign, terminate, creationflags=subprocess.CREATE_NO_WINDOW,
                stdin=subprocess.PIPE, env=env,
            )
    if (not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT")
            or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL):
        raise ChildCustodyError("CLI child requires exclusive unreaped POSIX custody")
    return execute_contained(argv, argv, timeout, lambda p: None, None,
                             start_new_session=True, stdin=subprocess.DEVNULL, env=env)


def execute_contained(command, argv, timeout, assign, terminate, **flags):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, **flags)

    def stop(force=False):
        if terminate is not None:
            terminate()
        else:
            # WNOWAIT plus exclusive Popen ownership pins this group leader's PID.
            observe_unreaped(process)
            try:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                pass

    def communicate(duration):
        if terminate is None:
            return communicate_unreaped(process, argv, duration)
        return process.communicate(input="1" if process.stdin and not process.stdin.closed else None,
                                   timeout=duration)

    try:
        assign(process)
        try:
            stdout, stderr = communicate(timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop()
            try:
                communicate(CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                stop(force=True)
                try:
                    communicate(CLEANUP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass
            raise
    finally:
        failed = sys.exc_info()[0] is not None
        try:
            stop(force=True)
            if terminate is not None and process.poll() is None:
                process.kill()
            process.wait(timeout=CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            if not failed:
                raise ChildCustodyError("CLI child cleanup is unconfirmed")
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, argv, stdout, stderr)
    return stdout


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_output(path: Path) -> None:
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        fsync_directory(directory.parent)
    if not missing:
        raise FileExistsError(path)


BUILDKIT_IMAGE = ("moby/buildkit@sha256:"
                  "2be0a5be7ae3cd9268f6836782e9690cce7bc4124142f7700f1641af5ccca8de")


def validate_driver_options(options, selected: dict) -> None:
    if (not isinstance(options, dict) or set(options) != {
            "image", "memory", "memory-swap", "cpu-period", "cpu-quota",
    } or any(not isinstance(value, str) for value in options.values())
            or options["image"] != BUILDKIT_IMAGE):
        raise ValueError("Only the digest-pinned, explicitly bounded BuildKit driver is supported")

    def memory(value):
        match = re.fullmatch(r"([1-9][0-9]{0,15})([kmg]?)", value)
        if not match:
            raise ValueError("Unsupported BuildKit memory bound")
        return int(match[1]) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match[2]]

    limit = memory(options["memory"])
    if not 134217728 <= limit <= selected["max_memory_bytes"] or memory(options["memory-swap"]) != limit:
        raise ValueError("BuildKit driver memory/swap exceeds admission")
    for key in ("cpu-period", "cpu-quota"):
        if not re.fullmatch(r"[1-9][0-9]{0,8}", options[key]):
            raise ValueError("Unsupported BuildKit CPU bound")
    period, quota = int(options["cpu-period"]), int(options["cpu-quota"])
    if not 1000 <= period <= 1000000 or not 1000 <= quota <= selected["max_cpus"] * period:
        raise ValueError("BuildKit driver CPU bound exceeds admission")


def docker_environment(selected: dict, output: Path) -> dict[str, str]:
    ambient = dict(os.environ)
    home = Path(ambient.get("USERPROFILE" if os.name == "nt" else "HOME") or Path.home())
    docker = Path(ambient.get("DOCKER_CONFIG") or home / ".docker")
    buildx = Path(ambient.get("BUILDX_CONFIG") or docker / "buildx")
    with (buildx / "instances" / selected["name"]).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Selected builder metadata exceeds the bounded local schema")
    metadata = json.loads(raw)
    if not isinstance(metadata, dict) or set(metadata) != {"Name", "Driver", "Dynamic", "Nodes"}:
        raise ValueError("Selected builder metadata has unsupported fields")
    nodes = metadata.get("Nodes", [])
    if (metadata.get("Name") != selected["name"] or metadata.get("Driver") != "docker-container"
            or metadata.get("Dynamic", False) is not False
            or not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)):
        raise ValueError("Only one explicitly selected static local builder may be copied")
    node = nodes[0]
    if (set(node) != {"Name", "Endpoint", "DriverOpts", "Files", "Flags", "Platforms"}
            or not isinstance(node.get("Name"), str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", node["Name"])
            or node.get("Endpoint") not in ("default", "unix:///var/run/docker.sock")
            or node["Files"] not in (None, {}) or node["Platforms"] not in (None, [])
            or node["Flags"] not in ([], ["--allow-insecure-entitlement=network.host"])):
        raise ValueError("Selected builder metadata has unsupported endpoint or options")
    validate_driver_options(node["DriverOpts"], selected)
    snapshot = metadata
    client = output / "docker-client"
    for directory in (client, client / "buildx", client / "buildx" / "instances"):
        directory.mkdir(mode=0o700)
        fsync_directory(directory.parent)
    # A nonempty, credential-free auth map also disables Docker's native helper autodetection.
    (client / "config.json").write_text(json.dumps({"auths": {"": {}}}))
    (client / "buildx" / "instances" / selected["name"]).write_text(json.dumps(snapshot))
    environment = {key: value for key, value in ambient.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
    environment.update(HOME=str(client), USERPROFILE=str(client), DOCKER_CONFIG=str(client),
                       BUILDX_CONFIG=str(client / "buildx"))
    return environment


def verify_builder(engine: str, selected: dict, runner) -> dict:
    def local_context() -> None:
        endpoint = json.loads(runner(
            [engine, "--context", "default", "context", "inspect", "default",
             "--format", "{{json .Endpoints.docker}}"], timeout=30,
        ))
        if endpoint.get("Host") != "unix:///var/run/docker.sock" or endpoint.get("SkipTLSVerify") is not False:
            raise ValueError("Only the explicit default local Unix Docker context is supported")

    local_context()
    listing = runner(
        [engine, "--context", "default", "buildx", "ls", "--format", "{{json .}}"], timeout=30,
    )
    candidates = {}
    for line in listing.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("Expected structured Buildx builder records")
        if record.get("Name") == selected["name"]:
            candidates[json.dumps(record, sort_keys=True, separators=(",", ":"))] = record
    if len(candidates) != 1:
        raise ValueError("Selected builder is missing or has conflicting same-name records")
    info = next(iter(candidates.values()))
    nodes = info.get("Nodes", [])
    if (info.get("Name") != selected["name"] or info.get("Driver") != "docker-container"
            or len(nodes) != 1 or nodes[0].get("Status") != "running"):
        raise ValueError("Requires one running, explicitly selected docker-container builder")
    if nodes[0].get("Endpoint") not in ("default", "unix:///var/run/docker.sock"):
        raise ValueError("Selected Buildx node is not bound to the inspected local Docker endpoint")
    containers = json.loads(runner(
        [engine, "--context", "default", "inspect", selected["container_id"]], timeout=30,
    ))
    if len(containers) != 1:
        raise ValueError("Builder container identity is ambiguous")
    container = containers[0]
    if (container.get("Id") != selected["container_id"]
            or container.get("Name") != "/buildx_buildkit_" + nodes[0]["Name"]
            or container.get("State", {}).get("Running") is not True):
        raise ValueError("Builder does not match the admitted running instance")
    config = container.get("Config")
    if not isinstance(config, dict) or config.get("Image") != BUILDKIT_IMAGE:
        raise ValueError("Running builder image differs from the admitted digest-pinned BuildKit image")
    limits = container["HostConfig"]
    memory = limits.get("Memory", 0)
    nano = limits.get("NanoCpus", 0)
    quota = limits.get("CpuQuota", 0)
    period = limits.get("CpuPeriod", 0) or 100000
    cpus = nano / 1_000_000_000 if nano > 0 else quota / period
    if not 0 < memory <= selected["max_memory_bytes"] or not 0 < cpus <= selected["max_cpus"]:
        raise ValueError("Builder CPU/memory bounds are absent or exceed admission")
    if limits.get("MemorySwap") != memory:
        raise ValueError("The admitted builder must disable swap beyond its memory bound")
    local_context()
    return {"memory_bytes": memory, "cpus": cpus, "swap_disabled": True}


def build_command(engine: str, selected: str, context: Path, archive: Path) -> list[str]:
    return [engine, "--context", "default", "buildx", "build", "--builder", selected,
            "--platform", "linux/amd64",
            "--provenance=false", "--sbom=false", "--network=default",
            "--output", "type=oci,dest=" + str(archive),
            "--file", str(context / "Dockerfile"), str(context)]


def oci_identity(archive: Path) -> dict[str, str]:
    with tarfile.open(archive) as tar:
        def read(name: str) -> bytes:
            member = tar.getmember(name)
            if not member.isfile() or member.size > 16 * 1024 * 1024:
                raise ValueError("Invalid OCI metadata member")
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("Missing OCI metadata")
            return stream.read()

        def blob(digest: str) -> bytes:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("Only SHA-256 OCI identities are supported")
            data = read("blobs/sha256/" + digest[7:])
            if hashlib.sha256(data).hexdigest() != digest[7:]:
                raise ValueError("OCI metadata checksum mismatch")
            return data

        index = json.loads(read("index.json"))
        if len(index["manifests"]) != 1:
            raise ValueError("Expected one Linux amd64 manifest")
        manifest_digest = index["manifests"][0]["digest"]
        manifest = json.loads(blob(manifest_digest))
        config_digest = manifest["config"]["digest"]
        config = json.loads(blob(config_digest))
        if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
            raise ValueError("Wrong OCI platform")
        for layer in manifest["layers"]:
            digest = layer["digest"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("Unsupported layer digest")
            member = tar.getmember("blobs/sha256/" + digest[7:])
            if not member.isfile() or member.size != layer["size"]:
                raise ValueError("Invalid OCI layer")
            stream = tar.extractfile(member)
            if stream is None or hashlib.file_digest(stream, "sha256").hexdigest() != digest[7:]:
                raise ValueError("OCI layer checksum mismatch")
    return {"manifest_digest": manifest_digest, "config_digest": config_digest}


def build(inputs: object, output: Path, engine: str = "docker", runner=None,
          root: Path = ROOT, now: datetime | None = None) -> dict:
    files = context_files(root)
    approval = validate_inputs(inputs, context_digest(files))
    lock = json.loads(files["dependencies.lock.json"])
    validate_age(lock, now)
    output = output.resolve()
    if output == root.resolve() or root.resolve() in output.parents:
        raise ValueError("Artifact output must be outside the recipe directory")
    # BuildKit CSV output options cannot represent an unescaped comma safely.
    if "," in str(output) or "\n" in str(output) or "\r" in str(output):
        raise ValueError("Unsupported artifact path")
    create_output(output)
    runner = partial(runner or execute, env=docker_environment(approval["builder"], output))
    bounds = verify_builder(engine, approval["builder"], runner)
    context = output / "context"
    context.mkdir()
    for name, content in files.items():
        (context / name).write_bytes(content)
    archive = output / "image.oci.tar"
    receipt = {"schema_version": 1, "source_revision": approval["source_revision"],
               "context_sha256": approval["context_sha256"], "platform": "linux/amd64",
               "approval_sha256": hashlib.sha256(
                   json.dumps(approval, sort_keys=True, separators=(",", ":")).encode()
               ).hexdigest(),
               "builder_container_id": approval["builder"]["container_id"],
               "docker_context": "default", "endpoint_kind": "local-unix",
               "resource_bounds": bounds, "timeout_seconds": approval["timeout_seconds"],
               "status": "build-started-outcome-unconfirmed",
               "source_revision_authority": "caller-supplied"}

    def save_receipt() -> None:
        staged = output / "build-receipt.pending"
        with staged.open("w") as stream:
            stream.write(json.dumps(receipt, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        staged.replace(output / "build-receipt.json")
        fsync_directory(output)

    save_receipt()
    try:
        runner(build_command(engine, approval["builder"]["name"], context, archive),
               timeout=approval["timeout_seconds"])
        verify_builder(engine, approval["builder"], runner)
        receipt.update(oci_identity(archive))
        receipt["archive_sha256"] = file_sha256(archive)
        receipt["status"] = "built-not-runtime-validated"
    except (subprocess.TimeoutExpired, KeyboardInterrupt, ChildCustodyError):
        receipt["status"] = "cancellation-requested-builder-outcome-unconfirmed"
        raise
    except BaseException:
        receipt["status"] = "build-failed"
        raise
    finally:
        save_receipt()
        (output / "dependencies.lock.json").write_bytes(files["dependencies.lock.json"])
    return receipt


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    try:
        if args.describe:
            print(json.dumps({"schema_version": 1, "platform": "linux/amd64",
                              "context_sha256": context_digest(context_files())}))
        else:
            if args.inputs is None or args.output is None:
                parser.error("--inputs and --output are required")
            print(json.dumps(build(json.loads(args.inputs.read_text()), args.output, args.docker)))
    except (ValueError, OSError, KeyError, tarfile.TarError, subprocess.SubprocessError) as error:
        print("Build refused: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancellation requested; builder outcome requires owner inspection.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
