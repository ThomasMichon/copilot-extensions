"""Build one approved Linux amd64 recipe to a local OCI archive; never deploy."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

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


def execute(argv: list[str], *, timeout: float = 30) -> str:
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, **flags) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, argv, stdout, stderr)
        return stdout


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


def build(inputs: object, output: Path, engine: str = "docker", runner=execute,
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
    bounds = verify_builder(engine, approval["builder"], runner)
    output.mkdir(parents=True, exist_ok=False)
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

    save_receipt()
    try:
        runner(build_command(engine, approval["builder"]["name"], context, archive),
               timeout=approval["timeout_seconds"])
        verify_builder(engine, approval["builder"], runner)
        receipt.update(oci_identity(archive))
        receipt["archive_sha256"] = file_sha256(archive)
        receipt["status"] = "built-not-runtime-validated"
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
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
