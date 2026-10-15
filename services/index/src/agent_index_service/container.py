"""Offline generic Linux image context, not fleet/Gateway deployment or migration.

The caller selects an approved digest-pinned Python base and independently
trusted release revision. Hashes bind integrity, not source authenticity.
Prepared contexts contain only verified wheels and generated public contracts;
no host config, credential, source checkout or runtime database is copied.
The explicit target describes the approved base, not an inferred host ABI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import stat
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .release import inspect_wheel, verify_descriptor
from .staging import (
    _PROBE,
    _bytes,
    _copy,
    _environment,
    _json,
    _path,
    _run,
    _unique,
    hashed_requirements,
)

_SCHEMA = "agent-index-service.container-context"
_ARCHITECTURES = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}
_CONTEXT_LIMIT = 1024 * 1024 * 1024
_FILE_LIMIT = 512 * 1024 * 1024
_CONTEXT_FILE_LIMIT = 4096


class ContainerError(ValueError):
    """Invalid inputs, incomplete wheel closure or conflicting prepared output."""


class ContextCleanupError(ContainerError):
    """Preparation failed and its owned output could not safely be removed."""


@dataclass(frozen=True)
class ContainerTarget:
    python_version: str
    platform: str
    glibc_version: str

    def environment(self) -> dict[str, str]:
        if any(not isinstance(value, str) for value in (
            self.python_version, self.platform, self.glibc_version,
        )):
            raise ContainerError("target values must be strings")
        try:
            version = Version(self.python_version)
        except InvalidVersion as exc:
            raise ContainerError("target Python version is malformed") from exc
        if (
            version.is_prerelease or version.is_postrelease or version.local
            or version.epoch or len(version.release) != 3 or version.release[0] != 3
            or self.platform not in _ARCHITECTURES
            or re.fullmatch(r"2\.[0-9]+", self.glibc_version) is None
        ):
            raise ContainerError("target requires Linux, exact stable Python 3.x.y and glibc 2.x")
        if not 10 <= version.release[1] <= 99 or not 5 <= int(self.glibc_version.split(".")[1]) <= 99:
            raise ContainerError("target Python/glibc version is outside supported tag bounds")
        return {
            "python_version": ".".join(map(str, version.release[:2])),
            "python_full_version": str(version), "os_name": "posix", "sys_platform": "linux",
            "platform_machine": _ARCHITECTURES[self.platform],
            "platform_system": "Linux", "platform_release": "", "platform_version": "",
            "platform_python_implementation": "CPython", "implementation_name": "cpython",
            "implementation_version": str(version),
        }

    def tags(self):
        environment = self.environment()
        architecture = environment["platform_machine"]
        minor = int(self.glibc_version.split(".")[1])
        minimum = 5 if architecture == "x86_64" else 17
        platforms = [
            f"manylinux_2_{value}_{architecture}" for value in range(minor, minimum - 1, -1)
        ]
        for value, name in ((17, "manylinux2014"), (12, "manylinux2010"), (5, "manylinux1")):
            if minor >= value and (architecture == "x86_64" or value == 17):
                platforms.append(f"{name}_{architecture}")
        platforms.append(f"linux_{architecture}")
        python = Version(self.python_version).release[:2]
        interpreter = f"cp{python[0]}{python[1]}"
        return set(cpython_tags(python, abis=[interpreter, "abi3", "none"], platforms=platforms)) | set(
            compatible_tags(python, interpreter=interpreter, platforms=platforms),
        )


def _base(value: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}", value) is None
        or ".." in value or "//" in value
    ):
        raise ContainerError("base image must be a plain approved OCI reference pinned by sha256")
    reference = value.split("@", 1)[0]
    if reference.rfind(":") > reference.rfind("/"):
        reference, tag = reference.rsplit(":", 1)
        if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag) is None:
            raise ContainerError("base image tag is malformed")
    parts = reference.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry = parts.pop(0)
        host, separator, port = registry.partition(":")
        if (
            re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host) is None
            or separator and (not port.isdecimal() or not 1 <= int(port) <= 65535)
        ):
            raise ContainerError("base image registry is malformed")
    if not parts or any(
        re.fullmatch(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*", part) is None
        for part in parts
    ):
        raise ContainerError("base image repository is malformed")
    return value


def _closure(metadata: dict, extras: dict, environment: dict, target: ContainerTarget) -> None:
    supported = target.tags()
    pending = [(name, extra) for name in metadata for extra in ("", *extras.get(name, []))]
    seen = set()
    while pending:
        name, extra = pending.pop()
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        item = metadata[name]
        if not SpecifierSet(item["requires_python"]).contains(
            environment["python_full_version"], prereleases=True,
        ):
            raise ContainerError(f"wheel Requires-Python does not support target: {name}")
        if not parse_wheel_filename(item["artifact"]["file"])[3] & supported:
            raise ContainerError(f"wheel tags do not support the selected target: {name}")
        if extra and extra not in item["provides_extra"]:
            raise ContainerError(f"wheel does not provide requested extra: {name}[{extra}]")
        for raw in item["requires_dist"]:
            req = Requirement(raw)
            if req.url:
                raise ContainerError("direct URL dependencies are not permitted in image artifacts")
            if req.marker is not None and re.search(
                r"\bplatform_(release|version)\b", str(req.marker),
            ):
                raise ContainerError("kernel-dependent markers require separate deployment admission")
            if req.marker is not None and not req.marker.evaluate({**environment, "extra": extra}):
                continue
            dependency = canonicalize_name(req.name)
            if dependency not in metadata or not req.specifier.contains(
                metadata[dependency]["artifact"]["version"], prereleases=True,
            ):
                raise ContainerError(f"native dependency closure missing or incompatible: {dependency}")
            pending.extend((dependency, requested) for requested in (
                "", *(canonicalize_name(value) for value in req.extras),
            ))


_BUILD = r'''"""Generated trusted adapter: verify bytes first, then offline native install."""
import hashlib,json,os,platform,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
contract_bytes=(root/'context.json').read_bytes()
expected_digest=(sys.argv[1] if len(sys.argv)>1 and sys.argv[1]!='--verify-only'
                 else os.environ.get('CONTEXT_SHA256',''))
if hashlib.sha256(contract_bytes).hexdigest()!=expected_digest:
    raise SystemExit('context contract digest mismatch')
contract=json.loads(contract_bytes)
expected={item['file'] for item in contract['files']} | {'context.json'}
actual={path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
if expected!=actual:
    raise SystemExit('context file inventory mismatch')
for item in contract['files']:
    path=root/item['file']
    if path.is_symlink() or path.resolve().parent not in (root,root/'wheels'):
        raise SystemExit('unsafe context file')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(262144),b''):
            digest.update(block)
    if digest.hexdigest()!=item['sha256']:
        raise SystemExit('context artifact digest mismatch')
if '--verify-only' in sys.argv:
    print(json.dumps({'verified':True}))
    raise SystemExit(0)
target=contract['target']
if (sys.platform!='linux' or platform.python_implementation()!='CPython'
    or platform.python_version()!=target['python_version']
    or platform.machine()!=contract['architecture']
    or platform.libc_ver()!=('glibc',target['glibc_version'])):
    raise SystemExit('approved base does not match explicit target contract')
scratch=Path('/tmp/index-image-build')
scratch.mkdir(mode=0o700)
env={'PATH':str(Path(sys.executable).parent),'HOME':str(scratch),
     'TMPDIR':str(scratch),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1',
     'LANG':'C','LC_ALL':'C','PIP_CONFIG_FILE':os.devnull}
subprocess.run([sys.executable,'-I','-m','venv','/opt/agent-index'],env=env,check=True)
python='/opt/agent-index/bin/python'
subprocess.run([python,'-I','-m','pip','install','--no-index','--find-links='+str(root/'wheels'),
    '--require-hashes','--only-binary=:all:','--no-deps','--no-cache-dir',
    '-r',str(root/'install.txt')],env=env,check=True)
env.update({'STAGING_EXPECTED_PINS':json.dumps(contract['pins']),
    'STAGING_EXPECTED_EXTRAS':json.dumps(contract['extras']),
    'AGENT_INDEX_HOME':str(scratch/'index'),'AGENT_INDEX_DEVICE':'cpu',
    'AGENT_INDEX_ENGINE_MODE':'external','AGENT_INDEX_SEARCH_IN_PROCESS':'0'})
subprocess.run([python,'-I','-B','-c',(root/'native-probe.py').read_text()],env=env,check=True)
state=Path('/var/lib/agent-index')
state.mkdir(parents=True)
os.chown(state,contract['uid'],contract['gid'])
'''


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _files(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ContainerError("prepared context must not contain symlinks/reparse points")
        if stat.S_ISDIR(info.st_mode):
            if path.relative_to(root).as_posix() != "wheels":
                raise ContainerError("prepared context contains an unlisted directory")
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(256 * 1024), b""):
                    digest.update(block)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
        else:
            raise ContainerError("prepared context contains a non-regular entry")
    return result


def _cleanup_preparation(output: Path, identity, primary: BaseException) -> None:
    try:
        if identity is None:
            raise ContainerError("output ownership was not captured")
        _path(output)
        try:
            actual = output.stat()
        except FileNotFoundError:
            return  # Output is already absent, not an excuse to remove another path.
        if (actual.st_dev, actual.st_ino) != (identity.st_dev, identity.st_ino):
            raise ContainerError("output filesystem identity changed")
        # Check the tree before recursive deletion, not merely its root.
        _files(output)
        shutil.rmtree(output)
    except (OSError, ValueError) as cleanup:
        error = ContextCleanupError(
            f"preparation failed ({type(primary).__name__}); "
            f"owned output cleanup unconfirmed ({type(cleanup).__name__}); output preserved"
        )
        error.cleanup_reason = str(cleanup)
        raise error from primary


def _context_name(value: object) -> str:
    if not isinstance(value, str) or not (
        value in {"Dockerfile", ".dockerignore", "release.json", "install.txt",
                  "verify-and-install.py", "native-probe.py"}
        or re.fullmatch(r"wheels/[A-Za-z0-9][A-Za-z0-9_.+!-]*\.whl", value)
    ):
        raise ContainerError("context manifest contains an unsafe/unapproved file path")
    return value


class _SnapshotReader:
    def __init__(self, stream, expected: str):
        self.stream = stream
        self.expected = expected
        self.digest = hashlib.sha256()

    def read(self, count: int) -> bytes:
        data = self.stream.read(count)
        self.digest.update(data)
        return data


@contextmanager
def verified_context_snapshot(context: Path, *, expected_contract_sha256: str):
    """Capture exact digest-pinned bytes before any builder is invoked.

    A private bounded temporary tar, not the mutable original directory, becomes
    Docker's stdin. Snapshot integrity is not source/release authenticity.
    Limits: 1 MiB manifest, 4096 files, 512 MiB per file, 1 GiB aggregate tar.
    """
    if not isinstance(expected_contract_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", expected_contract_sha256,
    ) is None:
        raise ContainerError("expected context digest must be 64 lowercase hexadecimal characters")
    context = _path(context)
    manifest = _bytes(_path(context / "context.json", file=True))
    if _digest(manifest) != expected_contract_sha256:
        raise ContainerError("context contract digest does not match independent pin")
    try:
        contract = json.loads(manifest, object_pairs_hook=_unique)
    except ValueError as exc:
        raise ContainerError("context contract JSON is malformed") from exc
    keys = {"schema", "schema_version", "source_commit", "service_version", "base_image",
            "target", "architecture", "uid", "gid", "pins", "extras", "files"}
    if (
        not isinstance(contract, dict) or set(contract) != keys
        or contract["schema"] != _SCHEMA
        or type(contract["schema_version"]) is not int or contract["schema_version"] != 1
        or not isinstance(contract["files"], list)
        or not 6 <= len(contract["files"]) <= _CONTEXT_FILE_LIMIT
    ):
        raise ContainerError("unsupported context manifest shape")
    expected = {"context.json": expected_contract_sha256}
    for item in contract["files"]:
        if not isinstance(item, dict) or set(item) != {"file", "sha256"}:
            raise ContainerError("malformed context artifact")
        name = _context_name(item["file"])
        if name in expected or not isinstance(item["sha256"], str) or re.fullmatch(
            r"[0-9a-f]{64}", item["sha256"],
        ) is None:
            raise ContainerError("duplicate context file or malformed digest")
        expected[name] = item["sha256"]
    if not {"Dockerfile", ".dockerignore", "release.json", "install.txt",
            "verify-and-install.py", "native-probe.py"} <= expected.keys():
        raise ContainerError("context omits recipe/ignore/install artifacts")
    sizes = {}
    for path in context.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ContainerError("context contains a symlink/reparse point")
        name = path.relative_to(context).as_posix()
        if stat.S_ISDIR(info.st_mode):
            if name != "wheels":
                raise ContainerError("context contains an unlisted directory")
            continue
        if not stat.S_ISREG(info.st_mode) or name not in expected:
            raise ContainerError("context file inventory mismatch")
        sizes[name] = info.st_size
        if len(sizes) > _CONTEXT_FILE_LIMIT:
            raise ContainerError("context exceeds file count limit")
    if set(sizes) != set(expected):
        raise ContainerError("context file inventory mismatch")
    if max(sizes.values()) > _FILE_LIMIT or sum(sizes.values()) > _CONTEXT_LIMIT:
        raise ContainerError("context exceeds snapshot byte limits")
    with tempfile.TemporaryFile(mode="w+b") as snapshot:
        with tarfile.open(fileobj=snapshot, mode="w") as archive:
            for name in sorted(expected):
                path = _path(context / name, file=True)
                before = path.stat()
                info = tarfile.TarInfo(name)
                info.size, info.mode = before.st_size, 0o644
                info.mtime, info.uid, info.gid = 0, 0, 0
                info.uname, info.gname = "", ""
                if info.size != sizes[name]:
                    raise ContainerError("context changed before capture")
                with path.open("rb") as stream:
                    reader = _SnapshotReader(stream, expected[name])
                    archive.addfile(info, reader)
                    if reader.digest.hexdigest() != expected[name]:
                        raise ContainerError("context artifact digest mismatch while capturing")
                after = path.stat()
                _path(path, file=True)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                ):
                    raise ContainerError("context filesystem identity changed while capturing")
                if snapshot.tell() > _CONTEXT_LIMIT:
                    raise ContainerError("context tar exceeds snapshot limit")
        if snapshot.tell() > _CONTEXT_LIMIT:
            raise ContainerError("context tar exceeds snapshot limit")
        snapshot.seek(0)
        yield contract, snapshot


def build_container_context(
    context: Path, *, expected_contract_sha256: str, docker: Path | None = None,
    docker_context: str = "default", docker_config: Path | None = None,
    timeout_seconds: float = 600.0,
) -> dict:
    """Use only a preverified bounded snapshot, never Docker over a mutable path.

    No arbitrary Docker flags/URLs/headers. Cached approved base must exist.
    Client process containment cannot prove daemon cancellation; any build
    failure or interruption reports an uncertain daemon outcome, never success.
    Explicit Docker config is client-only and never part of the tar or image.
    """
    if (
        isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        or not isinstance(docker_context, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", docker_context) is None
    ):
        raise ContainerError("builder requires finite positive timeout and a named Docker context")
    with verified_context_snapshot(context, expected_contract_sha256=expected_contract_sha256) as (
        contract, snapshot,
    ):
        executable = docker or shutil.which("docker")
        if executable is None:
            raise ContainerError("Docker executable unavailable; supply --docker")
        executable = _path(Path(executable), file=True)
        base = _base(contract["base_image"])
        target = ContainerTarget(**contract["target"])
        architecture = target.environment()["platform_machine"]
        prefix = [str(executable), "--context", docker_context]
        with tempfile.TemporaryDirectory(prefix="index-container-build-") as temporary:
            scratch = Path(temporary)
            env = _environment(scratch, tool_paths=(executable.parent,))
            env["DOCKER_CONFIG"] = str(
                _path(docker_config) if docker_config is not None else scratch / "docker-config"
            )
            deadline = time.monotonic() + timeout_seconds
            try:
                cached = json.loads(_run(
                    [*prefix, "image", "inspect", "--format",
                     '{"os":"{{.Os}}","architecture":"{{.Architecture}}"}', base],
                    cwd=scratch, env=env, deadline=deadline,
                    operation_label="cached image inspection",
                ))
            except (OSError, ValueError, RuntimeError) as exc:
                raise ContainerError("approved base unavailable in selected Docker cache") from exc
            if cached != {"os": "linux", "architecture": {
                "x86_64": "amd64", "aarch64": "arm64",
            }[architecture]}:
                raise ContainerError("cached base does not match Linux target architecture")
            try:
                iid = scratch / "image.id"
                _run([*prefix, "build", "--pull=false", "--network=none",
                      "--platform", target.platform, "--build-arg",
                      "CONTEXT_SHA256=" + expected_contract_sha256,
                      "--iidfile", str(iid), "-"],
                     cwd=scratch, env=env, deadline=deadline, input_stream=snapshot,
                     operation_label="container image build")
                image_id = _bytes(iid).decode("ascii").strip()
                if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
                    raise ContainerError("Docker did not return a valid immutable image identity")
            except BaseException as exc:
                raise ContainerError(
                    "Docker client did not confirm build completion; daemon outcome uncertain"
                ) from exc
    return {"schema": _SCHEMA, "schema_version": 1, "state": "built",
            "contract_sha256": expected_contract_sha256,
            "source_commit": contract["source_commit"], "service_version": contract["service_version"],
            "image_id": image_id}


def prepare_container_context(
    descriptor_path: Path, *, expected_source_commit: str, third_party_lock: Path,
    third_party_wheels: Path, output: Path, base_image: str, target: ContainerTarget,
    uid: int = 10001, gid: int = 10001,
) -> dict:
    """Prepare an exclusive immutable build snapshot; replay only identical content.

    No Docker, package installation, network, credential/config or host-state
    copying occurs. The resulting multi-stage Dockerfile installs from wheels
    offline, validates native closure and executes the loopback service non-root.
    """
    created = False
    identity = None
    try:
        base_image = _base(base_image)
        environment = target.environment()
        if any(type(value) is not int or not 1 <= value <= 2147483647 for value in (uid, gid)):
            raise ContainerError("uid and gid must be positive non-root integers")
        descriptor_path = _path(descriptor_path, file=True)
        lock_path = _path(third_party_lock, file=True)
        third_party_wheels = _path(third_party_wheels)
        output = _path(output)
        if output == Path(output.anchor) or any(
            output == path or output in path.parents or path in output.parents
            for path in (descriptor_path.parent, third_party_wheels, lock_path)
        ):
            raise ContainerError("output must be a dedicated directory disjoint from input artifacts")
        descriptor = verify_descriptor(
            descriptor_path, expected_source_commit=expected_source_commit,
        )
        locked = hashed_requirements(_bytes(lock_path))
        if set(locked) & {"agent-index-engine", "torch", "sentence-transformers"}:
            raise ContainerError("engine/model packages are outside the standalone service image")
        metadata, sources = {}, {}
        for artifact in descriptor["artifacts"]:
            name = artifact["distribution"]
            source = descriptor_path.parent / artifact["file"]
            item = inspect_wheel(source, distributions=frozenset({name}))
            if item["artifact"] != artifact:
                raise ContainerError("first-party artifact changed after release verification")
            metadata[name], sources[name] = item, source
        for source in sorted(third_party_wheels.iterdir()):
            if source.suffix.lower() != ".whl":
                continue  # Deliberately never read/copy sibling config, credentials or files.
            item = inspect_wheel(source, distributions=frozenset(locked), native=True)
            artifact = item["artifact"]
            name = artifact["distribution"]
            if name in metadata or artifact["version"] != locked[name]["version"]:
                raise ContainerError("duplicate/conflicting third-party wheel")
            if artifact["sha256"] not in locked[name]["sha256"]:
                raise ContainerError("third-party wheel does not match approved lock hashes")
            metadata[name], sources[name] = item, source
        if set(metadata) != set(locked) | {item["distribution"] for item in descriptor["artifacts"]}:
            raise ContainerError("native third-party wheel closure is incomplete")
        extras = {name: item["extras"] for name, item in locked.items()}
        extras.update({"agent-index-service": ["native"], "agent-index": ["store", "server"]})
        _closure(metadata, extras, environment, target)
        pins = {name: item["artifact"]["version"] for name, item in metadata.items()}
        install = "".join(
            name + ("[" + ",".join(extras[name]) + "]" if extras.get(name) else "")
            + "==" + pins[name] + " --hash=sha256:" + metadata[name]["artifact"]["sha256"] + "\n"
            for name in sorted(metadata)
        )
        generated = {
            "release.json": _json(descriptor), "install.txt": install.encode(),
            "verify-and-install.py": _BUILD.encode(), "native-probe.py": _PROBE.encode(),
        }
        dockerfile = f"""FROM --platform={target.platform} {base_image} AS build
USER 0
ARG CONTEXT_SHA256
COPY wheels/ /build/wheels/
COPY Dockerfile .dockerignore release.json install.txt context.json verify-and-install.py native-probe.py /build/
RUN --network=none ["python", "-I", "-B", "-S", "/build/verify-and-install.py"]
FROM --platform={target.platform} {base_image} AS runtime
COPY --from=build /opt/agent-index/ /opt/agent-index/
COPY --from=build --chown={uid}:{gid} /var/lib/agent-index/ /var/lib/agent-index/
USER {uid}:{gid}
ENV HOME=/var/lib/agent-index PYTHONDONTWRITEBYTECODE=1
WORKDIR /var/lib/agent-index
VOLUME ["/var/lib/agent-index"]
ENTRYPOINT ["/opt/agent-index/bin/python", "-I", "-m", "agent_index_service", "serve", "--config", "/etc/agent-index/host.yaml"]
"""
        generated["Dockerfile"] = dockerfile.encode()
        generated[".dockerignore"] = (
            "*\n!Dockerfile\n!.dockerignore\n!context.json\n!release.json\n!install.txt\n"
            "!verify-and-install.py\n!native-probe.py\n!wheels/\n!wheels/*.whl\n"
        ).encode()
        hashes = {name: _digest(value) for name, value in generated.items()}
        hashes.update({f"wheels/{item['artifact']['file']}": item["artifact"]["sha256"]
                       for item in metadata.values()})
        contract = {
            "schema": _SCHEMA, "schema_version": 1,
            "source_commit": descriptor["source_commit"], "service_version": pins["agent-index-service"],
            "base_image": base_image, "target": {
                "python_version": environment["python_full_version"], "platform": target.platform,
                "glibc_version": target.glibc_version,
            },
            "architecture": environment["platform_machine"], "uid": uid, "gid": gid,
            "pins": pins, "extras": extras,
            "files": [{"file": name, "sha256": hashes[name]} for name in sorted(hashes)],
        }
        contract_bytes = _json(contract)
        generated["context.json"] = contract_bytes
        if (
            len(contract_bytes) > 1024 * 1024 or len(hashes) + 1 > _CONTEXT_FILE_LIMIT
            or sum(source.stat().st_size for source in sources.values())
            + sum(len(value) for value in generated.values())
            + (len(hashes) + 1) * 2048 + 10240 > _CONTEXT_LIMIT
        ):
            raise ContainerError("prepared context exceeds manifest/aggregate snapshot limits")
        expected = {**hashes, **{name: _digest(value) for name, value in generated.items()}}
        state = "prepared"
        if output.exists():
            if _files(output) != expected:
                raise ContainerError("prepared output conflicts; unrelated/existing content preserved")
            state = "already_prepared"
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            _path(output.parent)
            output.mkdir()
            created = True
            identity = output.stat()
            (output / "wheels").mkdir()
            for name, source in sources.items():
                artifact = metadata[name]["artifact"]
                destination = output / "wheels" / artifact["file"]
                _copy(source, destination, artifact["sha256"])
                if inspect_wheel(
                    destination, distributions=frozenset({name}), native=name in locked,
                ) != metadata[name]:
                    raise ContainerError("wheel snapshot changed during preparation")
            for name, value in generated.items():
                if name != "context.json":
                    (output / name).write_bytes(value)
            (output / "context.json").write_bytes(contract_bytes)  # Completion published last.
            if _files(output) != expected:
                raise ContainerError("prepared context integrity check failed")
        return {
            "schema": _SCHEMA, "schema_version": 1, "state": state,
            "context": str(output), "contract_sha256": _digest(contract_bytes),
            "source_commit": descriptor["source_commit"], "service_version": pins["agent-index-service"],
            "build_argv": ["agent-index-service", "container", "build", "--context", str(output),
                           "--expected-contract-sha256", _digest(contract_bytes)],
        }
    except BaseException as exc:
        if created:
            _cleanup_preparation(output, identity, exc)
        if isinstance(exc, (OSError, ValueError)):
            raise ContainerError(str(exc)) from exc
        raise
