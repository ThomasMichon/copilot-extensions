"""First-touch native candidate staging, never activation or migration.

Uses the unchanged build-delivered versioned-runtime primitive. Inputs are
operator-owned: a pinned release descriptor, a platform-specific fully hashed
third-party lock and explicit uv TOML configuration. Receipts bind integrity,
not approval/signatures or store/queue rollback safety. Venvs are built at their
final paths; preexisting incomplete or conflicting slots are never repaired.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from .config import load_config
from .release import verify_descriptor

_LIMIT = 1024 * 1024
_BUNDLED = {
    "agent-index-service", "agent-index", "agent-zdd", "agent-procutil", "agent-dropin-registry",
}
_RECEIPT = "candidate.json"
_OWNER = ".stage-owner.json"
_SCHEMA = "agent-index-service.candidate"


class CandidateError(ValueError):
    """Invalid inputs, unsafe slot mutation or failed candidate construction."""


class _CleanupUnconfirmed(CandidateError):
    """Preserve the owned slot when descendant termination cannot be proven."""


@dataclass(frozen=True)
class NativeBuildConfig:
    python: Path
    uv: Path
    third_party_lock: Path
    uv_config: Path
    timeout_seconds: float = 600.0

    def __post_init__(self) -> None:
        value = self.timeout_seconds
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0
        ):
            raise CandidateError("timeout_seconds must be finite and positive")


def _primitive():
    try:
        return importlib.import_module("agent_index_service._versioned_runtime")
    except ImportError as exc:
        raise CandidateError(
            "packaged versioned-runtime unavailable; build/install a wheel"
        ) from exc


@contextmanager
def _lease(root: Path):
    from zdd import CutoverLock, CutoverLockedError

    try:
        with CutoverLock(root):
            yield
    except CutoverLockedError as exc:
        raise CandidateError("candidate staging lease is held by another process") from exc


def _path(value: Path, *, file: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise CandidateError("paths must be absolute and must not contain parent traversal")
    for ancestor in (*reversed(path.parents), path):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise CandidateError("symlink/reparse ancestors are not allowed")
        if ancestor != path and not stat.S_ISDIR(info.st_mode):
            raise CandidateError("path ancestor must be a directory")
    if file and not path.is_file():
        raise CandidateError("required input must be an existing regular file")
    if not file and path.exists() and not path.is_dir():
        raise CandidateError("install root must be a directory")
    return path


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _host_root(install_root: Path, host_config_path: Path) -> Path:
    root = _path(install_root)
    if root == Path(root.anchor):
        raise CandidateError("install root must be a dedicated directory, not a filesystem root")
    host_path = _path(host_config_path, file=True)
    host = load_config(host_path)
    for selected in (host.home, host.data, host.routing):
        _path(selected)
    if any(_overlap(root, selected) for selected in (
        host_path, host.home, host.data, host.routing,
    )):
        raise CandidateError("install root must not overlap host configuration/state/routing")
    return root


def _bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(_LIMIT + 1)
    if len(value) > _LIMIT:
        raise CandidateError("policy/receipt exceeds the 1 MiB limit")
    return value


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    _path(path, file=True)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino, after.st_size, after.st_mtime_ns,
    ):
        raise CandidateError("build tool changed while reading")
    return digest.hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CandidateError("duplicate receipt key")
        value[key] = item
    return value


def _read_json(path: Path):
    _path(path, file=True)
    return json.loads(_bytes(path), object_pairs_hook=_unique)


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _lock_requirements(data: bytes) -> dict[str, str]:
    """Accept a resolved per-platform pip-style lock, no includes/options/markers."""
    text = data.decode("utf-8").replace("\\\r\n", " ").replace("\\\n", " ")
    pins = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+--hash=", line)
        if len(parts) < 2 or any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", part.strip()) is None for part in parts[1:]
        ):
            raise CandidateError("third-party lock requires sha256 hashes for every pin")
        try:
            requirement = Requirement(parts[0].strip())
        except InvalidRequirement as exc:
            raise CandidateError("invalid third-party requirement") from exc
        specs = list(requirement.specifier)
        name = canonicalize_name(requirement.name)
        if (
            requirement.url or requirement.marker is not None or len(specs) != 1
            or specs[0].operator != "==" or "*" in specs[0].version
            or name in _BUNDLED or name in pins
        ):
            raise CandidateError("lock must use unique exact third-party pins, no URLs or markers")
        pins[name] = str(Version(specs[0].version))
    if not pins:
        raise CandidateError("third-party lock must not be empty")
    return pins


def _uv_policy(data: bytes) -> None:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError as exc:
            raise CandidateError("uv policy validation requires Python 3.11+ or tomli") from exc
    try:
        policy = tomllib.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise CandidateError("invalid explicit uv TOML configuration") from exc
    if not policy:
        raise CandidateError("explicit uv configuration must not be empty")


def _environment(scratch: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("AGENT_", "COPILOT_", "UV_", "PIP_", "PYTHON"))
        and key.upper() not in {"VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}
    }
    env.update({
        "HOME": str(scratch), "USERPROFILE": str(scratch),
        "XDG_CONFIG_HOME": str(scratch / "config"), "XDG_CACHE_HOME": str(scratch / "cache"),
        "APPDATA": str(scratch / "config"), "LOCALAPPDATA": str(scratch / "cache"),
        "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1", "UV_PYTHON_DOWNLOADS": "never",
        "AGENT_INDEX_HOME": str(scratch / "index"),
        "AGENT_INDEX_DATA_DIR": str(scratch / "index" / "data"),
        "AGENT_INDEX_ROUTING_DIR": str(scratch / "index" / "routing"),
        "AGENT_INDEX_CONFIG": str(scratch / "absent-config.yaml"),
        "AGENT_INDEX_DEVICE": "cpu", "AGENT_INDEX_ENGINE_MODE": "external",
        "AGENT_INDEX_SEARCH_IN_PROCESS": "0",
    })
    if os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    return env


_CONTAINED_SUPERVISOR = """
import ctypes, os, signal, subprocess, sys, time
from pathlib import Path
# A subreaper retains double-forked/orphaned descendants without a new group.
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
    raise SystemExit(125)
def interrupted(signum, frame):
    raise TimeoutError()
signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
def descendants():
    table = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / 'stat').read_text()
            fields = text[text.rfind(')') + 2:].split()
            table[int(entry.name)] = (int(fields[1]), fields[19], fields[0])
        except (OSError, ValueError, IndexError):
            continue
    owned, parents = {}, {os.getpid()}
    while True:
        added = {pid for pid, value in table.items()
                 if value[0] in parents and pid not in parents}
        if not added:
            return {pid: table[pid] for pid in owned}
        owned.update({pid: table[pid] for pid in added})
        parents.update(added)
code = 125
try:
    child = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL)
    try:
        code = child.wait(timeout=float(sys.argv[1]))
    except (subprocess.TimeoutExpired, TimeoutError):
        code = 124
finally:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    until = time.monotonic() + 10
    while True:
        owned = descendants()
        for pid, identity in owned.items():
            if identity[2] != 'Z' and descendants().get(pid) == identity:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break
        if not descendants():
            break
        if time.monotonic() >= until:
            code = 125
            break
        time.sleep(.02)
raise SystemExit(code if 0 <= code <= 255 else 1)
"""


def _operation(argv: list[str]) -> str:
    if "venv" in argv:
        return "venv creation"
    if "pip" in argv and "install" in argv:
        return "dependency installation"
    if "-c" in argv:
        return "isolated runtime probe"
    return "build operation"


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], deadline: float) -> str:
    from agent_procutil import (
        contained_test_mode,
        no_window_kwargs,
        spawn_sync_in_kill_on_close_job,
    )

    operation = _operation(argv)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CandidateError(f"{operation}: timeout before spawn")
    kwargs = no_window_kwargs()
    contained = contained_test_mode()  # Parent authority, not a caller-supplied child env.
    private_group = os.name != "nt" and not contained
    supervised = os.name != "nt" and contained
    if supervised and (sys.platform != "linux" or not Path("/proc/self/stat").is_file()):
        raise CandidateError(f"{operation}: contained descendant supervision unavailable")
    if private_group:
        kwargs["start_new_session"] = True
    command = ([sys.executable, "-I", "-B", "-c", _CONTAINED_SUPERVISOR,
                str(remaining), *argv] if supervised else argv)
    # Output is bounded on disk/read and never relayed as an error: uv may print credentials.
    output = cwd / f".build-output-{uuid.uuid4().hex}"
    process = job = None
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            process, job = spawn_sync_in_kill_on_close_job(
                command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.DEVNULL, **kwargs,
            )
            if os.name == "nt" and job is None:
                raise CandidateError(f"{operation}: Windows Job containment unavailable")
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise CandidateError(f"{operation}: timeout")
                if output.stat().st_size > _LIMIT:
                    raise CandidateError(f"{operation}: output limit exceeded")
                time.sleep(0.05)
            code = process.returncode
            if code:
                if supervised and code == 125:
                    raise _CleanupUnconfirmed(f"{operation}: descendant supervision failure")
                classification = (
                    " (timeout)" if supervised and code == 124 else
                    " (descendant supervision failure)" if supervised and code == 125 else ""
                )
                raise CandidateError(f"{operation}: exit status {code}{classification}")
        return _bytes(output).decode("utf-8")
    finally:
        if job is not None:
            job.close()
        elif process is not None and private_group:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process is not None and supervised and process.poll() is None:
            process.terminate()  # Supervisor handles its own descendants; never kill outer group.
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                # Do not clear candidate files with unconfirmed descendant cleanup.
                raise _CleanupUnconfirmed(
                    f"{operation}: descendant cleanup could not be confirmed"
                ) from exc
        if process is not None and process.poll() is None:
            process.kill()
        if process is not None:
            process.wait(timeout=15)
        cleanup_deadline = time.monotonic() + 5
        while True:
            try:
                output.unlink(missing_ok=True)
                break
            except PermissionError:
                if time.monotonic() >= cleanup_deadline:
                    raise _CleanupUnconfirmed(
                        f"{operation}: output handles remained open after cleanup"
                    ) from None
                time.sleep(0.05)


_PROBE = """
import importlib, json, os
from importlib.metadata import distributions
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
installed = {canonicalize_name(d.metadata['Name']): d for d in distributions()}
pins = json.loads(os.environ['STAGING_EXPECTED_PINS'])
extra_pins = json.loads(os.environ['STAGING_EXPECTED_EXTRAS'])
for name, version in pins.items():
    assert name in installed and Version(installed[name].version) == Version(version), name
for name, dist in installed.items():
    extras = ['', *extra_pins.get(name, [])]
    if name == 'agent-index-service': extras += ['native']
    if name == 'agent-index': extras += ['store', 'server']
    for raw in dist.requires or []:
        req = Requirement(raw)
        if req.marker is not None and not any(req.marker.evaluate({'extra': e}) for e in extras):
            continue
        target = canonicalize_name(req.name)
        assert req.url is None and target in installed, target
        assert req.specifier.contains(installed[target].version, prereleases=True), target
        provided = {canonicalize_name(e) for e in
                    installed[target].metadata.get_all('Provides-Extra', [])}
        assert {canonicalize_name(e) for e in req.extras} <= provided, target
for module in ('fastapi','uvicorn','pydantic','numpy','pyarrow','lancedb','tree_sitter',
               'agent_index_service','agent_index.config','agent_index.indexing.task_store',
               'agent_index.indexing.runner','zdd','agent_procutil','dropin_registry',
               'agent_index_service._versioned_runtime'):
    importlib.import_module(module)
from agent_index.config import SOURCE_MODE_ENV
assert SOURCE_MODE_ENV == 'AGENT_INDEX_SOURCE_MODE'
import agent_index_service
assert Version(agent_index_service.__version__) == Version(pins['agent-index-service'])
print(json.dumps({'validated': True, 'versions': {n: installed[n].version for n in pins}}))
"""


def _copy(source: Path, target: Path, expected: str) -> None:
    _path(source, file=True)
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        for block in iter(lambda: incoming.read(256 * 1024), b""):
            digest.update(block)
            outgoing.write(block)
    after = source.stat()
    if (
        digest.hexdigest() != expected
        or (before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise CandidateError("release source changed while snapshotting")
    _path(source, file=True)


def _receipt(slot: Path, primitive, root: Path, version: str) -> dict | None:
    value = _read_json(slot / _RECEIPT)
    keys = {
        "schema", "schema_version", "version", "source_commit", "identity",
        "descriptor_sha256", "lock_sha256", "build_policy_sha256",
    }
    if (
        not isinstance(value, dict) or set(value) != keys or value["schema"] != _SCHEMA
        or type(value["schema_version"]) is not int or value["schema_version"] != 1
        or value["version"] != version
        or re.fullmatch(r"[0-9a-f]{40}", str(value["source_commit"])) is None
        or any(re.fullmatch(r"[0-9a-f]{64}", str(value[key])) is None for key in (
            "identity", "descriptor_sha256", "lock_sha256", "build_policy_sha256",
        ))
        or not primitive.is_complete(root, version, expect_hash=value["identity"])
        or primitive.slot_python(root, version) is None
    ):
        return None
    identity_input = {key: item for key, item in value.items() if key != "identity"}
    if _hash(_json(identity_input)) != value["identity"]:
        return None
    descriptor = verify_descriptor(
        slot / "artifacts" / "release.json", expected_source_commit=value["source_commit"],
    )
    if (
        _hash(_json(descriptor)) != value["descriptor_sha256"]
        or _hash(_bytes(slot / "artifacts" / "third-party.txt")) != value["lock_sha256"]
    ):
        return None
    return value


def _result(root: Path, version: str, primitive, state: str, receipt: dict) -> dict:
    return {
        "schema": _SCHEMA, "schema_version": 1, "state": state,
        "version": version, "source_commit": receipt["source_commit"],
        "identity": receipt["identity"], "slot": str(primitive.version_dir(root, version)),
        "python": str(primitive.slot_python(root, version)),
    }


def stage_candidate(
    descriptor_path: Path, *, expected_source_commit: str, install_root: Path,
    host_config_path: Path, build: NativeBuildConfig,
) -> dict:
    """Build a new final-path runtime; never activate or repair an existing slot."""
    try:
        root = _host_root(install_root, host_config_path)
        inputs = [_path(path, file=True) for path in (
            descriptor_path, build.python, build.uv, build.third_party_lock, build.uv_config,
        )]
        descriptor_path, python, uv, lock_path, config_path = inputs
        if any(_overlap(root, path) for path in (*inputs, descriptor_path.parent)):
            raise CandidateError("install root overlaps bundle/tool/build-policy inputs")
        descriptor = verify_descriptor(
            descriptor_path, expected_source_commit=expected_source_commit,
        )
        lock_bytes, config_bytes = _bytes(lock_path), _bytes(config_path)
        pins = _lock_requirements(lock_bytes)
        _uv_policy(config_bytes)
        policy = {
            "python": str(python), "uv": str(uv), "uv_config_sha256": _hash(config_bytes),
            "python_sha256": _file_hash(python), "uv_sha256": _file_hash(uv),
            "native_extras": ["native", "server", "store"],
        }
        receipt = {
            "schema": _SCHEMA, "schema_version": 1, "version": descriptor["service_version"],
            "source_commit": descriptor["source_commit"],
            "descriptor_sha256": _hash(_json(descriptor)), "lock_sha256": _hash(lock_bytes),
            "build_policy_sha256": _hash(_json(policy)),
        }
        receipt["identity"] = _hash(_json(receipt))
        primitive = _primitive()
        version = descriptor["service_version"]
        deadline = time.monotonic() + build.timeout_seconds
        root.mkdir(parents=True, exist_ok=True)
        _path(root)
        with _lease(root):
            _path(root)
            slot = primitive.version_dir(root, version)
            _path(slot)
            if slot.exists():
                marker = primitive.read_marker(root, version)
                if marker is None:
                    raise CandidateError("preexisting incomplete candidate preserved")
                existing = _receipt(slot, primitive, root, version)
                if existing != receipt:
                    raise CandidateError("preexisting candidate identity/completeness conflict")
                return _result(root, version, primitive, "already_staged", receipt)
            slot.parent.mkdir(parents=True, exist_ok=True)
            _path(slot.parent)
            slot.mkdir()  # Exclusive reservation at the venv's permanent location.
            owner = {
                "token": uuid.uuid4().hex, "pid": os.getpid(), "identity": receipt["identity"],
            }
            try:
                _write(slot / _OWNER, owner)
            except BaseException:
                slot.rmdir()  # Only remove the empty, newly reserved directory.
                raise
            try:
                if primitive.slot(root, version, clean_incomplete=False) != slot:
                    raise CandidateError("canonical primitive returned an unexpected slot")
                artifacts = slot / "artifacts"
                artifacts.mkdir()
                for artifact in descriptor["artifacts"]:
                    _copy(descriptor_path.parent / artifact["file"],
                          artifacts / artifact["file"], artifact["sha256"])
                snapshot = artifacts / "release.json"
                _write(snapshot, descriptor)
                if verify_descriptor(
                    snapshot, expected_source_commit=expected_source_commit,
                ) != descriptor:
                    raise CandidateError("snapshotted release differs from verified input")
                (artifacts / "third-party.txt").write_bytes(lock_bytes)
                scratch = slot / "build-state"
                scratch.mkdir()
                env = _environment(scratch)
                if time.monotonic() >= deadline:
                    raise CandidateError("candidate build timeout")
                base = [str(uv), "--config-file", str(config_path),
                        "--cache-dir", str(scratch / "cache")]
                if _hash(_bytes(config_path)) != policy["uv_config_sha256"]:
                    raise CandidateError("uv policy changed before build")
                if (
                    _file_hash(python) != policy["python_sha256"]
                    or _file_hash(uv) != policy["uv_sha256"]
                ):
                    raise CandidateError("build tool changed before execution")
                # This exclusive new reservation contains receipts/artifacts, not a prior venv.
                _run([*base, "venv", "--python", str(python), "--no-python-downloads",
                      "--allow-existing", str(slot)],
                     cwd=slot, env=env, deadline=deadline)
                interpreter = primitive.slot_python(root, version)
                if interpreter is None:
                    raise CandidateError("venv did not create a slot interpreter")
                pip = [*base, "pip", "install", "--python", str(interpreter), "--no-deps",
                       "--only-binary", ":all:"]
                _run([*pip, "--require-hashes", "-r", str(artifacts / "third-party.txt")],
                     cwd=slot, env=env, deadline=deadline)
                wheels = [str(artifacts / item["file"]) + (
                    "[native]" if item["distribution"] == "agent-index-service" else ""
                ) for item in descriptor["artifacts"]]
                _run([*pip, "--no-index", *wheels], cwd=slot, env=env, deadline=deadline)
                pins.update({item["distribution"]: item["version"]
                             for item in descriptor["artifacts"]})
                env["STAGING_EXPECTED_PINS"] = json.dumps(pins)
                extras = {}
                lock_text = lock_bytes.decode().replace("\\\r\n", " ").replace("\\\n", " ")
                for line in lock_text.splitlines():
                    if line.strip() and not line.strip().startswith("#"):
                        req = Requirement(re.split(r"\s+--hash=", line.strip())[0])
                        extras[canonicalize_name(req.name)] = sorted(req.extras)
                env["STAGING_EXPECTED_EXTRAS"] = json.dumps(extras)
                output = _run([str(interpreter), "-I", "-B", "-c", _PROBE],
                              cwd=slot, env=env, deadline=deadline)
                probe = json.loads(output, object_pairs_hook=_unique)
                if (
                    not isinstance(probe, dict) or probe.get("validated") is not True
                    or not isinstance(probe.get("versions"), dict)
                    or set(probe["versions"]) != set(pins)
                    or any(Version(probe["versions"][key]) != Version(value)
                           for key, value in pins.items())
                ):
                    raise CandidateError("isolated native/dependency/version probe failed")
                if _hash(_bytes(config_path)) != policy["uv_config_sha256"]:
                    raise CandidateError("uv policy changed during build")
                if time.monotonic() >= deadline:
                    raise CandidateError("candidate build timeout before completion")
                _write(slot / _RECEIPT, receipt)
                primitive.mark_complete(root, version, payload_hash=receipt["identity"])
                return _result(root, version, primitive, "staged", receipt)
            except BaseException as exc:
                # A failed call may remove only its own freshly reserved incomplete slot.
                _path(slot)
                if (
                    not isinstance(exc, _CleanupUnconfirmed)
                    and primitive.read_marker(root, version) is None
                    and _read_json(slot / _OWNER) == owner
                ):
                    shutil.rmtree(slot)
                raise
    except CandidateError:
        raise
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        raise CandidateError(f"candidate staging failed ({type(exc).__name__})") from exc


def inspect_candidates(install_root: Path, *, host_config_path: Path) -> dict:
    """Read candidate receipts/completeness without selecting a runtime or creating files."""
    try:
        root = _host_root(install_root, host_config_path)
        candidates = []
        if root.exists():
            primitive = _primitive()
            versions = root / "versions"
            _path(versions)
            if versions.exists():
                for slot in sorted(versions.iterdir()):
                    _path(slot)
                    version = slot.name
                    try:
                        if not (slot / _RECEIPT).exists():
                            value, state = None, "incomplete"
                        else:
                            value = _receipt(slot, primitive, root, version)
                            state = "staged" if value else "invalid"
                    except FileNotFoundError:
                        value, state = None, "incomplete"
                    except (CandidateError, ValueError):
                        value, state = None, "invalid"
                    candidates.append({
                        "version": version, "state": state, "slot": str(slot),
                        "identity": value["identity"] if value else None,
                        "source_commit": value["source_commit"] if value else None,
                    })
        return {"schema": _SCHEMA, "schema_version": 1,
                "install_root": str(root), "candidates": candidates}
    except CandidateError:
        raise
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        raise CandidateError(f"candidate inspection failed ({type(exc).__name__})") from exc
