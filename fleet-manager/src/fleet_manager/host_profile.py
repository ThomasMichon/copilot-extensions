"""Explicit, bounded Linux host-profile bootstrap; never a deployment installer."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

from .trusted_systemd_profile import PROFILE_NAME, profile_bytes

STATE_ROOT = Path("/var/lib/fleet-manager/host-profiles")
PROFILE_ROOT = Path("/etc/apparmor.d")
KERNEL_PROFILES = Path("/sys/kernel/security/apparmor/profiles")
PROC = Path("/proc")
PARSER = "/usr/sbin/apparmor_parser"
LIMIT = 65536


class ProfileError(ValueError):
    """A bounded reason code, never raw parser output or host environment."""


def _root() -> None:
    if (sys.platform != "linux" or not hasattr(os, "geteuid")
            or os.geteuid() != 0 or os.getegid() != 0):
        raise ProfileError("linux-root-required")


def _stamp(info) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid,
            stat.S_IMODE(info.st_mode), info.st_nlink]


def _directory(path: Path, private=False) -> list[int]:
    for member in (*reversed(path.parents), path):
        info = member.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise ProfileError("unsafe-directory")
    identity = _stamp(path.lstat())[:5]
    if private and identity[4] != 0o700:
        raise ProfileError("private-directory-mode-drift")
    return identity


def _file(path: Path, mode: int, expected=None) -> tuple[bytes, list[int]]:
    _directory(path.parent)
    before = path.lstat()
    identity = _stamp(before)
    if (not stat.S_ISREG(before.st_mode) or identity[2:] != [0, 0, mode, 1]
            or expected is not None and identity != expected or before.st_size > LIMIT):
        raise ProfileError("file-ownership-or-identity-drift")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        if _stamp(os.fstat(fd)) != identity:
            raise ProfileError("file-replaced")
        data = os.read(fd, LIMIT + 1)
        after = os.fstat(fd)
        if (len(data) > LIMIT or after.st_size != len(data)
                or after.st_mtime_ns != before.st_mtime_ns or _stamp(path.lstat()) != identity):
            raise ProfileError("file-changed")
        return data, identity
    finally:
        os.close(fd)


def _new(path: Path, mode: int) -> int:
    _directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), mode)
    try:
        os.fchmod(fd, mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _flush_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    while data:
        written = os.write(fd, data)
        if not written:
            raise ProfileError("short-write")
        data = data[written:]
    os.fsync(fd)


def _save(state: Path, record: dict) -> None:
    if ([_directory(STATE_ROOT, True), _directory(state, True)] != record["directories"]
            or _file(state / "lock", 0o600, record["lock_identity"])[1] != record["lock_identity"]):
        raise ProfileError("state-replaced")
    target, candidate = state / "receipt.json", state / "receipt.next"
    if target.exists() or target.is_symlink():
        _file(target, 0o600, record.get("receipt_identity", []))
    fd = _new(candidate, 0o600)
    try:
        record["receipt_identity"] = _stamp(os.fstat(fd))
        _write_all(fd, json.dumps(record, sort_keys=True).encode())
    finally:
        os.close(fd)
    _directory(state, private=True)
    os.replace(candidate, target)
    _flush_directory(state)


def _profile(digest: str) -> Path:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProfileError("invalid-saved-profile-digest")
    return PROFILE_ROOT / (PROFILE_NAME + "." + digest)


@contextmanager
def _locked(create: bool):
    import fcntl

    state = STATE_ROOT / PROFILE_NAME
    fresh = False
    if state.is_symlink():
        raise ProfileError("linked-state")
    if not state.exists():
        if not create:
            for member in (*reversed(STATE_ROOT.parents), STATE_ROOT):
                if member.exists() or member.is_symlink():
                    _directory(member, private=member == STATE_ROOT)
            yield state, None
            return
        for member in (*reversed(STATE_ROOT.parents), STATE_ROOT):
            if not member.exists() and not member.is_symlink():
                member.mkdir(mode=0o700)
                _flush_directory(member.parent)
            _directory(member)
        _directory(STATE_ROOT, private=True)
        state.mkdir(mode=0o700)
        _flush_directory(STATE_ROOT)
        fd = _new(state / "lock", 0o600)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _flush_directory(state)
        fresh = True
    roots = [_directory(STATE_ROOT, private=True), _directory(state, private=True)]
    _, lock_id = _file(state / "lock", 0o600)
    fd = os.open(state / "lock", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if _stamp(os.fstat(fd)) != lock_id:
            raise ProfileError("lock-replaced")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state / "receipt.next").exists() or (state / "receipt.next").is_symlink():
            raise ProfileError("interrupted-receipt-publication")
        receipt = state / "receipt.json"
        if receipt.exists() or receipt.is_symlink():
            data, identity = _file(receipt, 0o600)
            record = json.loads(data)
            if (not isinstance(record, dict) or type(record.get("schema")) is not int
                    or record.get("schema") != 1 or record.get("name") != PROFILE_NAME
                    or record.get("receipt_identity") != identity
                    or record.get("directories") != roots or record.get("lock_identity") != lock_id):
                raise ProfileError("unattributed-state")
        elif fresh:
            record = None
        else:
            raise ProfileError("ambiguous-state-without-receipt")
        yield state, record
    finally:
        os.close(fd)


def _kernel() -> str | None:
    with KERNEL_PROFILES.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ProfileError("kernel-inspection-overflow")
    found = []
    for line in data.decode("utf-8").splitlines():
        if line.startswith(PROFILE_NAME + "//"):
            raise ProfileError("foreign-nested-profile")
        if line.startswith(PROFILE_NAME + " ") or line == PROFILE_NAME:
            if line not in (PROFILE_NAME + " (enforce)", PROFILE_NAME + " (complain)"):
                raise ProfileError("kernel-inspection-uncertain")
            found.append(line.rsplit(" (", 1)[1][:-1])
    if len(found) > 1:
        raise ProfileError("ambiguous-kernel-profile")
    return found[0] if found else None


def _unused() -> None:
    entries = list(PROC.iterdir())
    if len(entries) > 131072:
        raise ProfileError("process-inspection-overflow")
    inspected = 0
    for entry in entries:
        if entry.name.isdecimal():
            try:
                tasks = [task for task in (entry / "task").iterdir() if task.name.isdecimal()]
            except FileNotFoundError:
                if not entry.exists():
                    continue
                raise
            inspected += len(tasks)
            if inspected > 131072:
                raise ProfileError("process-inspection-overflow")
            if not tasks:
                raise ProfileError("process-inspection-uncertain")
            for task in tasks:
                try:
                    with (task / "attr/current").open("rb") as stream:
                        label = stream.read(4097)
                except FileNotFoundError:
                    if not task.exists():
                        continue
                    raise
                if len(label) > 4096 or not label.strip() or b"\0" in label:
                    raise ProfileError("process-inspection-uncertain")
                tokens = re.split(r"[\s/:&]+", label.decode("utf-8"))
                if PROFILE_NAME in tokens:
                    raise ProfileError("profile-in-use")


def _parser_custody(process=None):
    if (not all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT"))
            or not hasattr(signal, "SIGCHLD") or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL):
        raise ProfileError("parser-custody-unavailable-outcome-unknown")
    if process is None:
        return None
    if process.returncode is not None:
        raise ProfileError("parser-custody-released-outcome-unknown")
    try:
        status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except OSError as error:
        raise ProfileError("parser-custody-unconfirmed-outcome-unknown") from error
    if status is not None and status.si_pid != process.pid:
        raise ProfileError("parser-identity-changed-outcome-unknown")
    return status


def _parser(action: str, path: Path) -> None:
    import selectors

    _parser_custody()
    executable = Path(PARSER)
    _directory(executable.parent)
    info = executable.lstat()
    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
            or info.st_mode & 0o022 or info.st_nlink != 1):
        raise ProfileError("unsafe-parser-executable")
    flags = ["--jobs=1", "--skip-cache"]
    flags += ["--skip-kernel-load"] if action == "validate" else ["--" + action]
    captured, deadline = bytearray(), time.monotonic() + 30
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(
            [PARSER, *flags, str(path)], shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except BaseException:
        selector.close()
        raise
    try:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProfileError("parser-outcome-unknown")
            for key, _ in selector.select(min(remaining, 0.25)):
                block = os.read(key.fileobj.fileno(), 4096)
                if not block:
                    selector.unregister(key.fileobj)
                captured.extend(block)
                if len(captured) > LIMIT:
                    raise ProfileError("parser-output-overflow-outcome-unknown")
    finally:
        selector.close()
        try:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                # Exclusive Popen ownership and WNOWAIT pin the leader through group retirement.
                if sig == signal.SIGKILL and _parser_custody(process) is None:
                    term_deadline = time.monotonic() + 0.1
                    while _parser_custody(process) is None and time.monotonic() < term_deadline:
                        continue
                _parser_custody(process)
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
            code = process.wait(timeout=2)
        finally:
            process.stdout.close()
            process.stderr.close()
    if code:
        diagnostic = next((v for k, v in (
            (b"syntax error", "parser-syntax-error"), (b"permission denied", "parser-permission-denied"),
            (b"already exists", "parser-profile-exists"),
        ) if k in captured.lower()), "parser-failed")
        raise ProfileError(diagnostic)


def _owned(record: dict) -> Path:
    identity = record.get("profile_identity")
    if not isinstance(identity, list) or len(identity) != 6 or identity[2:] != [0, 0, 0o444, 1]:
        raise ProfileError("invalid-saved-profile-identity")
    if _directory(PROFILE_ROOT) != record["profile_directory"]:
        raise ProfileError("profile-directory-drift")
    path = _profile(record["digest"])
    data, _ = _file(path, 0o444, record["profile_identity"])
    if hashlib.sha256(data).hexdigest() != record["digest"]:
        raise ProfileError("profile-content-drift")
    return path


def _vacant() -> None:
    _directory(PROFILE_ROOT)
    plain = PROFILE_ROOT / PROFILE_NAME
    if (_kernel() is not None or any(PROFILE_ROOT.glob(PROFILE_NAME + ".*"))
            or plain.exists() or plain.is_symlink()):
        raise ProfileError("foreign-existing-profile")


def operate(action: str, *, activation_quiesced=False) -> dict:
    _root()
    if action not in ("install", "status", "uninstall"):
        raise ProfileError("unsupported-action")
    if action == "uninstall" and activation_quiesced is not True:
        raise ProfileError("activation-quiescence-assertion-required")
    if action == "install" and not (STATE_ROOT / PROFILE_NAME).exists():
        _vacant()
    with _locked(action == "install") as (state, record):
        body = profile_bytes()
        current = hashlib.sha256(body).hexdigest()
        if record is None and action != "install":
            _vacant()
            return {"state": "absent", "name": PROFILE_NAME}
        if record is not None:
            if record["phase"] == "retired":
                _vacant()
                if action != "install":
                    return {"state": "retired", "name": PROFILE_NAME}
            elif record["phase"] in ("ready", "prepared", "validation-failed"):
                path = _owned(record)
                expected = "enforce" if record["phase"] == "ready" else None
                if _kernel() != expected:
                    raise ProfileError("kernel-state-drift")
                if action == "status" or action == "install":
                    state_name = "ready" if record["phase"] == "ready" else "blocked-prepared"
                    if record["digest"] != current:
                        state_name = "template-drift"
                    return {"state": state_name, "name": PROFILE_NAME}
            else:
                raise ProfileError("pending-outcome-requires-owner-reconciliation")
        if action == "install":
            _vacant()
            previous = record.get("receipt_identity") if record else None
            record = {"schema": 1, "name": PROFILE_NAME, "phase": "preparing",
                      "operation": uuid.uuid4().hex, "digest": current,
                      "directories": [_directory(STATE_ROOT, True), _directory(state, True)],
                      "profile_directory": _directory(PROFILE_ROOT),
                      "lock_identity": _file(state / "lock", 0o600)[1]}
            if previous is not None:
                record["receipt_identity"] = previous
            _save(state, record)
            path = _profile(current)
            fd = _new(path, 0o444)
            try:
                _write_all(fd, body)
                record["profile_identity"] = _stamp(os.fstat(fd))
            finally:
                os.close(fd)
            _flush_directory(PROFILE_ROOT)
            record["phase"] = "prepared"
            _save(state, record)
            try:
                _parser("validate", _owned(record))
            except (ProfileError, OSError, subprocess.SubprocessError):
                record["phase"] = "validation-failed"
                _save(state, record)
                raise
            if _kernel() is not None:
                raise ProfileError("kernel-name-became-occupied")
            record["phase"] = "load-pending"
            _save(state, record)
            _parser("add", _owned(record))
            if _kernel() != "enforce":
                raise ProfileError("load-outcome-unknown")
            record["phase"] = "ready"
        else:
            _unused()
            record["activation_quiesced"] = True
            record["phase"] = "unload-pending"
            _save(state, record)
            if expected is not None:
                _parser("remove", _owned(record))
            if _kernel() is not None:
                raise ProfileError("unload-outcome-unknown")
            record["phase"] = "unloaded-cleanup-pending"
            _save(state, record)
            _owned(record).unlink()
            _flush_directory(PROFILE_ROOT)
            record["phase"] = "retired"
        _save(state, record)
        return {"state": record["phase"], "name": PROFILE_NAME}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "status", "uninstall"))
    parser.add_argument("--activation-quiesced", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = operate(args.action, activation_quiesced=args.activation_quiesced)
    except (ProfileError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, ProfileError) else "inspection-or-effect-unavailable"
        print(json.dumps({"state": "blocked", "name": PROFILE_NAME, "reason": reason}))
        return 2
    print(json.dumps(result))
    return 0 if result["state"] in ("ready", "absent", "retired") else 2


if __name__ == "__main__":
    raise SystemExit(main())
