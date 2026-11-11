"""Standard-library receiving entrypoint transported by the selected Bridge."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile

MAX_INPUT = 24 * 1024 * 1024
MAX_SUPPORT = 16 * 1024 * 1024
_FORMAT = "#{session_id}:#{session_created}|#{pane_id}"


def _directory(path: Path) -> None:
    for parent in reversed((path, *path.parents)):
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if (
                not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Mux receiving state must not traverse linked directories")
        else:
            parent.mkdir(mode=0o700)


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _request(value: object) -> dict:
    required = {
        "version", "attempt", "session_id", "cwd", "policy_context", "support_sha256",
        "state_root", "copilot_args", "scope_id",
    }
    if (
        not isinstance(value, dict) or not required <= set(value)
        or set(value) - required - {"ensure_mux", "registration", "driven_by"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value.get("ensure_mux", False)) is not bool
    ):
        raise ValueError("Unsupported mux receiving request")
    for key in ("attempt", "session_id"):
        if not isinstance(value[key], str) or str(uuid.UUID(value[key])) != value[key]:
            raise ValueError("Mux receiving requires canonical attempt and native session UUIDs")
    for key in ("cwd", "state_root"):
        if (
            not isinstance(value[key], str)
            or not (Path(value[key]).is_absolute() or PurePosixPath(value[key]).is_absolute())
            or "\0" in value[key]
            or ".." in value[key].replace("\\", "/").split("/")
        ):
            raise ValueError("Mux receiving requires absolute execution and state paths")
    if (
        not isinstance(value["policy_context"], str)
        or not isinstance(value["scope_id"], str) or not value["scope_id"]
        or len(value["scope_id"]) > 256 or "\0" in value["scope_id"]
        or not isinstance(value["support_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", value["support_sha256"]) is None
    ):
        raise ValueError("Mux receiving requires its selected policy, scope and support identity")
    if "driven_by" in value and (
        not isinstance(value["driven_by"], str) or not value["driven_by"].strip()
        or len(value["driven_by"]) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value["driven_by"])
    ):
        raise ValueError("Invalid portable session driver label")
    if "registration" in value:
        registration = value["registration"]
        if (
            not isinstance(registration, dict) or set(registration) != {"token", "port"}
            or type(registration["port"]) is not int or not 0 < registration["port"] <= 65535
            or not isinstance(registration["token"], str)
            or re.fullmatch(r"[A-Za-z0-9._~-]{16,512}", registration["token"]) is None
        ):
            raise ValueError("Invalid launch-scoped Bridge registration")
    arguments = value["copilot_args"]
    if (
        not isinstance(arguments, list) or len(arguments) > 256
        or any(not isinstance(arg, str) or "\0" in arg for arg in arguments)
    ):
        raise ValueError("Invalid native Copilot arguments")
    values = {
        "--agent", "--model", "--reasoning-effort", "--context", "--name", "--log-level",
        "--allow-tool", "--deny-tool", "--allow-url", "--deny-url", "--max-ai-credits",
        "--max-autopilot-continues", "--mode", "--extension-sdk-path",
    }
    flags = {
        "--allow-all", "--allow-all-tools", "--allow-all-paths", "--allow-all-urls",
        "--experimental", "--no-experimental", "--no-ask-user", "--no-auto-update",
        "--trust-folder", "--autopilot", "--plan", "--disable-client-telemetry",
    }
    index = 0
    while index < len(arguments):
        name, separator, _value = arguments[index].partition("=")
        if name in values:
            if not separator:
                index += 1
                if index == len(arguments) or arguments[index].startswith("-"):
                    raise ValueError("Native argument requires its explicit value")
        elif name not in flags or separator:
            raise ValueError("Native arguments cannot replace the receiving identity, mode or seed owner")
        index += 1
    return value


def _unpack(payload: bytes, root: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if (
            len(entries) > 2048 or len(set(names)) != len(names)
            or sum(entry.file_size for entry in entries) > MAX_SUPPORT
        ):
            raise ValueError("Mux support archive exceeds its unique asset bound")
        for entry in entries:
            name = entry.orig_filename
            if (
                name != entry.filename or not name or "\\" in name or ":" in name
                or PurePosixPath(name).is_absolute()
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir()
            ):
                raise ValueError("Unsafe mux receiving support asset")
            target = root.joinpath(*name.split("/"))
            _directory(target.parent)
            with target.open("xb") as stream:
                os.chmod(target, 0o600)
                stream.write(archive.read(entry))


def _policy(request: dict, support: Path) -> None:
    sys.path.insert(0, str(support))
    try:
        from repository_policy import receiving

        if not Path(receiving.__file__).resolve().is_relative_to(support.resolve()):
            raise ValueError("Mux policy was not loaded from the selected receiving support")
        receiving.require_receiving_policy(request["cwd"], request["policy_context"])
    finally:
        sys.path.remove(str(support))


def _run_tmux(argv: list[str], *, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=30, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,  # headless-guard: allow standalone stdlib receiver
    )


def _core_executable(support: Path) -> list[str]:
    archive = support / "driver.pyz"
    info = archive.lstat()
    if not stat.S_ISREG(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Selected mux core must be a regular receiving artifact")
    interpreter = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    if not interpreter.is_file():
        raise ValueError("Selected mux core requires a physical base interpreter")
    return [str(interpreter), "-I", "-S", str(archive)]


def _require_core(support: Path) -> None:
    selected = subprocess.run(
        [*_core_executable(support), "--support-json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        timeout=15, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,  # headless-guard: allow standalone stdlib receiver
    )
    try:
        declaration = json.loads(selected.stdout)
        if (
            selected.returncode != 0
            or declaration.get("contract") != "agent-remote-driver.session-host"
            or type(declaration.get("version")) is not int or declaration["version"] != 1
            or type(declaration.get("protocol_version")) is not int
            or declaration["protocol_version"] != 1
            or any(
                type(declaration.get(key)) is not list
                or not all(type(item) is str for item in declaration[key])
                or required not in declaration[key]
                for key, required in (
                    ("modes", "headed"), ("transports", "tcp-frame"),
                    ("lifetime_policies", "cli"), ("headed_process_receipts", "native-child-v1"),
                )
            )
        ):
            raise ValueError("unsupported declaration")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("Selected mux core does not support headed CLI execution") from error


def _native(value: str) -> dict:
    match = re.fullmatch(r"(\$[0-9]+:[0-9]+)\|(%[0-9]+)\s*", value)
    if match is None:
        raise ValueError("Mux creation has no confirmed native incarnation")
    return {"incarnation": match[1], "pane": match[2]}


def _mux_binary() -> str:
    if os.name == "nt":
        raise ValueError("Portable mux receiving requires the POSIX TMux boundary")
    binary = shutil.which("tmux")
    if not binary:
        raise ValueError("TMux is unavailable; prepare the selected native prerequisite through its owner")
    return binary


def _socket_identity(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode):
        raise ValueError("Mux receiving endpoint is not a native socket")
    # TMux chmods this socket on human attach/detach, so ctime is not its birth.
    return {"device": info.st_dev, "inode": info.st_ino}


def _prepare_mux(request: dict, support: Path) -> None:
    if not request.get("ensure_mux") or shutil.which("tmux"):
        return
    helper = support / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "mux_prerequisite.py"
    ensure = runpy.run_path(str(helper))["ensure_mux_available"]
    if not ensure("tmux"):
        raise ValueError("The selected native TMux prerequisite could not be prepared")


def _registration(request: dict, attempt: Path) -> Path | None:
    registration = request.get("registration")
    if registration is None:
        return None
    root = attempt / "bridge"
    _directory(root)
    auth = root / "auth.yaml"
    expected = "token: " + registration["token"] + "\n"
    try:
        with auth.open("x", encoding="utf-8") as stream:
            os.chmod(auth, 0o600)
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if auth.is_symlink() or auth.read_text(encoding="utf-8") != expected:
            raise ValueError("Retained registration credentials differ from the original attempt")
    route = {"active": {"bind": "127.0.0.1", "port": registration["port"], "forwarded": True}}
    active = root / "active.json"
    if active.is_symlink():
        raise ValueError("Launch registration route must not be a link")
    if not active.exists():
        _write(active, route)
    elif json.loads(active.read_text(encoding="utf-8")) != route:
        raise ValueError("Retained registration route differs from the original attempt")
    return root


@contextmanager
def _launch_lock(attempt: Path):
    path = attempt / "launch.lock"
    try:
        stream = path.open("x")
    except FileExistsError:
        yield False
        return
    with stream:
        os.chmod(path, 0o600)
        owned = os.fstat(stream.fileno())
        try:
            yield True
        finally:
            stream.close()
            # A crashed holder leaves the original attempt pending; no stale
            # lock stealing or removal of another invocation's lock.
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    path.unlink()
            except FileNotFoundError:
                pass


def receive(request: dict, payload: bytes) -> dict:
    """Create once, or observe the same confirmed incarnation without replay."""
    request = _request(request)
    tmux = None if request.get("ensure_mux") else _mux_binary()
    if len(payload) > MAX_SUPPORT or hashlib.sha256(payload).hexdigest() != request["support_sha256"]:
        raise ValueError("Mux support differs from the selected launch")
    cwd = Path(request["cwd"]).resolve(strict=True)
    if not cwd.is_dir() or str(cwd) != request["cwd"]:
        raise ValueError("Mux receiving requires the actual canonical execution directory")
    root = Path(request["state_root"])
    _directory(root)
    attempt = root / request["attempt"]
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    session = "wt-bridge-" + request["attempt"]
    socket = attempt / "tmux.sock"
    if os.name != "nt" and len(os.fsencode(socket)) >= 104:
        raise ValueError("Mux receiving state path exceeds the native socket path bound")
    support = attempt / "support"
    try:
        attempt.mkdir(mode=0o700)
        first = True
    except FileExistsError:
        first = False
        _directory(attempt)
        record = json.loads((attempt / "receipt.json").read_text(encoding="utf-8"))
        if record.get("request_digest") != digest:
            raise ValueError("Mux attempt belongs to different launch inputs")
        _policy(request, support)
        if record.get("phase") not in {"created", "ready"}:
            return {**record, "ok": False, "pending": True}
        if record.get("phase") == "created":
            if _socket_identity(socket) != record.get("socket_identity"):
                raise ValueError("Retained mux socket was replaced; creation will not be replayed")
            observed = _run_tmux(
                [tmux or _mux_binary(), "-S", str(socket), "display-message", "-p", "-t", record["native"]["pane"], _FORMAT],
                cwd=str(cwd),
            )
            if observed.returncode or _native(observed.stdout) != record["native"]:
                raise ValueError("Retained mux incarnation is unavailable; creation will not be replayed")
            return {**record, "ok": True, "reused": True}
    if first:
        record = {
            "version": 1, "attempt": request["attempt"], "session_id": request["session_id"],
            "request_digest": digest, "phase": "preparing", "mux_session": session,
            "mux_socket": str(socket), "cwd": str(cwd),
        }
        _write(attempt / "receipt.json", record)
        _directory(support)
        _unpack(payload, support)
        _policy(request, support)
        _write(attempt / "launch.json", request)
        record["phase"] = "ready"
        _write(attempt / "receipt.json", record)
    with _launch_lock(attempt) as acquired:
        if not acquired:
            return {**record, "ok": False, "pending": True}
        record = json.loads((attempt / "receipt.json").read_text(encoding="utf-8"))
        if record.get("request_digest") != digest or record.get("phase") != "ready":
            return {**record, "ok": False, "pending": True}
        _policy(request, support)
        _require_core(support)
        _prepare_mux(request, support)
        _policy(request, support)
        _registration(request, attempt)
        command = [tmux or _mux_binary(), "-S", str(socket)]
        record["phase"] = "starting"
        _write(attempt / "receipt.json", record)
        # A private native socket inherits this request's credential environment,
        # without placing secrets on tmux argv or rewriting an existing server.
        argv = [
            *command, "new-session", "-d", "-s", session, "-P", "-F", _FORMAT,
            "-c", str(cwd), sys.executable, "-I", "-S", str(support / "receiver.py"),
            "child", str(attempt),
        ]
        try:
            created = _run_tmux(argv, cwd=str(cwd))
            if created.returncode:
                raise ValueError("Native mux creation did not confirm success")
            native = _native(created.stdout)
            socket_identity = _socket_identity(socket)
        except (OSError, ValueError, subprocess.SubprocessError):
            record["phase"] = "unknown"
            _write(attempt / "receipt.json", record)
            return {**record, "ok": False, "pending": True}
        record.update(phase="created", native=native, socket_identity=socket_identity)
        _write(attempt / "receipt.json", record)
        return {**record, "ok": True, "reused": False}


def child(attempt: Path) -> None:
    """Recheck actual policy inside the selected pane before native execution."""
    _directory(attempt)
    request = _request(json.loads((attempt / "launch.json").read_text(encoding="utf-8")))
    support = attempt / "support"
    if Path.cwd().resolve() != Path(request["cwd"]):
        raise ValueError("Mux child execution directory differs from its retained launch")
    _policy(request, support)
    bridge = support / "plugins" / "agent-bridge"
    driver = support / "plugins" / "agent-remote-driver"
    env = os.environ.copy()
    for key in (
        "COPILOT_AGENT_SESSION_ID", "SESSION_ID", "WORKTREE_ID", "WORKTREE_LAUNCH_ID",
        "AGENT_WORKTREES_SESSION_ID", "AGENT_BRIDGE_SCOPE_ID", "AGENT_BRIDGE_DRIVEN_BY",
    ):
        env.pop(key, None)
    env.update({
        "AGENT_BRIDGE_REMOTE_DRIVER_ROOT": str(driver),
        "AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT": str(bridge),
        "AGENT_BRIDGE_NATIVE_SESSION_ID": request["session_id"],
        "AGENT_BRIDGE_PORTABLE_ATTEMPT": request["attempt"],
        "AGENT_BRIDGE_DRIVER_OWNER": "bridge-mux-" + request["attempt"],
        "AGENT_BRIDGE_SCOPE_ID": request["scope_id"],
        "AGENT_REMOTE_DRIVER_CONFIG_DIR": str(attempt / "driver"),
    })
    if request.get("registration") is not None:
        env["AGENT_BRIDGE_CONFIG_DIR"] = str(attempt / "bridge")
    if request.get("driven_by") is not None:
        env["AGENT_BRIDGE_DRIVEN_BY"] = request["driven_by"]
    argv = [
        "bash", str(support / "mux" / "pane-wrapper.sh"),
        "--aw-manager-root", str(support / "mux"),
        *_core_executable(support), "--mode", "headed", "--lifetime-policy", "cli",
        "--session-id", request["session_id"],
        "--attempt-directory", str(attempt / "driver-core"),
        "--state-file", str(attempt / "driver-core" / "host.json"),
        "--cwd", request["cwd"], "--repository-policy-context", request["policy_context"], "--",
        "copilot", "--session-id", request["session_id"],
        "--plugin-dir", str(driver), "--plugin-dir", str(bridge), *request["copilot_args"],
    ]
    os.execvpe(argv[0], argv, env)


def main() -> None:
    """Read one bounded transport envelope; never receive a prompt over a TTY."""
    if len(sys.argv) == 3 and sys.argv[1] == "child":
        child(Path(sys.argv[2]))
        return
    wire = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(wire) > MAX_INPUT:
        raise ValueError("Mux receiving envelope exceeds its input bound")
    envelope = json.loads(wire)
    if not isinstance(envelope, dict) or set(envelope) != {"request", "support"}:
        raise ValueError("Invalid mux receiving envelope")
    payload = base64.b64decode(envelope["support"], validate=True)
    print(json.dumps(receive(envelope["request"], payload), separators=(",", ":")))


if __name__ == "__main__":
    main()
