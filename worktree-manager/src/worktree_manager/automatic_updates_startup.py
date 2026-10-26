"""Durable, stdlib-only entry point until a completed Manager slot is selected."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

_VERSION = re.compile(r"[0-9][A-Za-z0-9.+_-]{0,127}")
AUTOSTART_SCHEMA = "worktree-manager.automatic-updates-autostart"
AUTOSTART_FIELDS = frozenset({
    "schema", "version", "root", "platform", "name", "bootstrap", "entry",
})
_KEY_FILES = (
    ".install-complete.json",
    "pyvenv.cfg",
    "payload/pyproject.toml",
    "payload/src/worktree_manager/__init__.py",
    "payload/src/worktree_manager/__main__.py",
    ".runtime-bootstrap/runtime_launcher.py",
    ".runtime-bootstrap/versioned_runtime.py",
    ".runtime-bootstrap/versioned_runtime_leases.py",
    ".runtime-bootstrap/versioned_runtime_source.py",
    ".runtime-bootstrap/versioned_runtime_construction.py",
    ".runtime-bootstrap/versioned_runtime_publication.py",
)


def checked_path(path: Path, *, directory: bool = False) -> Path:
    """Reject linked/reparsed components before using an installation artifact."""
    absolute = path.absolute()
    for part in reversed((absolute, *absolute.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"linked or reparsed startup path: {part}")
    info = absolute.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError(f"unexpected startup path type: {absolute}")
    return absolute


def installation_name(root: Path) -> str:
    identity = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()[:24]
    return f"worktree-manager-updates-{identity}"


def resolve_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return checked_path(explicit, directory=True).resolve(strict=True)
    script = checked_path(Path(__file__))
    if script.name != "startup.py" or script.parent.name != "automatic-updates":
        raise ValueError("explicit --root is required outside an installation-owned startup.py")
    root = checked_path(script.parent.parent, directory=True).resolve(strict=True)
    for parent in (root, *root.parents):
        try:
            (parent / ".git").lstat()
        except FileNotFoundError:
            continue
        raise ValueError("implicit startup root cannot be a source checkout or worktree")
    receipt = checked_path(script.parent / "autostart.json")
    owner = json.loads(receipt.read_text(encoding="utf-8"))
    if (
        not isinstance(owner, dict) or set(owner) != AUTOSTART_FIELDS
        or owner.get("schema") != AUTOSTART_SCHEMA
        or type(owner.get("version")) is not int or owner["version"] != 1
        or owner.get("root") != str(root)
        or owner.get("name") != installation_name(root)
        or owner.get("platform") not in {"windows", "linux", "wsl"}
        or not isinstance(owner.get("entry"), str)
        or owner.get("bootstrap") != hashlib.sha256(script.read_bytes()).hexdigest()
    ):
        raise ValueError("implicit startup root requires this installation's exact bootstrap receipt")
    return root


def completed_slot(root: Path) -> Path:
    root = checked_path(root, directory=True)
    marker = checked_path(root / "current-version")
    version = marker.read_text(encoding="utf-8").strip()
    if _VERSION.fullmatch(version) is None:
        raise ValueError("invalid Manager current-version selector")
    slot = checked_path(root / "versions" / version, directory=True)
    for relative in _KEY_FILES:
        checked_path(slot / relative)
    completion = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
    if (
        not isinstance(completion, dict) or completion.get("version") != version
        or not isinstance(completion.get("payload_hash"), str)
        or re.fullmatch(r"[0-9a-f]{64}", completion["payload_hash"]) is None
    ):
        raise ValueError("Manager startup requires attributable constructed completion")
    return slot


def retained_command(root: Path, args: list[str], *, windows: bool) -> list[str]:
    slot = completed_slot(root)
    executable = slot / ("Scripts/python.exe" if windows else "bin/python")
    checked_path(executable.parent, directory=True)
    target = checked_path(executable.resolve(strict=True))
    if not windows and not os.access(executable, os.X_OK):
        raise ValueError("selected Manager interpreter is not executable")
    for parent in target.parents:
        try:
            (parent / ".git").lstat()
        except FileNotFoundError:
            continue
        raise ValueError("selected Manager interpreter targets a source checkout")
    return [
        str(executable), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap/runtime_launcher.py"),
        "-m", "worktree_manager", *args,
    ]


def selected_command(root: Path, *, windows: bool) -> list[str]:
    return retained_command(
        root,
        ["automatic-updates", "ensure" if windows else "run", "--root", str(root.absolute())],
        windows=windows,
    )


def startup(root: Path) -> int:
    windows = sys.platform == "win32"
    command = selected_command(root, windows=windows)
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper() in {
            "PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__", "GH_TOKEN", "GITHUB_TOKEN",
            "AGENT_WORKTREES_AHP_AUTH_TOKEN",
            "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_INTERNAL__PYTHONHOME",
        }:
            environment.pop(key)
    environment["WORKTREE_MANAGER_ROOT"] = str(root.absolute())
    if not windows:
        os.chdir(root)
        os.execve(command[0], command, environment)
        raise AssertionError("execve returned")
    # This is a bounded ensure client; ensure owns the canonical durable spawn.
    log_path = checked_path(root / "automatic-updates", directory=True) / "startup.log"
    if log_path.exists() or log_path.is_symlink():
        checked_path(log_path)
    with log_path.open("ab") as output:
        return subprocess.run(
            command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, timeout=90, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).returncode


def process_snapshot(pid: int) -> tuple[int, int, tuple[str, ...], tuple[str, ...]]:
    """Kernel start identity, parent, argv and systemd/unified cgroup membership."""
    root = Path("/proc") / str(pid)
    raw = (root / "stat").read_text(encoding="utf-8")
    fields = raw[raw.rfind(")") + 2:].split()
    parent, started = int(fields[1]), int(fields[19])
    with (root / "cmdline").open("rb") as stream:
        command = stream.read(65537)
    if len(command) > 65536 or not command.endswith(b"\0"):
        raise ValueError("invalid or excessive process command evidence")
    argv = tuple(os.fsdecode(item) for item in command[:-1].split(b"\0"))
    groups = []
    for line in (root / "cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, path = line.split(":", 2)
        if hierarchy == "0" or "name=systemd" in controllers.split(","):
            groups.append(path)
    return started, parent, argv, tuple(groups)


def systemd_process_owner(root: Path, pid: int, group: str) -> bool:
    if pid <= 0 or not group.startswith("/") or ".." in group.split("/"):
        return False
    current = os.getpid()
    main = process_snapshot(pid)
    worker = process_snapshot(current)
    for snapshot in (main, worker):
        if not any(path == group or path.startswith(group + "/") for path in snapshot[3]):
            return False
    tail = ("-m", "worktree_manager", "automatic-updates", "run", "--root", str(root))
    if worker[2][-len(tail):] != tail:
        return False
    command = main[2]
    bootstrap = ("-I", "-S", str(root / "automatic-updates/startup.py"), "--root", str(root))
    direct_worker = pid == current and command[1:] == tail
    if command[1:] != bootstrap and not direct_worker:
        if (
            pid != current
            or command[1:6] != ("-I", "-S", "-B", "-X", "utf8")
            or len(command) != 13
            or command[7:] != tail
        ):
            return False
        launcher = checked_path(Path(command[6]))
        slot = checked_path(launcher.parent.parent, directory=True)
        if slot.parent != root / "versions" or _VERSION.fullmatch(slot.name) is None:
            return False
        if launcher != slot / ".runtime-bootstrap/runtime_launcher.py":
            return False
        for relative in _KEY_FILES:
            checked_path(slot / relative)
    ancestor = current
    for _ in range(64):
        if ancestor == pid:
            break
        snapshot = process_snapshot(ancestor)
        if snapshot[1] <= 0 or snapshot[1] == ancestor or snapshot[0] < main[0]:
            return False
        ancestor = snapshot[1]
    else:
        return False
    return main == process_snapshot(pid) and worker == process_snapshot(current)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    try:
        root = resolve_root(args.root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        return startup(root)
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
        directory = checked_path(root / "automatic-updates", directory=True)
        log = directory / "startup.log"
        if log.exists() or log.is_symlink():
            checked_path(log)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"Automatic update startup failed: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
