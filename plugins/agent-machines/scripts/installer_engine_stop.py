"""Linux native STOP admission bound to original kernel birth and command bytes.

The native installer owns service control. This finite helper never discovers a
replacement identity during STOP, never signals by numeric PID, and never forces
an unrelated process or a descendant to exit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import select
import shlex
import signal
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class StopRefused(RuntimeError):
    """A daemon's admitted identity or kernel signaling capability is unverified."""


@dataclass(frozen=True)
class Identity:
    pid: int
    birth: int
    command_digest: str
    uid: int


def _posix(value: str | Path) -> PurePosixPath:
    text = str(value).replace("\\", "/")
    if os.name == "nt":
        text = re.sub(r"^([A-Za-z]):/", lambda match: "/" + match[1].lower() + "/", text)
    return PurePosixPath(text)


def _runtime_path(value: str, root: str | Path, names: set[str]) -> bool:
    path, base = _posix(value), _posix(root)
    if not path.is_absolute() or ".." in path.parts:
        return False
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return False
    historical = {"venv", ".venv"}
    if len(parts) == 3 and parts[0] in historical:
        return parts[1] == "bin" and parts[2] in names
    return (
        len(parts) == 4 and parts[0] == "versions"
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", parts[1]) is not None
        and not parts[1].endswith(".") and parts[2] == "bin" and parts[3] in names
    )


def admits_argv(argv: list[str], root: str | Path, plugin: str) -> bool:
    """Recognize the actual native unit and shebang forms, not a launch path."""
    if not argv:
        return False
    python = {"python", "python3"}
    if plugin == "agent-bridge":
        if _runtime_path(argv[0], root, {"agent-bridge"}) and argv[1:] == ["start"]:
            return True
        if (len(argv) >= 3 and re.fullmatch(r"python(?:[0-9.]+)?", _posix(argv[0]).name)
                and _runtime_path(argv[1], root, {"agent-bridge"})):
            return argv[2:] == ["start"]
    if not _runtime_path(argv[0], root, python):
        return False
    tail = argv[1:]
    while tail and tail[0] in {"-I", "-B", "-S"}:
        tail = tail[1:]
    if len(tail) < 2 or tail[0] != "-m" or tail[1] != plugin.replace("-", "_") + (
        ".service" if plugin == "agent-vault" else ""
    ):
        return False
    tail = tail[2:]
    if plugin == "agent-bridge":
        return tail in (["start"], ["service", "start", "--direct"])
    if plugin == "agent-vault":
        allowed = {"--foreground", "--persistent", "--handoff-stdin"}
        return len(set(tail)) == len(tail) and all(value in allowed for value in tail)
    if plugin == "agent-dispatch":
        if tail[:1] == ["--local"]:
            tail = tail[1:]
        if not tail:
            return False
        if tail[0] == "serve":
            return True
        if tail[0] == "supervise":
            return len(tail) == 1 or tail[1] == "serve" or tail[1].startswith("-")
        return len(tail) >= 2 and tail[0] in {
            "emitter", "schedule", "evaluator", "lane", "webhook",
        } and tail[1] == "serve"
    return False


def _owns_unit_argv(argv: list[str], unit: Path, root: str | Path, plugin: str) -> bool:
    owned = admits_argv(argv, root, plugin)
    if plugin == "agent-dispatch":
        coordinator = (
            bool(argv) and _runtime_path(argv[0], root, {"python", "python3"})
            and argv[1:] == ["-m", "agent_dispatch", "serve"]
        )
        supervisor = (
            len(argv) == 3 and _posix(argv[0]) == _posix(root) / "supervise-service.sh"
            and argv[1:] == ["--systemd-unit", unit.name]
        )
        owned = coordinator or supervisor
    return owned


def check_unit(unit: Path, root: str | Path, plugin: str) -> None:
    """Validate the installed registration against the same argv admission."""
    if not _posix(root).is_absolute() or ".." in _posix(root).parts:
        raise StopRefused("Service ownership requires an absolute root without traversal")
    if unit.is_symlink() or not unit.is_file():
        raise StopRefused("The service unit must be an ordinary file")
    lines = []
    section = ""
    for raw in unit.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line
        elif section == "[Service]":
            lines.append(line)
    starts = [line.removeprefix("ExecStart=") for line in lines if line.startswith("ExecStart=")]
    if len(starts) != 1 or not _owns_unit_argv(shlex.split(starts[0]), unit, root, plugin):
        raise StopRefused("The service unit does not name this installation's daemon")
    directories = [line.removeprefix("WorkingDirectory=") for line in lines
                   if line.startswith("WorkingDirectory=")]
    if len(directories) != 1 or _posix(directories[0]) != _posix(root):
        raise StopRefused("The service unit working directory names another root")


def check_effective_unit(unit: Path, root: str | Path, plugin: str, properties: str) -> str:
    """Check one manager property snapshot; never expose its potentially secret argv."""
    check_unit(unit, root, plugin)
    required = {"LoadState", "ActiveState", "FragmentPath", "DropInPaths", "ExecStart", "WorkingDirectory"}
    values = {}
    for line in properties.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in required or key in values:
            raise StopRefused("Ambiguous effective service properties")
        values[key] = value
    if values.keys() != required:
        raise StopRefused("Incomplete effective service properties")
    if values["DropInPaths"]:
        raise StopRefused("Service drop-in overrides are unsupported for native uninstall")
    if values["LoadState"] == "not-found" and values["ActiveState"] == "inactive":
        if any(values[key] for key in ("FragmentPath", "ExecStart", "WorkingDirectory")):
            raise StopRefused("An unloaded service retains ambiguous effective registration")
        return "unloaded"
    if (values["LoadState"] != "loaded" or values["ActiveState"] not in {
        "active", "inactive", "failed", "activating", "deactivating", "reloading",
    }):
        raise StopRefused("Effective service load/activity state is unverified")
    if any("\\" in values[key] for key in ("FragmentPath", "WorkingDirectory", "ExecStart")):
        raise StopRefused("Escaped effective service properties are unsupported")
    if _posix(values["FragmentPath"]) != _posix(unit):
        raise StopRefused("The effective service fragment names another unit")
    if _posix(values["WorkingDirectory"]) != _posix(root):
        raise StopRefused("The effective service working directory names another root")
    # systemctl show serializes ExecStart's command array as brace-delimited
    # records. The native writers use one simple command; multiple commands,
    # escaped arguments and unknown shapes are deliberately not inferred.
    command = re.fullmatch(
        r"\{\s*path=([^;{}\r\n]+?)\s*;\s*argv\[\]=([^;{}\r\n]+?)\s*;"
        r"\s*ignore_errors=(?:yes|no)\s*;[^{}\r\n]*\}",
        values["ExecStart"],
    )
    if command is None:
        raise StopRefused("Unsupported effective service command array")
    argv = shlex.split(command[2])
    if (not argv or _posix(command[1]) != _posix(argv[0])
            or not _owns_unit_argv(argv, unit, root, plugin)):
        raise StopRefused("The effective service command names another daemon")
    return "loaded"


def _root(value: str) -> Path:
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        raise StopRefused("Kernel-bound native STOP requires Linux /proc and pidfds")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts:
        raise StopRefused("Daemon STOP requires an absolute root without traversal")
    if root == Path(root.anchor) or root == Path.home() or root in Path.home().parents:
        raise StopRefused("Daemon STOP refuses a filesystem or home root")
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise StopRefused("Daemon STOP refuses an indirect root")
    return root


def _boot() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9a-f-]{36}", value) is None:
        raise StopRefused("The kernel boot identity is unavailable")
    return value


def _stat(pid: int) -> tuple[int, int, str]:
    directory = Path("/proc") / str(pid)
    text = (directory / "stat").read_text(errors="surrogateescape")
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) < 20:
        raise StopRefused("Incomplete process birth inspection")
    return int(fields[19]), int(fields[1]), fields[0]


ProcessRecord = tuple[Identity, int, tuple[str, ...]]


def _observe(pid: int) -> ProcessRecord | None:
    try:
        before, parent, state = _stat(pid)
        if state == "Z":
            return None
        directory = Path("/proc") / str(pid)
        command = (directory / "cmdline").read_bytes()
        uid = directory.stat().st_uid
        after, later_parent, later_state = _stat(pid)
        if before != after:
            raise StopRefused(f"Process identity changed during original admission (pid={pid})")
        if later_state == "Z" or not command:
            return None
        identity = Identity(pid, before, hashlib.sha256(command).hexdigest(), uid)
        argv = tuple(os.fsdecode(value) for value in command.split(b"\0") if value)
        return identity, later_parent or parent, argv
    except (FileNotFoundError, ProcessLookupError):
        return None


def _inventory() -> dict[int, ProcessRecord]:
    result = {}
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            observed = _observe(int(path.name))
            if observed is not None:
                result[int(path.name)] = observed
    return result


def _children(rows: dict[int, ProcessRecord], selected: set[int]) -> None:
    for pid, (_, parent, _) in rows.items():
        if parent in selected and pid not in selected:
            raise StopRefused(f"An admitted daemon owns an unclassified child or transport (pid={pid})")


def capture(root: Path, plugin: str, pid_file: Path | None = None) -> dict[str, Any]:
    """Capture once, before service STOP, with exact kernel start ticks/argv."""
    rows = _inventory()
    selected = {}
    for pid, (identity, _, arguments) in rows.items():
        if identity.uid != os.getuid():
            continue
        argv = list(arguments)
        if admits_argv(argv, root, plugin):
            selected[pid] = identity
        elif argv and _posix(argv[0]).is_relative_to(_posix(root)):
            raise StopRefused(f"An unclassified runtime-rooted process is still active (pid={pid})")
    _children(rows, set(selected))
    if pid_file is not None and pid_file.exists():
        if (not pid_file.is_absolute() or ".." in pid_file.parts
                or not pid_file.is_relative_to(root)
                or any(path.is_symlink() for path in (pid_file, *pid_file.parents))):
            raise StopRefused("Unvalidated daemon PID receipt")
        value = pid_file.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[1-9][0-9]*", value) is None:
            raise StopRefused("Invalid daemon PID receipt")
        if int(value) in rows and int(value) not in selected:
            raise StopRefused("The PID receipt names an unowned process")
    return {"version": 1, "root": str(root), "plugin": plugin, "boot": _boot(),
            "identities": [asdict(value) for value in selected.values()]}


def stop(root: Path, plugin: str, admission: dict[str, Any], timeout: float = 15) -> None:
    """Open pidfds and compare against ORIGINAL admission before any signal."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise StopRefused("Native STOP requires a finite positive settlement budget")
    if (set(admission) != {"version", "root", "plugin", "boot", "identities"}
            or type(admission["version"]) is not int or admission["version"] != 1
            or admission["root"] != str(root) or admission["plugin"] != plugin
            or admission["boot"] != _boot() or not isinstance(admission["identities"], list)):
        raise StopRefused("Invalid or stale original daemon admission")
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if not callable(opener) or not callable(sender):
        raise StopRefused("Kernel-bound signaling is unavailable; PID-only STOP is forbidden")
    records = []
    for value in admission["identities"]:
        if (not isinstance(value, dict) or set(value) != {"pid", "birth", "command_digest", "uid"}
                or any(type(value[key]) is not int or value[key] < 0 for key in ("pid", "birth", "uid"))
                or not 0 < value["pid"] < 2**31 or not isinstance(value["command_digest"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["command_digest"])):
            raise StopRefused("Malformed original process identity")
        identity = Identity(**value)
        if identity.uid != os.getuid():
            raise StopRefused("Original admission does not own this daemon")
        records.append(identity)
    if len({value.pid for value in records}) != len(records):
        raise StopRefused("Duplicate original process admission")
    opened = []
    try:
        for original in records:
            try:
                descriptor = opener(original.pid, 0)
            except ProcessLookupError:
                continue
            opened.append((original, descriptor))
            current = _observe(original.pid)
            if current is None:
                continue
            if current[0] != original:
                raise StopRefused(f"Originally admitted daemon was replaced; refusing STOP (pid={original.pid})")
            if not admits_argv(list(current[2]), root, plugin):
                raise StopRefused("The admitted command does not own this daemon")
        _children(_inventory(), {value.pid for value in records})
        for original, descriptor in opened:
            current = _observe(original.pid)
            if current is None:
                continue
            if current[0] != original:
                raise StopRefused(f"Originally admitted daemon changed before signaling (pid={original.pid})")
            if not admits_argv(list(current[2]), root, plugin):
                raise StopRefused("The admitted command does not own this daemon")
            try:
                sender(descriptor, signal.SIGTERM, None, 0)
            except ProcessLookupError:
                continue
            if not select.select([descriptor], [], [], timeout)[0]:
                raise StopRefused("The owned daemon did not settle after STOP")
    finally:
        for _, descriptor in opened:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "pids", "check-unit", "check-effective-unit", "stop"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--plugin", required=True, choices=("agent-bridge", "agent-dispatch", "agent-vault"))
    parser.add_argument("--admission")
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--unit", type=Path)
    args = parser.parse_args()
    try:
        if args.action in {"check-unit", "check-effective-unit"}:
            if args.unit is None:
                raise StopRefused("Service unit inspection requires its exact path")
            if args.action == "check-effective-unit":
                print(check_effective_unit(args.unit, args.root, args.plugin, sys.stdin.read()))
            else:
                check_unit(args.unit, args.root, args.plugin)
        elif args.action == "pids":
            admission = json.loads(args.admission)
            for identity in admission["identities"]:
                print(identity["pid"])
        else:
            root = _root(args.root)
            if args.action == "capture":
                print(json.dumps(capture(root, args.plugin, args.pid_file), sort_keys=True))
            else:
                stop(root, args.plugin, json.loads(args.admission))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        print(f"Native daemon STOP refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
