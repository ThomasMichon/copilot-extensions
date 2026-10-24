"""Provider-owned, one-incarnation credential delivery for restricted workers."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from agent_procutil import no_window_flags
from session_liveness_probe import SessionLiveness

from .config import (
    DISPATCH_BINDING_LABEL, DISPATCH_ONLY_LABEL, FLEET_LABEL,
    RESTRICTED_PROFILE, SECURITY_PROFILE_LABEL, STATE_DIR,
)
from .private_state import atomic_write_json, write_json_exclusive
from .restricted_exec import resolve_executable, sanitized_exec_prefix

TOKEN_FILE = "/run/agent-dispatch/task-token"
WORKER_LOCK = "/run/agent-dispatch/worker.lock"
PUBLICATION_LOCK = "/run/agent-dispatch/publication.lock"
WORKER_ADMISSION = "/run/agent-dispatch/worker-admission"
_STATE = STATE_DIR / "dispatch-bindings"
_FACTS = {"task_id", "worker_id", "generation", "coordinator_epoch", "coordinator_url"}
_ID = re.compile(r"[0-9a-f]{64}")
_BINDING_ID = re.compile(r"[0-9a-f]{32}")

_ROOT_DIRECTORY = r"""
const fs = require('fs');
const crypto = require('crypto');
function rootDirectory() {
  if (process.getuid() !== 0) throw Error();
  const run = fs.lstatSync('/run');
  if (!run.isDirectory() || run.uid !== 0 || ((run.mode & 0o22) && !(run.mode & 0o1000))) throw Error();
  const dir = '/run/agent-dispatch';
  try { fs.mkdirSync(dir, {mode: 0o755}); }
  catch (err) { if (err.code !== 'EEXIST') throw err; }
  const st = fs.lstatSync(dir);
  if (!st.isDirectory() || st.uid !== 0 || (st.mode & 0o777) !== 0o755) throw Error();
  return dir;
}
"""

# Runs only as root through an immutable Node executable; credentials use stdin.
_WRITE_TOKEN = _ROOT_DIRECTORY + r"""
let input = '';
const inputTimer = setTimeout(() => {
  process.stderr.write('Restricted task credential input timed out\n');
  process.exit(1);
}, 30000);
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input, 'utf8') > 8192) process.exit(1);
});
process.stdin.on('end', () => {
  clearTimeout(inputTimer);
  let tmp;
  try {
    if (!/^[\x21-\x7e]{1,8192}$/.test(input)) throw Error();
    const dir = rootDirectory();
    const expiry = Number(process.argv[1]);
    if (!Number.isFinite(expiry) || expiry <= Date.now() / 1000) throw Error();
    const fence = dir + '/publication-expiry';
    function existing(dest) {
      try {
        const old = fs.lstatSync(dest);
        if (!old.isFile() || old.uid !== 0 || (old.mode & 0o777) !== 0o444) throw Error();
        return true;
      } catch (err) { if (err.code !== 'ENOENT') throw err; }
      return false;
    }
    if (existing(fence)) {
      const previous = JSON.parse(fs.readFileSync(fence, 'utf8'));
      if (typeof previous !== 'number' || !Number.isFinite(previous) ||
          previous <= 0 || expiry < previous) throw Error();
    }
    function publish(dest, value) {
      tmp = dir + '/.' + crypto.randomBytes(16).toString('hex');
      const fd = fs.openSync(tmp, fs.constants.O_CREAT | fs.constants.O_EXCL |
                            fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o444);
      try { fs.writeFileSync(fd, value, 'utf8'); fs.fchmodSync(fd, 0o444); fs.fsyncSync(fd); }
      finally { fs.closeSync(fd); }
      fs.renameSync(tmp, dest);
      tmp = undefined;
    }
    const dest = dir + '/task-token';
    existing(dest);
    publish(fence, JSON.stringify(expiry));
    publish(dest, input);
  } catch (_) {
    if (tmp) { try { fs.unlinkSync(tmp); } catch (_) {} }
    process.stderr.write('Restricted task credential publication failed\n');
    process.exitCode = 1;
  }
});
"""

_INITIALIZE_LOCK = _ROOT_DIRECTORY + r"""
try {
  const name = process.argv[1];
  if (!['worker.lock', 'publication.lock'].includes(name)) throw Error();
  const dest = rootDirectory() + '/' + name;
  try {
    const fd = fs.openSync(dest, fs.constants.O_CREAT | fs.constants.O_EXCL |
                          fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o444);
    try { fs.fchmodSync(fd, 0o444); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
  } catch (err) { if (err.code !== 'EEXIST') throw err; }
  const st = fs.lstatSync(dest);
  if (!st.isFile() || st.uid !== 0 || (st.mode & 0o777) !== 0o444) throw Error();
} catch (_) {
  process.stderr.write('Restricted worker lock initialization failed\n');
  process.exitCode = 1;
}
"""

_WRITE_WORKER_ADMISSION = _ROOT_DIRECTORY + r"""
let tmp;
try {
  const generation = Number(process.argv[1]), nonce = process.argv[2];
  if (!Number.isSafeInteger(generation) || generation <= 0 ||
      (nonce !== '' && !/^[0-9a-f]{32}$/.test(nonce))) throw Error();
  const dir = rootDirectory(), dest = dir + '/worker-admission';
  try {
    const st = fs.lstatSync(dest);
    if (!st.isFile() || st.uid !== 0 || (st.mode & 0o777) !== 0o444 ||
        st.size > 1024) throw Error();
    const old = JSON.parse(fs.readFileSync(dest, 'utf8'));
    if (Object.keys(old).sort().join(',') !== 'generation,nonce' ||
        !Number.isSafeInteger(old.generation) || old.generation <= 0 ||
        typeof old.nonce !== 'string' ||
        (old.nonce !== '' && !/^[0-9a-f]{32}$/.test(old.nonce)) ||
        generation < old.generation ||
        (generation === old.generation && nonce !== old.nonce)) throw Error();
  } catch (err) { if (err.code !== 'ENOENT') throw err; }
  tmp = dir + '/.' + crypto.randomBytes(16).toString('hex');
  const fd = fs.openSync(tmp, fs.constants.O_CREAT | fs.constants.O_EXCL |
                        fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW, 0o444);
  try {
    fs.writeFileSync(fd, JSON.stringify({generation, nonce}));
    fs.fchmodSync(fd, 0o444); fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, dest); tmp = undefined;
} catch (_) {
  if (tmp) { try { fs.unlinkSync(tmp); } catch (_) {} }
  process.stderr.write('Restricted worker admission publication failed\n');
  process.exitCode = 1;
}
"""

_CHECK_WORKER_ADMISSION = r"""
const fs = require('fs');
try {
  const dir = fs.lstatSync('/run/agent-dispatch');
  const dest = '/run/agent-dispatch/worker-admission', st = fs.lstatSync(dest);
  if (!dir.isDirectory() || dir.uid !== 0 || (dir.mode & 0o777) !== 0o755 ||
      !st.isFile() || st.uid !== 0 || (st.mode & 0o777) !== 0o444 ||
      st.size > 1024) throw Error();
  const value = JSON.parse(fs.readFileSync(dest, 'utf8'));
  if (Object.keys(value).sort().join(',') !== 'generation,nonce' ||
      !Number.isSafeInteger(value.generation) || value.generation <= 0 ||
      value.generation !== Number(process.argv[1]) || !value.nonce ||
      value.nonce !== process.argv[2]) throw Error();
} catch (_) {
  process.stderr.write('Restricted worker admission is closed or superseded\n');
  process.exitCode = 75;
}
"""


def _path(container_id: str) -> Path:
    if not isinstance(container_id, str) or not _ID.fullmatch(container_id):
        raise RuntimeError("Task projection requires the full physical container ID")
    return _STATE / f"{container_id}.json"


def _text(value: object, label: str) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 1024
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise RuntimeError(f"Invalid task projection {label}")
    return value


def _facts(request: object) -> dict:
    if not isinstance(request, dict) or set(request) != _FACTS:
        raise RuntimeError("Task preparation requires the exact incarnation fields")
    for name in ("task_id", "worker_id", "coordinator_url"):
        _text(request[name], name)
    for name in ("generation", "coordinator_epoch"):
        if type(request[name]) is not int or request[name] < 0:
            raise RuntimeError(f"Invalid task projection {name}")
    try:
        url = urlsplit(request["coordinator_url"])
        port = url.port
    except ValueError:
        raise RuntimeError("Invalid task projection coordinator URL") from None
    if (
        url.scheme not in {"http", "https"} or not url.hostname
        or url.username is not None or url.password is not None
        or url.query or url.fragment or port == 0
        or "?" in request["coordinator_url"] or "#" in request["coordinator_url"]
    ):
        raise RuntimeError("Invalid task projection coordinator URL")
    return dict(request)


def _read(container_id: str) -> dict | None:
    path = _path(container_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Container task binding is unreadable; refusing admission") from None
    try:
        if (
            not isinstance(raw, dict)
            or set(raw) - {"attempted_expires_at", "worker_admission_generation"} != _FACTS | {
                "version", "container_id", "binding_id", "owner_session_id", "expires_at"
            }
            or type(raw["version"]) is not int or raw["version"] != 1
            or raw["container_id"] != container_id
            or not _BINDING_ID.fullmatch(raw["binding_id"])
        ):
            raise ValueError
        facts = {key: raw[key] for key in _FACTS}
        if any(value is not None for value in facts.values()):
            _facts(facts)
        elif any(raw.get(key) is not None for key in (
            "owner_session_id", "expires_at", "attempted_expires_at",
        )):
            raise ValueError
        if raw["owner_session_id"] is not None:
            _text(raw["owner_session_id"], "owner_session_id")
        expiry = raw["expires_at"]
        if expiry is not None and (
            type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0
        ):
            raise ValueError
        attempted = raw.setdefault("attempted_expires_at", expiry)
        if attempted is not None and (
            type(attempted) not in (int, float) or not math.isfinite(attempted)
            or attempted <= 0 or (expiry is not None and attempted < expiry)
        ):
            raise ValueError
        if expiry is not None and attempted is None:
            raise ValueError
        if attempted is not None and raw["owner_session_id"] is None:
            raise ValueError
        admission_generation = raw.setdefault("worker_admission_generation", 0)
        if type(admission_generation) is not int or not 0 <= admission_generation < 2**53:
            raise ValueError
    except (KeyError, TypeError, ValueError, RuntimeError):
        raise RuntimeError("Container task binding is invalid; refusing admission") from None
    return raw


def verify_binding(container_id: str, binding_id: str) -> dict:
    binding = _read(container_id)
    if binding is None or binding["binding_id"] != binding_id or binding["task_id"] is None:
        raise RuntimeError("Container task binding does not match this launch")
    return binding


def register_birth(container_id: str, binding_id: str) -> None:
    """Retain one provider birth; missing authority never means an unused slot."""
    if not isinstance(binding_id, str) or not _BINDING_ID.fullmatch(binding_id):
        raise RuntimeError("Invalid container dispatch birth identity")
    write_json_exclusive(_path(container_id), {
        **dict.fromkeys(_FACTS), "version": 1, "container_id": container_id,
        "binding_id": binding_id, "owner_session_id": None, "expires_at": None,
        "attempted_expires_at": None,
    })


def creation_args(fleet_name: str, fleet, name: str) -> list[str]:
    args = [
        "run", "-d", "--name", name,
        "--label", f"{FLEET_LABEL}={fleet_name}",
        "--label", f"{SECURITY_PROFILE_LABEL}={fleet.security_profile}",
    ]
    if fleet.dispatch_only:
        import uuid

        fleet.validate_restricted()
        args += [
            "--label", f"{DISPATCH_ONLY_LABEL}=true",
            "--label", f"{DISPATCH_BINDING_LABEL}={uuid.uuid4().hex}",
        ]
    return args


def run_image(fleet, args: list[str], *, run):
    result = run(args, timeout=120)
    if fleet.dispatch_only and result.returncode == 0:
        birth = next(value.partition("=")[2] for value in args if value.startswith(DISPATCH_BINDING_LABEL + "="))
        register_birth(result.stdout.strip(), birth)
    return result


def prepare(name: str, expected_container_id: str, request: object) -> dict:
    """Seal a physical container to one task incarnation before worker startup."""
    from . import lease, lifecycle
    from .resolver import resolve_live_exec_target

    facts = _facts(request)
    _path(expected_container_id)
    with lease._lease_lock():
        target = resolve_live_exec_target(name, dispatch_management=True)
        if (
            target.actual_profile != RESTRICTED_PROFILE or not target.fleet.dispatch_only
            or target.container_id != expected_container_id
        ):
            raise RuntimeError("Task preparation requires the exact dispatch-only container")
        holds = lease._read_live_records(
            lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
        )
        admissions = lease._read_live_records(
            lease._SESSION_ADMISSIONS_FILE, lease.SessionAdmission, lease.SESSION_ADMISSION_TTL,
        )
        if name in holds or any(item.container == name for item in admissions.values()):
            raise RuntimeError("Container is busy; refusing task preparation")
        binding = _read(expected_container_id)
        if binding is None:
            raise RuntimeError("Container dispatch birth authority is missing; recreate it")
        inspected = lifecycle.inspect_container(expected_container_id)
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if (
            inspected.get("Id") != expected_container_id
            or labels.get(DISPATCH_BINDING_LABEL) != binding["binding_id"]
        ):
            raise RuntimeError("Container dispatch birth identity does not match retained authority")
        if binding["task_id"] is not None:
            if any(binding[key] != facts[key] for key in _FACTS):
                raise RuntimeError("Physical container already belongs to another task incarnation; recreate it")
        else:
            binding.update(facts)
            atomic_write_json(_path(expected_container_id), binding)
    return binding


def project(name: str, expected_container_id: str, request: object) -> dict:
    """Publish or rotate a trusted issuer's credential without persisting it."""
    from . import lease, lifecycle
    from .resolver import resolve_live_exec_target

    if not isinstance(request, dict) or set(request) != {
        "binding_id", "owner_session_id", "task_token", "expires_at",
    }:
        raise RuntimeError("Credential projection requires the exact grant fields")
    owner = _text(request["owner_session_id"], "owner_session_id")
    token = request["task_token"]
    if (
        not isinstance(token, str) or not 0 < len(token) <= 8192
        or any(ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        raise RuntimeError("Invalid task credential")
    expiry = request["expires_at"]
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= time.time():
        raise RuntimeError("Task credential expiry is invalid or expired")
    with lease._lease_lock():
        binding = verify_binding(expected_container_id, request["binding_id"])
        if binding["owner_session_id"] not in (None, owner):
            raise RuntimeError("Physical container native session cannot be rebound; recreate it")
        if binding["attempted_expires_at"] is not None and expiry < binding["attempted_expires_at"]:
            raise RuntimeError("Task credential rotation cannot move expiry backwards")
        target = resolve_live_exec_target(
            name, dispatch_binding_id=binding["binding_id"],
        )
        if target.container_id != expected_container_id:
            raise RuntimeError("Container physical identity changed before credential delivery")
        holds = lease._read_live_records(
            lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
        )
        if name in holds:
            raise RuntimeError("Container lifecycle is busy; refusing credential delivery")
        # Publication may succeed before its acknowledgement; fence the attempt first.
        binding["owner_session_id"] = owner
        binding["attempted_expires_at"] = expiry
        atomic_write_json(_path(expected_container_id), binding)
        inspected = lifecycle.inspect_container(expected_container_id)
        if inspected.get("Id") != expected_container_id:
            raise RuntimeError("Container physical identity changed before credential delivery")
        command = build_publication_command(expected_container_id, inspected, expiry)
        try:
            result = subprocess.run(
                command,
                input=token, text=True, capture_output=True, timeout=30,
                creationflags=no_window_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("Restricted task credential delivery failed") from None
        if result.returncode:
            raise RuntimeError("Restricted task credential delivery failed")
        binding["expires_at"] = expiry
        atomic_write_json(_path(expected_container_id), binding)
    return binding


def environment(binding: dict) -> dict[str, str]:
    """Return only the nonsecret, immutable worker call projection."""
    return {
        "AGENT_DISPATCH_TASK_ID": binding["task_id"],
        "AGENT_DISPATCH_TASK_WORKER_ID": binding["worker_id"],
        "AGENT_DISPATCH_TASK_URL": binding["coordinator_url"],
        "AGENT_DISPATCH_TASK_TOKEN_FILE": TOKEN_FILE,
    }


def _worker_executable(
    container_id: str, user: str, inspected: dict, *, kind: str, deadline: float,
    executables: dict | None = None,
) -> tuple[str, str]:
    if executables is None:
        return resolve_executable(container_id, user, inspected, kind=kind, deadline=deadline)
    snapshot = json.dumps(
        [inspected.get("Id"), (inspected.get("State") or {}).get("StartedAt"),
         inspected.get("Config"), inspected.get("HostConfig"), inspected.get("Mounts")],
        sort_keys=True,
    )
    key = container_id, user, kind, snapshot
    if key not in executables:
        executables[key] = resolve_executable(
            container_id, user, inspected, kind=kind, deadline=deadline,
        )
    return executables[key]


def build_spawn_command(
    target, binding: dict, acp_command: str, *, worker_admission: tuple[int, str],
    executables: dict | None = None,
) -> list[str]:
    from . import lifecycle

    inspected = lifecycle.inspect_container(target.container_id)
    if inspected.get("Id") != binding["container_id"]:
        raise RuntimeError("Container physical identity changed before task launch")
    bash, home = _worker_executable(
        target.container_id, target.user, inspected, kind="bash",
        deadline=time.monotonic() + 30, executables=executables,
    )
    flock, _ = _worker_executable(
        target.container_id, target.user, inspected, kind="flock",
        deadline=time.monotonic() + 30, executables=executables,
    )
    node, _ = _worker_executable(
        target.container_id, target.user, inspected, kind="node",
        deadline=time.monotonic() + 30, executables=executables,
    )
    prefix = sanitized_exec_prefix(target.container_id, target.user, home)
    projected = environment(binding)
    if target.fleet.native_inference is not None:
        projected.update(target.fleet.native_inference.environment())
    env_args = [part for key, value in projected.items() for part in ("-e", f"{key}={value}")]
    fenced = (
        f"exec 9<{shlex.quote(WORKER_LOCK)}; "
        f"{shlex.quote(flock)} --nonblock 9 || exit 75; "
        f"{shlex.quote(node)} -e {shlex.quote(_CHECK_WORKER_ADMISSION)} "
        f"{worker_admission[0]} {shlex.quote(worker_admission[1])} || exit 75; "
        f"exec {shlex.quote(bash)} --noprofile --norc -c {shlex.quote(acp_command)}"
    )
    return [
        *prefix[:2], "-i", *prefix[2:-1], *env_args, prefix[-1],
        bash, "--noprofile", "--norc", "-c", fenced,
    ]


def _initialize_worker_lock(target, inspected: dict, *, executables: dict | None = None) -> None:
    _initialize_lock(target.container_id, inspected, "worker.lock", executables=executables)


def _initialize_lock(
    container_id: str, inspected: dict, name: str, *, executables: dict | None = None,
) -> None:
    node, home = _worker_executable(
        container_id, "0", inspected, kind="node",
        deadline=time.monotonic() + 30, executables=executables,
    )
    try:
        result = subprocess.run(
            [*sanitized_exec_prefix(container_id, "0", home), node, "-e", _INITIALIZE_LOCK, name],
            stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=30,
            creationflags=no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Restricted worker lock initialization failed") from None
    if result.returncode:
        raise RuntimeError("Restricted worker lock initialization failed")


def build_publication_command(container_id: str, inspected: dict, expiry: float) -> list[str]:
    """Guest ownership and expiry fencing outlive the host Docker client."""
    _initialize_lock(container_id, inspected, "publication.lock")
    deadline = time.monotonic() + 30
    bash, home = resolve_executable(container_id, "0", inspected, kind="bash", deadline=deadline)
    flock, _ = resolve_executable(container_id, "0", inspected, kind="flock", deadline=deadline)
    node, _ = resolve_executable(container_id, "0", inspected, kind="node", deadline=deadline)
    script = (
        f"exec 9<{shlex.quote(PUBLICATION_LOCK)} || exit 1; "
        f"{shlex.quote(flock)} --nonblock --conflict-exit-code 75 9 || exit $?; "
        f"exec {shlex.quote(node)} -e {shlex.quote(_WRITE_TOKEN)} {shlex.quote(str(expiry))}"
    )
    prefix = sanitized_exec_prefix(container_id, "0", home)
    return [
        *prefix[:2], "-i", *prefix[2:], bash, "--noprofile", "--norc", "-c", script,
    ]

def _reserve_worker_admission(
    name: str, container_id: str, inspected: dict, *, admission_token: str | None = None,
    hold_token: str | None = None,
) -> tuple[int, str]:
    from . import lease

    with lease._lease_lock():
        binding = _read(container_id)
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if (
            binding is None or inspected.get("Id") != container_id
            or labels.get(DISPATCH_BINDING_LABEL) != binding["binding_id"]
            or (admission_token is None) == (hold_token is None)
        ):
            raise RuntimeError("Restricted worker admission authority is missing or mismatched")
        holds = lease._read_live_records(
            lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL,
        )
        if admission_token is not None:
            admissions = lease._read_live_records(
                lease._SESSION_ADMISSIONS_FILE, lease.SessionAdmission, lease.SESSION_ADMISSION_TTL,
            )
            if name in holds or not any(
                record.container == name and record.token == admission_token
                for record in admissions.values()
            ):
                raise RuntimeError("Restricted worker launch admission changed")
            nonce = os.urandom(16).hex()
        else:
            if name not in holds or holds[name].token != hold_token:
                raise RuntimeError("Restricted worker lifecycle admission changed")
            nonce = ""
        generation = binding["worker_admission_generation"] + 1
        if generation >= 2**53:
            raise RuntimeError("Restricted worker admission generation exhausted")
        binding["worker_admission_generation"] = generation
        atomic_write_json(_path(container_id), binding)
    return generation, nonce


def _publish_worker_admission(
    container_id: str, inspected: dict, admission: tuple[int, str],
    *, executables: dict | None = None,
) -> int:
    if executables is None:
        executables = {}
    _initialize_worker_lock(
        SimpleNamespace(container_id=container_id), inspected, executables=executables,
    )
    deadline = time.monotonic() + 30
    bash, home = _worker_executable(
        container_id, "0", inspected, kind="bash", deadline=deadline, executables=executables,
    )
    flock, _ = _worker_executable(
        container_id, "0", inspected, kind="flock", deadline=deadline, executables=executables,
    )
    node, _ = _worker_executable(
        container_id, "0", inspected, kind="node", deadline=deadline, executables=executables,
    )
    script = (
        f"exec 9<{shlex.quote(WORKER_LOCK)} || exit 1; "
        f"{shlex.quote(flock)} --nonblock --conflict-exit-code 75 9 || exit $?; "
        f"exec {shlex.quote(node)} -e {shlex.quote(_WRITE_WORKER_ADMISSION)} "
        f"{admission[0]} {shlex.quote(admission[1])}"
    )
    result = subprocess.run(
        [*sanitized_exec_prefix(container_id, "0", home),
         bash, "--noprofile", "--norc", "-c", script],
        stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=30,
        creationflags=no_window_flags(),
    )
    return result.returncode


def close_worker_admission(
    name: str, container_id: str, inspected: dict, *, hold_token: str,
) -> SessionLiveness:
    """Fence even delayed guest launches before a destructive lifecycle action."""
    try:
        admission = _reserve_worker_admission(
            name, container_id, inspected, hold_token=hold_token,
        )
        code = _publish_worker_admission(container_id, inspected, admission)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return SessionLiveness("unknown", [], [], "restricted worker admission closure failed")
    if code == 75:
        return SessionLiveness("active", [], [], "active restricted worker lock present")
    if code != 0:
        return SessionLiveness("unknown", [], [], "restricted worker admission closure failed")
    return SessionLiveness("idle", [], [])


def probe_worker_liveness(container_id: str, inspected: dict) -> SessionLiveness:
    """Probe the guest-held lock, independent of host wrapper process lifetime."""
    try:
        if inspected.get("Id") != container_id:
            raise RuntimeError("Container physical identity changed before worker probe")
        _initialize_worker_lock(SimpleNamespace(container_id=container_id), inspected)
        deadline = time.monotonic() + 30
        bash, home = resolve_executable(container_id, "0", inspected, kind="bash", deadline=deadline)
        flock, _ = resolve_executable(container_id, "0", inspected, kind="flock", deadline=deadline)
        script = (
            f"exec 9<{shlex.quote(WORKER_LOCK)} || exit 1; "
            f"{shlex.quote(flock)} --nonblock --conflict-exit-code 75 9"
        )
        result = subprocess.run(
            [*sanitized_exec_prefix(container_id, "0", home),
             bash, "--noprofile", "--norc", "-c", script],
            stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=30,
            creationflags=no_window_flags(),
        )
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return SessionLiveness("unknown", [], [], "restricted worker lock probe failed")
    if result.returncode == 75:
        return SessionLiveness("active", [], [], "active restricted worker lock present")
    if result.returncode != 0:
        return SessionLiveness("unknown", [], [], "restricted worker lock probe failed")
    return SessionLiveness("idle", [], [])


def launch_task(args, run_stdio) -> int:
    """Admit the explicit task lane without widening ordinary provider launches."""
    from .lease import ProviderAdmissionError

    try:
        return _launch_task(args, run_stdio)
    except ProviderAdmissionError as busy:
        print(str(busy), file=sys.stderr)
        return 75


def _launch_task(args, run_stdio) -> int:
    from .config import load_config
    from .lease import ProviderAdmissionError, session_admission
    from .lifecycle import inspect_container
    from .resolver import _append_copilot_args, resolve_extra_copilot_args, resolve_live_exec_target

    binding_id, expected_id = args.dispatch_binding_id, args.expected_container_id
    if not binding_id or not expected_id:
        raise RuntimeError("Task launch requires both physical and binding identities")
    target = resolve_live_exec_target(args.name, dispatch_binding_id=binding_id)
    if target.container_id != expected_id:
        raise RuntimeError("Container physical identity changed before task launch")
    from .hosting_policy import guarded_launch, require_container_hosting

    require_container_hosting(target)
    with session_admission(
        args.name, dispatch_container_id=expected_id, dispatch_binding_id=binding_id,
    ) as admission:
        target = resolve_live_exec_target(
            args.name, config=load_config(), dispatch_binding_id=binding_id,
        )
        if target.container_id != expected_id:
            raise RuntimeError("Container physical identity changed after task admission")
        require_container_hosting(target)
        binding = verify_binding(expected_id, binding_id)
        inspected = inspect_container(expected_id)
        worker_admission = _reserve_worker_admission(
            args.name, expected_id, inspected, admission_token=admission.token,
        )
        executables = {}
        if _publish_worker_admission(
            expected_id, inspected, worker_admission, executables=executables,
        ) != 0:
            raise ProviderAdmissionError("Restricted worker guest admission is busy or superseded")
        command = build_spawn_command(
            target, binding,
            guarded_launch(_append_copilot_args(
                target.acp_command, resolve_extra_copilot_args(getattr(args, "copilot_args", None)),
            ), target.workspace_folder), worker_admission=worker_admission, executables=executables,
        )
        env = os.environ.copy()
        if args.stdio:
            return run_stdio(command, env)
        return subprocess.run(command, env=env, creationflags=no_window_flags()).returncode


def add_subparsers(sub) -> None:
    parser = sub.add_parser(
        "dispatch-check-hosting", help="Read-only repository hosting admission for a physical worker",
    )
    parser.add_argument("name")
    parser.add_argument("--expected-container-id", required=True)
    parser.set_defaults(func=_check_hosting)
    for command, action in (("dispatch-prepare", prepare), ("dispatch-project", project)):
        parser = sub.add_parser(command, help="Apply a trusted task projection from bounded JSON stdin")
        parser.add_argument("name")
        parser.add_argument("--expected-container-id", required=True)
        parser.set_defaults(func=lambda args, action=action: _command(args, action))


def _check_hosting(args) -> int:
    from .hosting_policy import require_container_hosting
    from .resolver import resolve_live_exec_target

    target = resolve_live_exec_target(args.name, dispatch_management=True)
    if target.container_id != args.expected_container_id:
        raise RuntimeError("Container physical identity changed before hosting admission")
    require_container_hosting(target)
    print(json.dumps({
        "version": 1, "allowed": True, "container_id": target.container_id, "checked": True,
    }))
    return 0


def add_exec_subparser(sub) -> None:
    parser = sub.add_parser("exec", help="Run the ACP launch command in a container")
    parser.add_argument("name", help="Container name")
    parser.add_argument("--stdio", action="store_true", help="Attach stdio transport.")
    parser.add_argument("--dispatch-binding-id")
    parser.add_argument("--expected-container-id")
    parser.add_argument(
        "--force", action="store_true",
        help="Terminate a live SSH holder and take over this trusted container",
    )
    parser.add_argument("copilot_args", nargs="*", help="Extra copilot args (after --).")


def _command(args, action) -> int:
    import sys

    from .dispatch_target import launch_spec

    raw = sys.stdin.read(16385)
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        raise RuntimeError("Task projection request is not valid UTF-8") from None
    if size > 16384:
        raise RuntimeError("Task projection request exceeds its byte limit")
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Task projection request is not valid JSON") from None
    binding = action(args.name, args.expected_container_id, request)
    result = {
        **binding, "task_token_file": TOKEN_FILE,
        **launch_spec(args.name, binding),
    }
    print(json.dumps(result))
    return 0
