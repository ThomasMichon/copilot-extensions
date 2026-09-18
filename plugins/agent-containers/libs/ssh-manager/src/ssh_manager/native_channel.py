"""Shared native control channel over provider-owned SSH and admission."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import sys
import threading

log = logging.getLogger("ssh-manager.native")
_PROGRESS_PHASES = frozenset({
    "local-config", "owner-admission", "ssh-to-target", "target-auth-env",
    "target-binstub", "worktree", "native-host",
})
_PROGRESS_STATUSES = frozenset({"started", "reached", "failed"})


class CommandDeadlineError(RuntimeError):
    pass


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


async def serve(
    args, manager, ssh_config, relay_env: str, *,
    require_owner, mark_launch, retire, namespace="codespace", output=None,
) -> int:
    from ssh_manager import LocalForward
    from ssh_manager.remote_command import render_command

    forwards = []
    host_forward = None
    host_port = None
    forward_lock = asyncio.Lock()
    output_lock = asyncio.Lock()
    jobs: set[asyncio.Task] = set()
    retired_result = None
    retirement_only = bool(getattr(args, "retirement_only", False))
    descriptor = getattr(args, "remote_command_json", None)
    output = output or sys.stdout

    async def emit(value: dict) -> None:
        def write():
            output.write(json.dumps(value) + "\n")
            output.flush()
        async with output_lock:
            await asyncio.to_thread(write)

    async def remote(action: str, data: dict | None = None) -> dict:
        command = ["native-host", action]
        if action != "capabilities":
            if action != "start":
                command += [args.execution_id, "--expected-generation", args.generation]
            else:
                command.append("--request-stdin")
            if action in {"stop", "message", "result", "resource-complete"}:
                command.append("--request-stdin")
        payload = data or {}
        if action == "start":
            payload = {
                **payload, "executionId": args.execution_id, "generation": args.generation,
                "owner": args.effort, "prelude": relay_env,
                **({"codespace": args.name} if namespace == "codespace" else {"target": f"{namespace}:{args.name}"}),
            }
        elif action == "stop":
            payload = {**payload, "owner": args.effort,
                       **({"codespace": args.name} if namespace == "codespace" else {"target": f"{namespace}:{args.name}"})}
        timeout = 150.0 if action == "start" else 30.0
        if action == "message" and payload.get("wait"):
            wait = float(payload.get("waitTimeout", 120))
            if not math.isfinite(wait) or not 0 < wait <= 300:
                raise ValueError("native reply timeout must be in (0,300]")
            timeout = wait + 30
        result = await manager.exec_command(
            args.name, render_command(descriptor, command),
            timeout=timeout,
            input_bytes=json.dumps(payload).encode("utf-8"),
        )
        if getattr(result, "timed_out", False):
            raise CommandDeadlineError(
                "Remote observation deadline expired; delivery may have been admitted. "
                "Inspect the result or reuse the same messageId; do not assume non-delivery."
            )
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("official remote agent-bridge native hosting is unavailable or returned invalid data") from exc
        status_diagnostic = (
            action == "status" and isinstance(value, dict)
            and value.get("error") == "registration_lookup_failed"
            and value.get("state") == "unrepresented" and value.get("represented") is False
            and value.get("retired") is False
            and value.get("executionId") == args.execution_id and value.get("generation") == args.generation
        )
        if result.exit_code != 0 or not isinstance(value, dict) or (value.get("error") and not status_diagnostic):
            raise RuntimeError(
                value.get("detail", "remote native operation failed") if isinstance(value, dict) else
                "remote native operation returned invalid data"
            )
        if (
            value.get("executionId", args.execution_id) != args.execution_id
            or value.get("generation", args.generation) != args.generation
        ):
            raise RuntimeError("remote native execution identity changed")
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
                       "stop": "stop", "message": "message", "result": "result",
                       "resource-complete": "resource-complete"}
            if action not in mapping:
                raise ValueError("unsupported native transport operation")
            if action == "launch":
                mark_launch()
            result = await remote(mapping[action], request.get("params") or {})
            if action == "stop" and result.get("retired") is True:
                await retire(result)
                retired_result = result
            elif action != "stop" and not retirement_only and result.get("host") and not result.get("retired"):
                result["localPort"] = await ensure_forwards(int(result["host"]["port"]))
            result["ports"] = [
                {"direction": direction, "listen": listen, "connect": connect, "host": "127.0.0.1"}
                for direction, entries in (("local", args.local_forward), ("reverse", args.reverse_forward))
                for listen, connect in entries
            ]
            await emit({"id": request_id, "ok": True, "result": result})
        except CommandDeadlineError as exc:
            await emit({"id": request_id, "ok": False, "error": str(exc), "errorCode": "reply_transport_timeout"})
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
        resources = capability.get("hostResources") == "native-host-resources-v1"
        if not retirement_only:
            started = await manager.exec_command(
                args.name, render_command(descriptor, ["service", "start"]), timeout=150.0,
            )
            if started.exit_code != 0:
                raise RuntimeError("remote shared agent-bridge service did not start")
            ready = await manager.exec_command(
                args.name, render_command(descriptor, ["installer-readiness"]), timeout=30.0,
            )
            state = json.loads(ready.stdout)
            if ready.exit_code != 0 or state.get("module") != "agent-bridge/runtime" or state.get("state") != "ready":
                raise RuntimeError("remote shared agent-bridge service is not ready")
        if progress:
            progress("native-host", "reached")
        await emit({"event": "ready", "capability": f"{namespace}-native-transport-v1", "version": 1,
                    **({"hostResources": "native-host-resources-v1"} if resources else {})})
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
