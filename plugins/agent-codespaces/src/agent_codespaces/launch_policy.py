"""Host-registered launch policy: may a worker be launched on a CodeSpace now?

A host project that keeps its own reasons to hold a worker back (an operator
pause, a stop marker, a budget) registers one command on this machine::

    agent-codespaces launch-policy set [--timeout 20] -- <argv...>

Every CodeSpace worker launch asks it first: an attached or detached
``agent-codespaces copilot``, and agent-bridge's Session Host spawn on a
CodeSpace (a fresh start, or a respawn on resume -- including the implicit
resume a later ``send`` triggers), which shells ``agent-codespaces
launch-check``. So the host's semantics live in the host, but no launch path
can skip them.

The command gets one JSON document on stdin::

    {"schema": 1, "venue": "codespace", "codespace": "<name>"}

and answers on stdout with ``{"refuse": null}`` (allow) or ``{"refuse":
"<one-line reason>"}``. It **fails closed**: a non-zero exit, a timeout, output
that isn't that JSON, or an unreadable registration refuses the launch, since a
policy that couldn't be read can't say it's safe. With nothing registered every
launch is allowed. A refusal exits ``LAUNCH_REFUSED_EXIT`` (79).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags, spawn_sync_in_kill_on_close_job

from .config import RUNTIME_DIR, ensure_runtime_dir
from .launch_memory import _private as _private_dir

POLICY_FILE = RUNTIME_DIR / "launch-policy.json"
LAUNCH_REFUSED_EXIT = 79
DEFAULT_TIMEOUT = 20.0
#: The longest a registered policy may take. agent-bridge's ``launch-check``
#: call allows this plus a cleanup grace, so both launch paths agree on a slow
#: policy instead of the bridge alone timing it out.
MAX_TIMEOUT = 45.0
#: Per stream: a policy answers with one small JSON object, so more than this
#: is a malfunction, refused rather than buffered.
MAX_OUTPUT = 64 * 1024
_CLEANUP_GRACE = 5.0
#: ``agent_bridge.protocol.CODESPACE_LAUNCH_POLICY_PROTOCOL_VERSION``: the first
#: daemon that asks this policy before a Session Host spawn on a CodeSpace.
BRIDGE_POLICY_PROTOCOL = 25


def bridge_enforcement() -> bool | None:
    """Whether this machine's running agent-bridge daemon asks the policy before
    it spawns a worker on a CodeSpace; ``None`` when no daemon is reachable. An
    older resident daemon launches without asking until it is updated and
    restarted, so registering a policy reports this rather than assuming it."""
    try:
        from venue_copilot import _daemon_health, resolve_daemon_port

        port = resolve_daemon_port()
        if port is None:
            return None
        return int(_daemon_health(port).get("protocol_version") or 0) >= BRIDGE_POLICY_PROTOCOL
    except Exception:  # noqa: BLE001 -- unreachable just means unknown
        return None


def _warn_if_bridge_skips(enforces: bool | None) -> None:
    if enforces is False:
        print("[WARN] The running agent-bridge daemon predates launch-policy enforcement "
              f"(HTTP protocol < {BRIDGE_POLICY_PROTOCOL}): its CodeSpace launches skip this "
              "policy until agent-bridge is updated and its daemon restarted.", file=sys.stderr)


class PolicyUnreadable(Exception):
    """The registration exists but can't be read."""


def registered() -> dict[str, Any] | None:
    """The registration, ``None`` when there is none. A registration in a runtime
    directory another local user could write is refused (fail closed): they could
    have replaced it with a command of their choosing."""
    try:
        info = os.lstat(POLICY_FILE)
    except FileNotFoundError:
        return None
    except OSError as exc:  # can't tell whether one is registered: refuse
        raise PolicyUnreadable(f"{POLICY_FILE}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):  # register() only ever writes a regular file
        raise PolicyUnreadable(f"{POLICY_FILE} is a symlink")
    if not stat.S_ISREG(info.st_mode):  # a FIFO or device could block the read
        raise PolicyUnreadable(f"{POLICY_FILE} is not a regular file")
    if not _exclusive_dir(create=False):
        raise PolicyUnreadable(f"{POLICY_FILE.parent} is not a directory only this user controls")
    try:
        raw = POLICY_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None  # removed between the check and the read
    except (OSError, ValueError) as exc:  # ValueError: not UTF-8
        raise PolicyUnreadable(f"{POLICY_FILE}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyUnreadable(f"{POLICY_FILE} is not JSON: {exc}") from exc
    argv = data.get("argv") if isinstance(data, dict) else None
    if not valid_argv(argv):
        raise PolicyUnreadable(f"{POLICY_FILE} has no valid argv")
    timeout = data.get("timeout", DEFAULT_TIMEOUT)
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not 0 < timeout <= MAX_TIMEOUT):
        raise PolicyUnreadable(f"{POLICY_FILE} has an invalid timeout (must be in (0, {MAX_TIMEOUT:g}])")
    return {"argv": argv, "timeout": float(timeout)}


def valid_argv(argv: Any) -> bool:
    """An absolute command, then any arguments (empty ones are valid argv), no
    NULs. ``set`` pins a bare command to its absolute path, so the launcher and
    the resident bridge -- each in its own working directory -- run the same one."""
    return (isinstance(argv, list) and bool(argv) and all(isinstance(a, str) for a in argv)
            and bool(argv[0]) and not any("\x00" in a for a in argv) and os.path.isabs(argv[0]))


def describe(policy: dict[str, Any]) -> dict[str, Any]:
    """What ``show`` reports: the command and how many arguments follow it, never
    the arguments themselves (they may carry secrets; the file is owner-only)."""
    return {"command": policy["argv"][0], "arguments": len(policy["argv"]) - 1,
            "timeout": policy["timeout"]}


def register(argv: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Write the registration owner-only (0600 from creation): its argv may carry
    secrets, which a umask-default 0644 file would expose to other local users."""
    if not valid_argv(argv):
        raise ValueError("the policy command must be an absolute path and contain no NUL bytes")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be in (0, {MAX_TIMEOUT:g}] seconds")
    ensure_runtime_dir()
    if not _exclusive_dir(create=True):
        raise PermissionError(f"{POLICY_FILE.parent} can't be made a directory only this user controls")
    tmp = POLICY_FILE.with_name(f".{POLICY_FILE.name}.{os.getpid()}.tmp")
    data = json.dumps({"argv": argv, "timeout": timeout}, indent=2).encode("utf-8")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, POLICY_FILE)


def _exclusive_dir(*, create: bool) -> bool:
    """Whether the registration's directory is one only this user controls. On
    POSIX that is its ownership and mode (``launch_memory._private``). On Windows
    mode bits say nothing about the ACL, so a directory is trusted only inside
    the user's profile, whose inherited ACL admits only the user (plus SYSTEM and
    Administrators); elsewhere exclusive control can't be shown, so it fails
    closed rather than run a command another account could have written."""
    if not _private_dir(POLICY_FILE.parent, create=create):
        return False
    if os.name != "nt":
        return True
    try:
        parent, home = POLICY_FILE.parent.resolve(), Path.home().resolve()
    except OSError:
        return False
    return parent == home or home in parent.parents


def clear() -> bool:
    """Remove the registration. A directory squatting on its path (which every
    read refuses as corrupt) is removed when it is empty; otherwise this raises
    ``OSError`` naming what the operator has to remove by hand."""
    try:
        POLICY_FILE.unlink()
    except FileNotFoundError:
        return False
    except (IsADirectoryError, PermissionError):  # Windows reports a directory as PermissionError
        if POLICY_FILE.is_symlink() or not POLICY_FILE.is_dir():
            raise
        try:
            POLICY_FILE.rmdir()
        except OSError as exc:
            raise OSError(f"{POLICY_FILE} is a directory, not a registration, and isn't empty: "
                          "remove it by hand") from exc
    return True


def _one_line(text: str) -> str:
    return " ".join((text or "").split())[:200]


class PolicyOutputTooLarge(RuntimeError):
    """The policy wrote more than ``MAX_OUTPUT`` bytes to stdout or stderr."""


def _capped_reader(stream, sink: bytearray, overflow: threading.Event) -> None:
    """Drain ``stream`` into ``sink`` until EOF, or until it would exceed
    ``MAX_OUTPUT`` -- then flag the overflow and stop reading."""
    while chunk := stream.read1(8192):
        if len(sink) + len(chunk) > MAX_OUTPUT:
            overflow.set()
            return
        sink.extend(chunk)


def _run_contained(argv: list[str], request: str, timeout: float) -> tuple[int, str, str]:
    """Run the policy with its whole process tree contained (a kill-on-close Job
    Object on Windows, its own process group elsewhere), so a timeout kills every
    descendant too -- one left holding the pipes would otherwise keep the launch
    waiting for EOF. Its output is drained into a fixed cap, so a runaway policy
    can't grow the caller's memory. Raises ``subprocess.TimeoutExpired`` or
    ``PolicyOutputTooLarge`` after the cleanup."""
    kwargs: dict[str, Any] = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                              "cwd": str(POLICY_FILE.parent)}  # one directory, whichever path launches
    if os.name == "nt":
        kwargs["creationflags"] = no_window_flags()
        # Popen doesn't apply PATHEXT: resolve a bare name to its .exe/.cmd shim.
        resolved = shutil.which(argv[0])
        if resolved:
            argv = [resolved, *argv[1:]]
    else:
        kwargs["start_new_session"] = True
    proc, job = spawn_sync_in_kill_on_close_job(argv, **kwargs)
    try:
        out, err, overflow = bytearray(), bytearray(), threading.Event()
        readers = [threading.Thread(target=_capped_reader, args=(stream, sink, overflow), daemon=True)
                   for stream, sink in ((proc.stdout, out), (proc.stderr, err))]
        for reader in readers:
            reader.start()
        try:
            proc.stdin.write(request.encode("utf-8"))
            proc.stdin.close()
        except OSError:  # it exited (or closed stdin) without reading the request
            pass
        deadline = time.monotonic() + timeout
        while proc.poll() is None or any(r.is_alive() for r in readers):
            if overflow.is_set():
                raise PolicyOutputTooLarge(f"launch policy wrote more than {MAX_OUTPUT // 1024} KiB")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            overflow.wait(min(remaining, 0.05))
        if overflow.is_set():
            raise PolicyOutputTooLarge(f"launch policy wrote more than {MAX_OUTPUT // 1024} KiB")
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except BaseException:
        # A timeout, an overflow, or any interruption (Ctrl+C included): the whole
        # tree goes, never left running detached in its own process group.
        if job is not None:
            job.close()
            job = None
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        proc.kill()
        try:
            proc.wait(timeout=_CLEANUP_GRACE)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        if job is not None:
            job.close()


def refusal(codespace: str, deadline: float | None = None) -> str | None:
    """Why a worker may not be launched on ``codespace`` now, or ``None``.
    ``deadline`` (Unix time) is the caller's own limit: the policy's timeout
    shrinks so it, and its cleanup, finish first -- a caller's outer timeout
    killing this process would otherwise leave the policy's separate process
    group without its watchdog."""
    try:
        policy = registered()
    except PolicyUnreadable as exc:
        return f"launch policy registration unreadable: {_one_line(str(exc))}"
    if policy is None:
        return None
    timeout = policy["timeout"]
    if deadline is not None:
        timeout = min(timeout, deadline - time.time() - _CLEANUP_GRACE)
        if timeout <= 0:
            return "launch policy check ran out of time before the policy could run"
    request = json.dumps({"schema": 1, "venue": "codespace", "codespace": codespace})
    try:
        returncode, stdout, stderr = _run_contained(policy["argv"], request, timeout)
    except subprocess.TimeoutExpired:
        return f"launch policy timed out after {round(timeout, 1):g}s"
    except PolicyOutputTooLarge as exc:
        return str(exc)
    except (OSError, RuntimeError, ValueError) as exc:  # ValueError: e.g. an embedded NUL
        return f"launch policy could not run: {_one_line(str(exc))}"
    if returncode != 0:
        return f"launch policy failed (exit {returncode}): {_one_line(stderr or stdout)}"
    try:
        answer = json.loads(stdout)
    except json.JSONDecodeError:
        return "launch policy answered with something other than JSON"
    if not isinstance(answer, dict) or "refuse" not in answer:
        return 'launch policy answer has no "refuse" field'
    reason = answer["refuse"]
    if reason is None:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return "launch policy refused without a reason"
    return _one_line(reason)


def refused_exit_code(codespace: str) -> int | None:
    """``None`` to proceed, else print the refusal and return its exit code."""
    reason = refusal(codespace)
    if reason is None:
        return None
    print(f"[REFUSED] Launch on CodeSpace '{codespace}' refused: {reason}", file=sys.stderr)
    return LAUNCH_REFUSED_EXIT


# -- CLI ----------------------------------------------------------------------


def add_launch_policy_parsers(sub) -> None:
    check = sub.add_parser(
        "launch-check",
        help="Ask the registered launch policy whether a worker may be launched on a "
             "CodeSpace now (exit 0 allowed, 79 refused); the seam agent-bridge shells",
    )
    check.add_argument("codespace", help="CodeSpace name")
    check.add_argument("--json", action="store_true", help='Print {"codespace", "refuse"}')
    check.add_argument("--deadline", type=float,
                       help="Unix time by which the caller needs the answer; the policy's timeout shrinks to fit")
    check.set_defaults(func=cmd_launch_check)

    policy = sub.add_parser("launch-policy", help="Register, show or clear this machine's launch policy")
    verbs = policy.add_subparsers(dest="policy_verb", required=True)
    set_p = verbs.add_parser("set", help="Register the policy command: launch-policy set [--timeout S] -- <argv...>")
    set_p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                       help=f"Seconds the policy may take (default {DEFAULT_TIMEOUT:g}, at most {MAX_TIMEOUT:g})")
    set_p.add_argument("policy_argv", nargs=argparse.REMAINDER, help="The command and its arguments")
    set_p.set_defaults(func=cmd_launch_policy)
    for verb in ("show", "clear"):
        p = verbs.add_parser(verb)
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=cmd_launch_policy)


def cmd_launch_check(args) -> int:
    reason = refusal(args.codespace, deadline=getattr(args, "deadline", None))
    if args.json:
        print(json.dumps({"codespace": args.codespace, "refuse": reason}))
    elif reason is None:
        print(f"[OK] Launch on '{args.codespace}' allowed")
    if reason is not None:
        if not args.json:
            print(f"[REFUSED] Launch on CodeSpace '{args.codespace}' refused: {reason}", file=sys.stderr)
        return LAUNCH_REFUSED_EXIT
    return 0


def cmd_launch_policy(args) -> int:
    if args.policy_verb == "set":
        argv = list(args.policy_argv or [])
        if argv[:1] == ["--"]:
            argv = argv[1:]
        if argv and argv[0] and not os.path.isabs(argv[0]):  # pin it now, not per working directory
            resolved = shutil.which(argv[0])
            if resolved:
                argv[0] = os.path.abspath(resolved)
        if not valid_argv(argv) or not 0 < args.timeout <= MAX_TIMEOUT:
            print(f"[FAIL] usage: launch-policy set [--timeout S] -- <argv...> (0 < S <= {MAX_TIMEOUT:g}; "
                  "a command found on PATH or given by absolute path, no NUL bytes)", file=sys.stderr)
            return 2
        try:
            register(argv, timeout=args.timeout)
        except PermissionError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        # Arguments aren't echoed: they may carry secrets the 0600 file protects.
        print(f"[OK] Launch policy registered: {argv[0]} (+{len(argv) - 1} arguments, timeout {args.timeout:g}s)")
        _warn_if_bridge_skips(bridge_enforcement())
        return 0
    if args.policy_verb == "clear":
        try:
            removed = clear()
        except OSError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"cleared": removed}))
        else:
            print("[OK] Launch policy cleared" if removed else "[OK] No launch policy was registered")
        return 0
    try:
        policy = registered()
    except PolicyUnreadable as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    enforces = bridge_enforcement() if policy else None
    if args.json:
        print(json.dumps({"policy": describe(policy) if policy else None, "file": str(POLICY_FILE),
                          "bridge_enforces": enforces}))
    else:
        if policy:
            shown = describe(policy)
            print(f"{shown['command']} (+{shown['arguments']} arguments, timeout {shown['timeout']:g}s)  "
                  f"-- full command in {POLICY_FILE}")
        else:
            print("(none registered)")
        _warn_if_bridge_skips(enforces)
    return 0
