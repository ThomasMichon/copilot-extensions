"""Host-side "is this CodeSpace actually in use?" probe.

A lease record's ``pid`` is the process that *wrote* the lease (usually a
short-lived CLI invocation), so "lease pid is dead" is NOT evidence that a box
is free: an SSH ControlMaster started with ``ControlPersist=yes`` deliberately
outlives the process that spawned it, and a daemon-spawned ``agent-codespaces
ssh --stdio`` session, a port-forward carrier, a mux client, or an interactive
``gh codespace ssh`` can all still be riding the box.

This module derives the set of **live local users** of a CodeSpace from:

* the ``ssh_manager`` per-target lock holder (when its pid is alive), and
* the local process table: every ``ssh`` whose ``-F`` config is one of this
  CodeSpace's generated config files (the ``ssh-manager`` default directory or
  agent-codespaces' own), classified as control master,
  port-forward carrier, mux client, or plain session), and every
  ``gh codespace|cs ssh|ports|cp -c <name>`` process that is not merely the
  ProxyCommand child of an ``ssh`` already counted.

It owns no state and never mutates anything. When the process table cannot be
read, only the lock holder is known and "no users" is reported as **unknown**
(never as idle). The lifecycle commands (``stop`` / ``finalize`` / ``delete`` /
``prune``) refuse a box with live users unless ``--force`` (see
:class:`CodespaceInUseError`).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ssh_manager import TargetBusyError
from ssh_manager.locks import LockHolder

ROLE_LOCK = "ssh-target-lock"
ROLE_CONTROL_MASTER = "ssh-control-master"
ROLE_FORWARD = "ssh-port-forward"
ROLE_MUX = "ssh-mux-client"
ROLE_SSH = "ssh-session"
ROLE_GH = "gh-codespace-session"

_GH_SUBCOMMANDS = {"ssh", "ports", "cp", "code", "logs", "jupyter"}


@dataclass(frozen=True)
class ProcInfo:
    """One local process: pid, parent pid, and argv.

    ``raw`` is the unsplit command line when argv boundaries could not be
    recovered (the ``ps`` fallback), so paths containing spaces still match.
    """

    pid: int
    ppid: int
    argv: tuple[str, ...]
    raw: str = ""


@dataclass(frozen=True)
class LiveUser:
    """A live local process using a CodeSpace."""

    pid: int
    role: str
    command: str
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        extra = f" ({self.detail})" if self.detail else ""
        return f"pid {self.pid} [{self.role}]{extra}: {self.command}"


def config_file_name(name: str) -> str:
    """The ssh-manager per-CodeSpace ``-F`` config file name for ``name``.

    Mirrors ``ssh_manager.codespace_source.CodespaceConfigSource``.
    """
    return re.sub(r"[^\w\-.]", "_", name) + ".config"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def config_paths(name: str) -> frozenset[str]:
    """Normalized paths of every generated ``-F`` config for ``name``: the
    ssh-manager default directory (agent-bridge) and agent-codespaces' own."""
    return frozenset(_norm(p) for p in _raw_config_paths(name))


def _raw_config_paths(name: str) -> tuple[str, ...]:
    from .codespace_config import SSH_CONFIG_DIR

    dirs = (Path.home() / ".ssh-manager" / "codespace-config", SSH_CONFIG_DIR)
    return tuple(str(d / config_file_name(name)) for d in dirs)


def _owned_by_other_user(pid: str) -> bool:
    try:
        return os.stat(f"/proc/{pid}").st_uid != os.getuid()
    except FileNotFoundError:
        return True  # vanished
    except OSError:
        return False


def _read_proc_linux() -> list[ProcInfo] | None:
    procs: list[ProcInfo] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                raw = fh.read()
            with open(f"/proc/{entry}/stat", encoding="ascii", errors="replace") as fh:
                stat = fh.read()
        except (FileNotFoundError, ProcessLookupError):
            continue  # exited mid-scan: a harmless race
        except OSError:
            if _owned_by_other_user(entry):
                continue  # another user's process cannot ride our ssh configs
            return None  # one of OUR processes is unreadable: census unknown
        argv = tuple(p.decode(errors="replace") for p in raw.split(b"\0") if p)
        if not argv:
            continue
        try:
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (IndexError, ValueError):
            ppid = 0
        procs.append(ProcInfo(int(entry), ppid, argv))
    return procs


def _read_proc_ps() -> list[ProcInfo] | None:
    try:
        result = subprocess.run(
            ["ps", "-axww", "-o", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    procs: list[ProcInfo] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            procs.append(ProcInfo(int(parts[0]), int(parts[1]), tuple(parts[2].split()),
                                  raw=parts[2].strip()))
        except ValueError:
            continue
    return procs


def _split_windows_cmdline(cmdline: str) -> tuple[str, ...]:
    try:
        parts = shlex.split(cmdline, posix=False)
    except ValueError:
        parts = cmdline.split()
    return tuple(p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts)


def _read_proc_windows() -> list[ProcInfo] | None:
    from agent_procutil import no_window_flags

    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,"
              "CommandLine | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, check=False,
            stdin=subprocess.DEVNULL, creationflags=no_window_flags(),
        )
        rows = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return None
    procs: list[ProcInfo] = []
    for row in rows:
        cmdline = (row or {}).get("CommandLine") or ""
        try:
            pid, ppid = int(row["ProcessId"]), int(row.get("ParentProcessId") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if cmdline:
            procs.append(ProcInfo(pid, ppid, _split_windows_cmdline(cmdline)))
    return procs


def process_table() -> list[ProcInfo] | None:
    """Snapshot the local process table, or ``None`` when unavailable."""
    if sys.platform == "win32":
        return _read_proc_windows()
    if os.path.isdir("/proc/self"):
        return _read_proc_linux()
    return _read_proc_ps()


def _basename(arg: str) -> str:
    base = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _option_value(argv: tuple[str, ...], *flags: str) -> str | None:
    """Value of the first of ``flags`` in ``argv`` (``-f v`` or ``-f=v``)."""
    for i, arg in enumerate(argv[1:], start=1):
        if arg in flags:
            return argv[i + 1] if i + 1 < len(argv) else None
        for flag in flags:
            if arg.startswith(flag + "="):
                return arg[len(flag) + 1:]
    return None


# ssh options that take a value (``man ssh``). Parsing follows getopt: flags may
# be bundled (``-NT``); a value flag takes the rest of its token (``-oX=y``,
# ``-F/path``) or, when last in its token, the next argv element. The first
# non-option is the destination; everything after it is the remote command,
# which is never parsed as options and never rendered (it may carry secrets).
_SSH_VALUE_OPTS = frozenset("BbcDEeFIiJLlmOoPpQRSWw")


@dataclass(frozen=True)
class SshArgs:
    flags: frozenset[str]
    values: tuple[tuple[str, str], ...]
    head_end: int  # argv index just past the destination (or len(argv))
    has_remote_command: bool

    def value(self, flag: str) -> str | None:
        for f, v in self.values:
            if f == flag:
                return v
        return None

    def options(self) -> dict[str, str]:
        opts: dict[str, str] = {}
        for f, v in self.values:
            if f == "o" and "=" in v:
                key, _, value = v.partition("=")
                opts[key.strip().lower()] = value.strip()
        return opts


def parse_ssh(argv: tuple[str, ...]) -> SshArgs:
    flags: set[str] = set()
    values: list[tuple[str, str]] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or len(arg) < 2:
            break  # the destination
        j = 1
        while j < len(arg):
            c = arg[j]
            if c in _SSH_VALUE_OPTS:
                if j + 1 < len(arg):
                    values.append((c, arg[j + 1:]))
                elif i + 1 < len(argv):
                    i += 1
                    values.append((c, argv[i]))
                break
            flags.add(c)
            j += 1
        i += 1
    head_end = min(i + 1, len(argv))  # include the destination
    return SshArgs(frozenset(flags), tuple(values), head_end, head_end < len(argv))


def _ssh_role(parsed: SshArgs) -> tuple[str, str] | None:
    """Classify parsed ssh options into (role, detail); None for a transient ``-O``."""
    if parsed.value("O") is not None:
        return None
    opts = parsed.options()
    control_path = opts.get("controlpath", "")
    detail = f"ControlPath={control_path}" if control_path else ""
    if opts.get("controlmaster", "").lower() in ("yes", "auto", "autoask", "ask"):
        return ROLE_CONTROL_MASTER, detail
    if "N" in parsed.flags:
        return ROLE_FORWARD, detail
    if control_path:
        return ROLE_MUX, detail
    return ROLE_SSH, detail


def _gh_codespace_target(argv: tuple[str, ...]) -> str | None:
    """The CodeSpace a ``gh codespace|cs <sub> -c <name>`` argv targets."""
    if len(argv) < 3 or _basename(argv[0]) != "gh":
        return None
    if argv[1] not in ("codespace", "cs") or argv[2] not in _GH_SUBCOMMANDS:
        return None
    return _option_value(argv, "-c", "--codespace")


def _is_ssh(argv: tuple[str, ...]) -> bool:
    return bool(argv) and _basename(argv[0]) == "ssh"


_SECRET_RE = re.compile(
    r"(?i)([A-Z0-9_-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|BEARER|CREDENTIAL|API[_-]?KEY)"
    r"[A-Z0-9_-]*\s*[=:]\s*)[^\s'\";,]+")
_SECRET_FLAG_RE = re.compile(
    r"(?i)^--?[A-Z0-9_-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|BEARER|CREDENTIAL|API[_-]?KEY)"
    r"[A-Z0-9_-]*$")
_OTHER_MAX_TOKENS = 6


def render_command(argv: tuple[str, ...]) -> str:
    """A display-safe command line: never the ssh remote command (it may carry
    a relay token) nor anything after gh's ``--``, other programs truncated, and
    every ``*TOKEN*=`` / ``*SECRET*=``-style assignment redacted."""
    if not argv:
        return ""
    base = _basename(argv[0])
    if base == "ssh":
        parsed = parse_ssh(argv)
        kept, truncated = list(argv[:parsed.head_end]), parsed.has_remote_command
    elif base == "gh" and "--" in argv:
        cut = argv.index("--")
        kept, truncated = list(argv[:cut]), True
    else:
        kept = list(argv[:_OTHER_MAX_TOKENS])
        truncated = len(argv) > _OTHER_MAX_TOKENS
    shown = [
        "<redacted>" if i and _SECRET_FLAG_RE.match(kept[i - 1]) else
        _SECRET_RE.sub(r"\1<redacted>", tok)
        for i, tok in enumerate(kept)
    ]
    text = " ".join(shown)
    return text + (" ..." if truncated else "")


def _rejoin_config_path(proc: ProcInfo, raw_cfgs: tuple[str, ...]) -> tuple[str, ...]:
    """ps fallback: re-tokenize so a config path containing spaces stays one
    argv element (``ps`` output loses argument boundaries)."""
    for cfg in raw_cfgs:
        if proc.raw and " " in cfg and cfg in proc.raw:
            marker = "\0cfg\0"
            return tuple(cfg if t == marker else t
                         for t in proc.raw.replace(cfg, f" {marker} ").split())
    return proc.argv


def users_from_table(
    name: str, table: list[ProcInfo], *, exclude_pids: frozenset[int] = frozenset(),
) -> list[LiveUser]:
    """Live users of ``name`` found in a process-table snapshot."""
    cfgs = config_paths(name)
    raw_cfgs = _raw_config_paths(name)
    by_pid = {p.pid: p for p in table}
    users: list[LiveUser] = []
    ssh_pids: set[int] = set()
    for proc in table:
        if proc.pid in exclude_pids or not _is_ssh(proc.argv):
            continue
        parsed = parse_ssh(_rejoin_config_path(proc, raw_cfgs))
        config = parsed.value("F")
        if not config or _norm(config) not in cfgs:
            continue
        classified = _ssh_role(parsed)
        if classified is None:
            continue
        role, detail = classified
        parent = by_pid.get(proc.ppid)
        if role == ROLE_CONTROL_MASTER and (proc.ppid <= 1 or parent is None):
            detail = (detail + "; " if detail else "") + "detached (spawning process exited)"
        ssh_pids.add(proc.pid)
        users.append(LiveUser(proc.pid, role, render_command(proc.argv), detail))
    for proc in table:
        if proc.pid in exclude_pids or proc.ppid in ssh_pids:
            continue  # the ProxyCommand child of an ssh already counted
        if _gh_codespace_target(proc.argv) == name:
            detail = "stdio carrier (ProxyCommand)" if "--stdio" in proc.argv else ""
            users.append(LiveUser(proc.pid, ROLE_GH, render_command(proc.argv), detail))
    return users


def lock_holder(name: str, table: list[ProcInfo] | None = None) -> LiveUser | None:
    """The live ``ssh_manager`` target-lock holder for ``name`` (not ourselves)."""
    try:
        from ssh_manager import TargetLock
        from ssh_manager.locks import pid_alive

        holder = TargetLock(name).read_holder()
    except Exception:
        return None
    if holder is None or holder.pid == os.getpid() or not pid_alive(holder.pid):
        return None
    command = ""
    for proc in table or ():
        if proc.pid == holder.pid:
            command = render_command(proc.argv)
            break
    detail = f"op={holder.op}, held {holder.age_seconds:.0f}s"
    return LiveUser(holder.pid, ROLE_LOCK, command or "<unknown command>", detail)


def live_users(name: str, *, table: list[ProcInfo] | None = None) -> list[LiveUser]:
    """Every live local user of CodeSpace ``name`` (lock holder first)."""
    if table is None:
        table = process_table()
    users: list[LiveUser] = []
    holder = lock_holder(name, table)
    if holder is not None:
        users.append(holder)
    if table:
        own = frozenset({os.getpid()})
        users.extend(u for u in users_from_table(name, table, exclude_pids=own)
                     if holder is None or u.pid != holder.pid)
    return users


def codespaces_in_use(
    names: list[str], *, table: list[ProcInfo] | None = None,
) -> dict[str, list[LiveUser]]:
    """``{name: users}`` for every name with at least one live user (one scan).

    Returns ``None`` when the process census is unavailable (unknown -- callers
    must not treat that as idle). Never raises: a failure is also ``None``.
    """
    found: dict[str, list[LiveUser]] = {}
    try:
        if table is None:
            table = process_table()
        if table is None:
            return None
        for name in names:
            users = live_users(name, table=table)
            if users:
                found[name] = users
    except Exception:
        return None
    return found


def holder_worktree_gone(worktree_path: str | None) -> bool:
    """True when a #897 **claim**'s owner worktree PATH is positively gone.

    A cheap, host-local check (no subprocess): the host-local ``leases.json``
    only records claims made on THIS host, so the claim's worktree path is local
    -- an absolute path no longer on disk means the owning worktree was
    finalized/pruned while the lease lingered (an **orphaned** lock). Conservative
    (biased toward alive): a non-path/legacy owner (an advisory borrow's effort)
    or an unreadable path is treated alive, so a live hold is never false-flagged,
    and a cross-machine hold (which rides the beacon/L2 overlay, not a local
    lease) is never seen here at all.
    """
    if not worktree_path or not os.path.isabs(worktree_path):
        return False
    try:
        return not os.path.exists(worktree_path)
    except OSError:
        return False



def claim_orphaned(worktree_path: str | None, users: list[LiveUser] | None) -> bool:
    """A claim is orphaned only when its owner worktree is gone AND nothing local
    is still using the box -- a lingering ControlMaster/session keeps it live."""
    return holder_worktree_gone(worktree_path) and not users


def describe(users: list[LiveUser], indent: str = "    ") -> str:
    return "\n".join(f"{indent}- {u.describe()}" for u in users)


def busy_report(name: str, busy: object) -> str:
    """A BUSY message that names what is actually holding ``name``."""
    lines = [f"[BUSY] {busy}"]
    users = getattr(busy, "users", None)
    if users is None:
        users = live_users(name)
    if users:
        lines.append(f"  Live local users of '{name}':")
        lines.append(describe(users))
        masters = [u for u in users if u.role == ROLE_CONTROL_MASTER]
        if masters:
            lines.append(
                "  A leftover ControlMaster can be closed with "
                "`ssh -O exit -o ControlPath=<path> _` (path shown above).")
    lines.append(f"  Inspect: agent-codespaces in-use {name} --json")
    return "\n".join(lines)


def cmd_in_use(args) -> int:
    """``agent-codespaces in-use <name>``: exit 0 idle, 75 in use, 3 unknown."""
    table = process_table()
    users = live_users(args.name, table=table)
    in_use = True if users else (False if table is not None else None)
    if getattr(args, "json_output", False):
        print(json.dumps({
            "codespace": args.name,
            "in_use": in_use,
            "process_scan": table is not None,
            "live_users": [u.to_dict() for u in users],
        }))
    elif users:
        print(f"{args.name}: IN USE by {len(users)} live local process(es):")
        print(describe(users))
    elif in_use is None:
        print(f"{args.name}: UNKNOWN -- no live lock holder, but the process table "
              "could not be read to rule out SSH sessions")
    else:
        print(f"{args.name}: not in use by any local process")
    return 75 if users else (0 if in_use is False else 3)


# Lifecycle commands that accept --force to override the live-user check.
_FORCEABLE_OPS = frozenset({"stop", "finalize", "delete"})


class CodespaceInUseError(TargetBusyError):
    """A lifecycle operation refused because live local users still ride the box.

    A :class:`ssh_manager.TargetBusyError` so every existing ``[BUSY]`` handler
    reports it unchanged (``busy_report`` lists the users).
    """

    def __init__(self, name: str, users: list[LiveUser], op: str = "this operation") -> None:
        first = users[0] if users else LiveUser(-1, "unknown", "")
        super().__init__(name, LockHolder(pid=first.pid, op=first.role, target=name,
                                          started_at=time.time()))
        self.users = users
        if users:
            why = f"is still in use by {len(users)} live local process(es)"
            fix = "close them (see below)"
        else:
            why = "could not be confirmed idle (local process table unreadable)"
            fix = "restore local process listing (ps / /proc / PowerShell CIM)"
        retry = (f"or re-run {op} with --force to proceed anyway" if op in _FORCEABLE_OPS
                 else f"{op} will retry on a later pass")
        self.args = (f"CodeSpace '{name}' {why}; refusing {op}. To proceed, {fix}; "
                     f"{retry}.",)


def refuse_if_in_use(name: str, op: str, *, settle: float = 0.0) -> None:
    """Raise :class:`CodespaceInUseError` when ``name`` has live local users, or
    when that cannot be ruled out (process table unreadable) -- fail closed.

    ``settle`` (seconds) re-probes while users remain, so a connection THIS
    operation just closed (e.g. the session-recovery ControlMaster) can exit
    before the final pre-destruction check counts it.
    """
    deadline = time.monotonic() + settle
    while True:
        table = process_table()
        users = live_users(name, table=table)
        if not users and table is not None:
            return
        if time.monotonic() >= deadline:
            raise CodespaceInUseError(name, users, op)
        time.sleep(0.5)


_RECHECK_SETTLE = 5.0


def recheck_before(name: str, op: str, *, force: bool = False) -> None:
    """The final fail-closed check run immediately before stop/delete (after a
    possibly long session recovery). No-op with ``force``."""
    if not force:
        refuse_if_in_use(name, op, settle=_RECHECK_SETTLE)
