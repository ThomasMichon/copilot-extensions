"""Provider-owned preparation/relay/forwarding for a bridge-owned native execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import shlex
import signal
import sys
import threading

from . import execution_claims

log = logging.getLogger("agent-codespaces")
CAPABILITY = "codespace-native-transport-v1"
_PROGRESS_PHASES = frozenset({
    "local-config", "owner-admission", "ssh-to-target", "target-auth-env",
    "target-binstub", "worktree", "native-host",
})
_PROGRESS_STATUSES = frozenset({"started", "reached", "failed"})


def emit_progress(args, phase: str, status: str = "started") -> None:
    if phase not in _PROGRESS_PHASES or status not in _PROGRESS_STATUSES:
        raise ValueError("Unsupported native preparation progress")
    print(json.dumps({
        "event": "progress", "executionId": args.execution_id,
        "generation": args.generation, "phase": phase, "status": status,
    }), flush=True)


class InputPump:
    """Read control input during preparation as well as while serving."""
    def __init__(self) -> None:
        self.frames = queue.Queue(maxsize=32)
        self.closed = threading.Event()
        threading.Thread(target=self._read, name="native-control-input", daemon=True).start()

    def _read(self) -> None:
        try:
            while raw := sys.stdin.buffer.readline(1048577):
                try:
                    self.frames.put_nowait(raw)
                except queue.Full:
                    break
        finally:
            self.closed.set()
            try:
                self.frames.put_nowait(None)
            except queue.Full:
                pass

    async def next(self):
        return await asyncio.to_thread(self.frames.get)


async def serve(args, manager, ssh_config, relay_env: str) -> int:
    from ssh_manager import LocalForward

    forwards = []
    host_forward = None
    host_port = None
    forward_lock = asyncio.Lock()
    output_lock = asyncio.Lock()
    jobs: set[asyncio.Task] = set()
    identity = (args.execution_id, args.generation)
    retired_result = None
    retirement_only = bool(getattr(args, "retirement_only", False))

    async def emit(value: dict) -> None:
        def write():
            sys.stdout.write(json.dumps(value) + "\n")
            sys.stdout.flush()
        async with output_lock:
            await asyncio.to_thread(write)

    def require_owner() -> None:
        from .lease import _lease_lock, CoordinationRejected

        with _lease_lock():
            row = execution_claims._read().get(args.name)
            if row is None or row["mode"] != "native":
                raise CoordinationRejected("native execution reservation is unavailable")
            execution_claims.assert_access(args.name, identity, args.effort)

    async def remote(action: str, data: dict | None = None) -> dict:
        command = ["agent-bridge", "native-host", action]
        if action != "capabilities":
            if action != "start":
                command += [args.execution_id, "--expected-generation", args.generation]
            else:
                command.append("--request-stdin")
            if action in {"stop", "message", "result"}:
                command.append("--request-stdin")
        payload = data or {}
        if action == "start":
            payload = {
                **payload, "executionId": args.execution_id, "generation": args.generation,
                "owner": args.effort, "codespace": args.name, "prelude": relay_env,
            }
        elif action == "stop":
            payload = {**payload, "owner": args.effort, "codespace": args.name}
        result = await manager.exec_command(
            args.name, "bash -lc " + shlex.quote(shlex.join(command)),
            timeout=150.0 if action in {"start", "message"} else 30.0,
            input_bytes=json.dumps(payload).encode("utf-8"),
        )
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("official remote agent-bridge native hosting is unavailable or returned invalid data") from exc
        if result.exit_code != 0 or not isinstance(value, dict) or value.get("error"):
            raise RuntimeError(value.get("detail", "remote native operation failed"))
        return value

    async def ensure_forwards(remote_port: int) -> int:
        nonlocal host_forward, host_port
        async with forward_lock:
            if host_port is not None and host_port != remote_port:
                raise RuntimeError("native host endpoint changed without a new execution generation")
            host_port = remote_port
            if host_forward is None:
                reverse = [f"127.0.0.1:{r}:127.0.0.1:{l}" for r, l in args.reverse_forward]
                host_forward = LocalForward(
                    ssh_config, remote_port, reverse_forwards=reverse,
                    extra_options={"ExitOnForwardFailure": "yes"},
                )
                await host_forward.establish()
                for local, distant in args.local_forward:
                    forward = LocalForward(ssh_config, distant, local_port=local)
                    forwards.append(forward)
                    await forward.establish()
            elif not host_forward.is_alive:
                await host_forward.refresh()
            for forward in forwards:
                if not forward.is_alive:
                    await forward.refresh()
            return host_forward.local_port

    async def monitor() -> None:
        while True:
            await asyncio.sleep(3)
            if host_port is not None:
                try:
                    await ensure_forwards(host_port)
                except Exception as exc:
                    log.warning("Native forwarding is temporarily unavailable: %s", exc)

    async def handle(request: dict) -> None:
        nonlocal retired_result
        request_id = request.get("id")
        try:
            if request.get("executionId") != args.execution_id or request.get("generation") != args.generation:
                raise ValueError("native request identity does not match this transport")
            action = request.get("method")
            if action == "stop" and retired_result is not None:
                await emit({"id": request_id, "ok": True, "result": retired_result})
                return
            require_owner()
            if retirement_only and action not in {"stop", "status"}:
                raise ValueError("retirement-only transport cannot launch, activate, or deliver input")
            mapping = {"launch": "start", "activate": "activate", "status": "status",
                       "stop": "stop", "message": "message", "result": "result"}
            if action not in mapping:
                raise ValueError("unsupported native transport operation")
            if action == "launch":
                execution_claims.mark(args.name, args.effort, identity, launchRequested=True)
            result = await remote(mapping[action], request.get("params") or {})
            if action == "stop" and result.get("retired") is True:
                result["recovery"] = {"ok": False, "detail": "session recovery pending; venue preserved"}
                retired_result = result
                execution_claims.release(args.name, args.effort, identity, proof=result)
                args.native_retirement_recorded = True
                args.native_retired = not result.get("noLaunch", False)
                from .sessions import sync_codespace_sessions

                try:
                    result["recovery"] = (
                        {"ok": True, "skipped": True, "detail": "native execution never launched"}
                        if result.get("noLaunch") else
                        await asyncio.to_thread(sync_codespace_sessions, args.name)
                    )
                except Exception:
                    result["recovery"] = {"ok": False, "detail": "session recovery remains pending; venue preserved"}
                execution_claims.update_recovery(args.name, args.effort, identity, result["recovery"])
            elif action != "stop" and not retirement_only and result.get("host") and not result.get("retired"):
                result["localPort"] = await ensure_forwards(int(result["host"]["port"]))
            result["ports"] = [
                {"direction": direction, "listen": listen, "connect": connect, "host": "127.0.0.1"}
                for direction, entries in (("local", args.local_forward), ("reverse", args.reverse_forward))
                for listen, connect in entries
            ]
            await emit({"id": request_id, "ok": True, "result": result})
        except Exception as exc:
            await emit({"id": request_id, "ok": False, "error": str(exc)})

    monitor_task = None
    try:
        progress = getattr(args, "native_progress", None)
        if progress:
            progress("native-host", "started")
        capability = await remote("capabilities")
        if capability.get("capability") != "codespace-native-host-v1" or capability.get("supported") is not True:
            raise RuntimeError("remote native execution hosting capability is unavailable")
        started = await manager.exec_command(
            args.name, "bash -lc 'agent-bridge service start'", timeout=150.0,
        )
        if started.exit_code != 0:
            raise RuntimeError("remote shared agent-bridge service did not start")
        ready = await manager.exec_command(
            args.name, "bash -lc 'agent-bridge installer-readiness'", timeout=30.0,
        )
        state = json.loads(ready.stdout)
        if ready.exit_code != 0 or state.get("module") != "agent-bridge/runtime" or state.get("state") != "ready":
            raise RuntimeError("remote shared agent-bridge service is not ready")
        if progress:
            progress("native-host", "reached")
        await emit({"event": "ready", "capability": CAPABILITY, "version": 1})
        if not retirement_only:
            monitor_task = asyncio.create_task(monitor())
        while True:
            raw = await args.native_input.next()
            if not raw:
                break
            if len(raw) > 1048576:
                raise ValueError("native transport request exceeds the bounded frame")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("native transport request must be an object")
            if len(jobs) >= 8:
                await emit({"id": request.get("id"), "ok": False, "error": "native transport request budget exceeded"})
                continue
            job = asyncio.create_task(handle(request))
            jobs.add(job)
            job.add_done_callback(jobs.discard)
        return 0
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for forward in forwards:
            await forward.cancel()
        if host_forward is not None:
            await host_forward.cancel()


def command(args) -> int:
    from . import __main__ as cli
    from . import gh_account, lifecycle

    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        raise RuntimeError("native hosting requires provider claims; claim bypass is not supported")
    from .lease import ClaimConflict
    from ssh_manager import TargetBusyError

    try:
        execution_claims.reserve(args.name, args.effort, args.execution_id, args.generation, "native")
    except (ClaimConflict, TargetBusyError):
        print(json.dumps({"event": "rejected", "code": "venue_busy"}), flush=True)
        return 75
    args.native_input = InputPump()
    print(json.dumps({"event": "reserved", "executionId": args.execution_id, "generation": args.generation}), flush=True)
    args.native_progress = lambda phase, status="started": emit_progress(args, phase, status)
    args.native_progress("local-config")
    account = lifecycle.account_for_codespace(args.name)
    if account:
        os.environ.update(gh_account.env_for_account(account))
    if sys.platform != "win32":
        def interrupted(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
    try:
        return cli._cmd_ssh(args)
    except asyncio.CancelledError:
        return 0
    finally:
        if getattr(args, "native_cleanup_complete", False) and not getattr(args, "native_retirement_recorded", False):
            execution_claims.mark(
                args.name, args.effort, (args.execution_id, args.generation), infrastructureStopped=True,
            )
