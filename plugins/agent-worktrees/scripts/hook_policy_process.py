"""Caller-owned resident policy execution; stdin EOF cancels its actual worker."""

from __future__ import annotations

import base64
import binascii
import os
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

_MAX_INPUT = 16 * 1024 * 1024
_MAX_OUTPUT = 64 * 1024


def _watch_caller() -> None:
    try:
        sys.stdin.buffer.read(1)
    finally:
        # Closing the caller-owned SDK's lifetime handles retires its whole scope,
        # even when this interpreter sits behind a Windows venv launcher.
        os._exit(3)


def _watch_legacy_parent(stop):
    from hook_client import _load_sibling

    identity = _load_sibling("hook_process_identity.py")
    birth_token = getattr(identity, "process_start_time", None)
    active = getattr(identity, "process_is_alive", None)
    parent = os.getppid()
    birth = birth_token(parent) if callable(birth_token) else None
    if parent <= 1 or birth is None or not callable(active) or not active(parent, birth) or os.getppid() != parent:
        raise RuntimeError("legacy caller identity unavailable")

    def watch():
        while not stop.wait(.05):
            try:
                live = os.getppid() == parent and active(parent, birth)
            except Exception:
                live = False
            if not live and not stop.is_set():
                # Exit closes the SDK lifetime handles; it does not attest settlement.
                os._exit(3)

    watcher = threading.Thread(target=watch, name="legacy-policy-caller", daemon=True)
    watcher.start()
    return watcher


def execute(frame: bytes, *, legacy: bool = False, settlement_deadline=None, initial_decision=None) -> int:
    stop = threading.Event()
    watcher = []
    try:
        return _execute(
            frame, legacy=legacy, settlement_deadline=settlement_deadline,
            initial_decision=initial_decision, stop=stop, watcher=watcher,
        )
    finally:
        stop.set()
        for thread in watcher:
            thread.join(timeout=.05)


def _execute(frame: bytes, *, legacy: bool, settlement_deadline, initial_decision, stop, watcher) -> int:
    """Supervise the one strict policy worker before forwarding any decision."""
    settlement_deadline = settlement_deadline or time.monotonic() + 23.0

    def failure(boundary, code):
        if initial_decision is not None:
            from hook_pre_policy import unavailable
            from hook_client import _merge_pre_decisions

            print(json.dumps(_merge_pre_decisions(initial_decision, unavailable(boundary))), end="")
            return 0
        return code

    try:
        from agent_procutil import direct_python
        from agent_procutil.owned_budget import OwnedExecutionBudget
        from agent_procutil.owned_process import (
            OwnedCleanupError,
            require_caller_owned_support,
            run_caller_owned,
        )

        require_caller_owned_support()
        if legacy:
            watcher.append(_watch_legacy_parent(stop))
        budget = OwnedExecutionBudget(settlement_deadline + 2.0, 2.0)
        python, overrides = direct_python(sys.executable)
        environment = dict(os.environ)
        environment.update(overrides)
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        environment["AGENT_WORKTREES_PRE_SETTLEMENT_DEADLINE"] = str(
            settlement_deadline if os.name == "nt" else min(
                settlement_deadline, budget.enclosed_budget().execution_deadline,
            )
        )
        if initial_decision is not None:
            frame = json.dumps({"event": json.loads(frame), "initial": initial_decision}).encode("utf-8")
        input_text = frame.decode("utf-8")
        cwd = Path.home()
        command = [
            python, "-X", "utf8", "-B", str(Path(__file__).with_name("hook_client.py")),
            "preToolUseStrict", *(["--only-pr-observations"] if initial_decision is not None else []),
        ]
    except Exception:
        sys.stderr.write("agent-worktrees resident policy: owned admission unavailable\n")
        return failure("supersede native admission unavailable", 3)
    settled = []
    try:
        result = run_caller_owned(
            command,
            timeout=25.0,
            max_stdout_bytes=_MAX_OUTPUT,
            max_stderr_bytes=_MAX_OUTPUT,
            input_text=input_text,
            cwd=cwd,
            env=environment,
            budget=budget,
            on_settled=lambda: settled.append(True),
        )
    except OwnedCleanupError as error:
        sys.stderr.write("agent-worktrees resident policy: cleanup unconfirmed\n")
        sys.stderr.flush()
        # Retain exact SDK custody until the caller closes its lifetime channel.
        threading.Event().wait()
        raise error
    except subprocess.TimeoutExpired as error:
        if settled and error.output:
            try:
                envelope = json.loads(error.output)
                pending = envelope["_ownedCleanupPending"] if set(envelope) == {"_ownedCleanupPending"} else envelope
                from hook_pre_policy import valid_decision

                if not isinstance(pending, dict) or not valid_decision(pending):
                    raise ValueError("invalid settled policy output")
            except (ValueError, KeyError, TypeError):
                pass
            else:
                from hook_client import _merge_pre_decisions

                sys.stdout.write(json.dumps(_merge_pre_decisions(initial_decision or {}, pending), separators=(",", ":")))
                return 0
        if settled:
            return failure("supersede observation timeout", 4)
        return 4
    except Exception:
        if initial_decision is not None:
            if settled:
                return failure("supersede execution unavailable", 2)
            sys.stderr.write("agent-worktrees supersede: native custody unresolved\n")
            threading.Event().wait()
        return 2
    if result.returncode:
        return failure("supersede worker exit unavailable", 2) if settled else 2
    if initial_decision is not None and settled:
        try:
            decision = json.loads(result.stdout)
            from hook_pre_policy import valid_decision

            if not isinstance(decision, dict) or not valid_decision(decision):
                raise ValueError("invalid supersede decision")
        except (TypeError, ValueError):
            return failure("supersede output unavailable", 2)
        from hook_client import _merge_pre_decisions

        result.stdout = json.dumps(_merge_pre_decisions(initial_decision, decision))
    if result.stdout and not (legacy and result.stdout.strip() == "{}"):
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return 0


def main() -> int:
    settlement_deadline = time.monotonic() + 23.0
    if sys.argv[1:] not in ([], ["--base64-input"]):
        return 2
    encoded = bool(sys.argv[1:])
    limit = 4 * ((_MAX_INPUT + 2) // 3) + 1 if encoded else _MAX_INPUT
    frame = sys.stdin.buffer.readline(limit + 1)
    if len(frame) > limit or not frame.endswith(b"\n"):
        return 2
    if encoded:
        try:
            frame = base64.b64decode(frame[:-1], validate=True)
        except (binascii.Error, ValueError):
            return 2
        if len(frame) > _MAX_INPUT:
            return 2
    threading.Thread(target=_watch_caller, name="policy-caller-lifetime", daemon=True).start()
    return execute(frame, settlement_deadline=settlement_deadline)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The daemon reader owns stdin; normal SDK scopes are settled before this reply.
    os._exit(code)
