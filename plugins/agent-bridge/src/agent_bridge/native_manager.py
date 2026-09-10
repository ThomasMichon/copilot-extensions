"""Bridge-owned native executions over the registered CodeSpace provider.

These are execution-host records, never phantom ACP SessionManager sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath

from .native_store import NativeError, NativeStore, identifier, receipt, signature
from .session_host.client import SessionHostClient
from .session_host.launcher import host_spawn_kwargs

CAPABILITY = "codespace-native-control-v1"
_LIVE = ("starting", "ready", "unrepresented", "unreachable")
_PROGRESS_PHASES = frozenset({
    "local-config", "owner-admission", "ssh-to-target", "target-auth-env",
    "target-binstub", "worktree", "native-host",
})
_PROGRESS_STATUSES = frozenset({"started", "reached", "failed"})


def validate_request(request: dict) -> dict:
    allowed = {"requestId", "codespace", "owner", "cwd", "command", "noPluginStaging",
               "requireRelay", "localForward", "reverseForward"}
    if set(request) - allowed:
        raise NativeError("invalid_request", "Unknown native launch fields", 400)
    for key in ("requestId", "codespace"):
        identifier(request.get(key))
    owner = request.get("owner")
    cwd = request.get("cwd")
    command = request.get("command")
    if not isinstance(owner, str) or not Path(owner).is_absolute() or not Path(owner).is_dir():
        raise NativeError("invalid_owner", "Owner must be an existing controller worktree directory", 400)
    if not isinstance(cwd, str) or not PurePosixPath(cwd).is_absolute() or "\0" in cwd:
        raise NativeError("invalid_cwd", "An absolute venue cwd is required", 400)
    if not isinstance(command, str) or not command.strip() or "\0" in command or len(command.encode()) > 262144:
        raise NativeError("invalid_command", "A bounded nonblank UTF-8 command is required", 400)
    if request.get("noPluginStaging", True) is not True or request.get("requireRelay", True) is not True:
        raise NativeError("invalid_preparation", "Native hosting retains required relay preparation without plugin copying", 400)
    for key in ("localForward", "reverseForward"):
        values = request.get(key, [])
        if not isinstance(values, list) or len(values) > 32:
            raise NativeError("invalid_forwards", "Expected a bounded forwarding list", 400)
        listeners = set()
        for value in values:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,5}:[0-9]{1,5}", value):
                raise NativeError("invalid_forwards", "Forwards require decimal LISTEN:CONNECT ports", 400)
            listen, connect = map(int, value.split(":"))
            if not 1 <= listen <= 65535 or not 1 <= connect <= 65535 or listen in listeners:
                raise NativeError("invalid_forwards", "Forward ports must be unique listeners in 1..65535", 400)
            listeners.add(listen)
    return {
        **request, "owner": str(Path(owner).resolve()), "noPluginStaging": True, "requireRelay": True,
        "localForward": request.get("localForward", []), "reverseForward": request.get("reverseForward", []),
    }


class ProviderTransport:
    def __init__(self, prefix: list[str], row: dict, on_reserved, on_progress=None) -> None:
        self.prefix, self.row, self.on_reserved = prefix, row, on_reserved
        self.on_progress = on_progress
        self.process = None
        self.pending: dict[str, asyncio.Future] = {}
        self.reader_task = self.stderr_task = None
        self.ready = None
        self.write_lock = asyncio.Lock()

    async def start(self, *, resume: bool, retirement_only: bool = False) -> None:
        spec = self.row["data"]["spec"]
        argv = [
            *self.prefix, "native-transport", self.row["codespace"],
            "--owner", self.row["owner"], "--execution-id", self.row["id"],
            "--generation", self.row["generation"], "--no-plugin-staging", "--require-relay",
        ]
        if resume:
            argv.append("--resume-infrastructure")
        if retirement_only:
            argv.append("--retirement-only")
        else:
            for key, flag in (("localForward", "--local-forward"), ("reverseForward", "--reverse-forward")):
                for value in spec[key]:
                    argv += [flag, value]
        env = dict(os.environ)
        for key in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "SESSION_ID", "COPILOT_PLUGIN_ROOT"):
            env.pop(key, None)
        self.ready = asyncio.get_running_loop().create_future()
        self.ready.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        self.process = await asyncio.create_subprocess_exec(
            *argv, cwd=self.row["owner"], env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=1048576, **host_spawn_kwargs(),
        )
        self.reader_task = asyncio.create_task(self._read())

        async def drain_errors():
            while await self.process.stderr.read(4096):
                pass  # provider diagnostics must not expose command/env payloads

        self.stderr_task = asyncio.create_task(drain_errors())
        await asyncio.wait_for(asyncio.shield(self.ready), 1800)

    async def _read(self) -> None:
        try:
            while raw := await self.process.stdout.readline():
                value = json.loads(raw)
                if value.get("event") == "reserved":
                    self.on_reserved()
                elif value.get("event") == "progress":
                    if (
                        self.on_progress is not None
                        and value.get("executionId") == self.row["id"]
                        and value.get("generation") == self.row["generation"]
                        and isinstance(value.get("phase"), str)
                        and value["phase"] in _PROGRESS_PHASES
                        and isinstance(value.get("status"), str)
                        and value["status"] in _PROGRESS_STATUSES
                    ):
                        self.on_progress(value["phase"], value["status"])
                elif value.get("event") == "rejected":
                    if not self.ready.done():
                        self.ready.set_exception(NativeError("venue_busy", "Another execution owns the CodeSpace"))
                elif value.get("event") == "ready":
                    if value.get("capability") != "codespace-native-transport-v1":
                        raise NativeError("provider_unavailable", "Native transport capability does not match", 503)
                    if not self.ready.done():
                        self.ready.set_result(True)
                elif value.get("id") in self.pending:
                    future = self.pending.pop(value["id"])
                    if not future.done():
                        if value.get("ok"):
                            future.set_result(value["result"])
                        else:
                            future.set_exception(NativeError("provider_operation_failed", str(value.get("error")), 503))
        except Exception:
            pass
        finally:
            error = NativeError("provider_unavailable", "Native infrastructure transport disconnected", 503)
            if not self.ready.done():
                self.ready.set_exception(error)
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()

    async def request(self, method: str, params: dict | None = None) -> dict:
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            frame = {
                "id": request_id, "method": method, "params": params or {},
                "executionId": self.row["id"], "generation": self.row["generation"],
            }
            async with self.write_lock:
                self.process.stdin.write((json.dumps(frame) + "\n").encode())
                await self.process.stdin.drain()
            return await asyncio.wait_for(future, 180)
        finally:
            self.pending.pop(request_id, None)

    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.stdin.close()
            await asyncio.wait_for(self.process.wait(), 30)
        for task in (self.reader_task, self.stderr_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self.reader_task, self.stderr_task) if task is not None),
            return_exceptions=True,
        )


class NativeManager:
    def __init__(self, root: Path, provider_command, *, acp_busy=lambda name: False,
                 serving=lambda: True, transport_factory=ProviderTransport) -> None:
        self.root, self.provider_command = Path(root), provider_command
        self.acp_busy, self.serving, self.transport_factory = acp_busy, serving, transport_factory
        self.transports = {}
        self.tasks = {}
        self.locks = {}
        self.monitor_task = None

    def store(self, *, create: bool = False) -> NativeStore | None:
        if not create and not (self.root / "native-controller.sqlite").exists() and not (
            self.root / ".native-controller.initialized"
        ).exists():
            return None
        return NativeStore(self.root)

    def records(self) -> list[dict]:
        store = self.store()
        return store.active() if store else []

    def assert_acp_allowed(self, codespace: str) -> None:
        if any(row["codespace"] == codespace for row in self.records()):
            raise NativeError("native_incumbent", "Native execution ownership blocks ACP fallback")

    async def start(self, request: dict) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "The owning bridge is not accepting native launches", 503)
        spec = validate_request(request)
        if self.acp_busy(spec["codespace"]):
            raise NativeError("venue_busy", "An ACP execution already owns the CodeSpace")
        prefix = self.provider_command()
        if not prefix:
            raise NativeError("provider_unavailable", "No attributable CodeSpace provider is registered", 503)
        store = self.store(create=True)
        row, created = store.reserve(
            uuid.uuid4().hex, uuid.uuid4().hex, spec["requestId"], spec["codespace"],
            spec["owner"], signature(spec), {"spec": spec, "phase": "preparing", "represented": False},
        )
        if row["state"] not in {"stopped", "rejected"} and row["id"] not in self.tasks:
            if not created and row["id"] not in self.transports:
                row = store.update(row["id"], row["generation"], state="unreachable", represented=False)
            self._schedule(row, launch=created or not row["data"].get("launchRequested"))
        return receipt(row)

    def _schedule(self, row: dict, *, launch: bool = False) -> None:
        task = asyncio.create_task(self._prepare(row["id"], row["generation"], launch=launch))
        self.tasks[row["id"]] = task
        task.add_done_callback(lambda _: self.tasks.pop(row["id"], None))

    @staticmethod
    def _save_progress(store, execution_id, generation, phase, status):
        if (
            not isinstance(phase, str) or phase not in _PROGRESS_PHASES
            or not isinstance(status, str) or status not in _PROGRESS_STATUSES
        ):
            return
        row = store.get(execution_id, generation)
        if row["state"] not in {"starting", "unreachable"} or row["data"].get("launchRequested"):
            return
        try:
            store.update(
                execution_id, generation, phase=f"preparing/{phase}/{status}",
                allowed_states=("starting", "unreachable"),
            )
        except NativeError as exc:
            if exc.code != "state_changed":
                raise

    async def _prepare(
        self, execution_id: str, generation: str, *, launch: bool = False,
        retirement_only: bool = False,
    ) -> None:
        lock = self.locks.setdefault(execution_id, asyncio.Lock())
        async with lock:
            store = self.store()
            row = store.get(execution_id, generation)
            if row["state"] in {"stopped", "rejected"}:
                return
            if row["state"] == "stopping" and not retirement_only:
                return
            transport = self.transports.get(execution_id)
            try:
                if transport is None:
                    transport = self.transport_factory(
                        self.provider_command(), row,
                        lambda: store.update(
                            execution_id, generation, infrastructureOwned=True,
                            allowed_states=(*_LIVE, "stopping"),
                        ),
                        on_progress=lambda phase, status: self._save_progress(
                            store, execution_id, generation, phase, status,
                        ),
                    )
                    self.transports[execution_id] = transport
                    if retirement_only:
                        await transport.start(resume=True, retirement_only=True)
                    else:
                        await transport.start(resume=not launch)
                if retirement_only:
                    return
                if launch:
                    store.update(
                        execution_id, generation, launchRequested=True, phase="launching", allowed_states=_LIVE,
                    )
                    result = await transport.request("launch", {
                        "command": row["data"]["spec"]["command"], "cwd": row["data"]["spec"]["cwd"],
                    })
                else:
                    result = await transport.request("status")
                if result.get("host") and not result.get("retired"):
                    await self._prove_endpoint(result)
                    if not result.get("activated"):
                        current = store.get(execution_id, generation)
                        if current["state"] == "stopping":
                            return
                        result = await transport.request("activate")
                    result = await transport.request("status")
                self._save_result(store, execution_id, generation, result)
            except Exception as exc:
                current = store.get(execution_id, generation)
                if current["state"] not in {"stopped", "rejected"}:
                    rejected = getattr(exc, "code", "") == "venue_busy" and not current["data"].get("infrastructureOwned")
                    store.update(
                        execution_id, generation,
                        state="stopping" if current["state"] == "stopping" else (
                            "rejected" if rejected else "unreachable"
                        ),
                        represented=False, error=getattr(exc, "code", "infrastructure_unavailable"),
                        allowed_states=(*_LIVE, "stopping"),
                    )
                if transport is not None:
                    try:
                        await transport.close()
                        self.transports.pop(execution_id, None)
                    except Exception:
                        pass

    async def _prove_endpoint(self, result: dict) -> None:
        host = result["host"]
        client = await SessionHostClient.connect(port=int(result["localPort"]))
        try:
            hello, alive, _ = await asyncio.wait_for(client.probe(nonce=host["nonce"].encode()), 5)
            if hello.child_pid != host["child_pid"] or not alive:
                raise NativeError("identity_mismatch", "Forwarded native host does not own the recorded child")
        finally:
            await client.close()

    @staticmethod
    def _save_result(store, execution_id, generation, result):
        stopping = store.get(execution_id, generation)["state"] == "stopping"
        return store.update(
            execution_id, generation, state="stopping" if stopping or result.get("retired") or result["state"] == "stopped" else result["state"],
            represented=not stopping and bool(result.get("represented")), sessionId=result.get("sessionId"),
            endpoint=result, ports=result.get("ports", []), exitCode=result.get("exitCode"),
            phase="stopping" if stopping else ("ready" if result.get("represented") else "registration"),
            error=result.get("error"), retired=bool(result.get("retired")),
            allowed_states=(*_LIVE, "stopping"),
        )

    async def status(self, execution_id: str, generation: str | None = None) -> dict:
        store = self.store()
        if store is None:
            raise NativeError("not_found", "Native execution is not recorded", 404)
        row = store.get(execution_id, generation)
        if row["state"] in {"stopped", "rejected", "stopping"}:
            return receipt(row)
        if not self.serving():
            value = receipt(row)
            value.update(ready=False, represented=False, state="unreachable", phase="controller-rebinding")
            return value
        if execution_id not in self.tasks:
            if execution_id not in self.transports:
                row = store.update(
                    execution_id, row["generation"], state="unreachable", represented=False,
                    phase="recovering", allowed_states=(*_LIVE, "stopping"),
                )
                if self.serving():
                    self._schedule(row)
                value = receipt(row)
                value.update(state="unreachable", ready=False, represented=False, phase="recovering")
                return value
            else:
                try:
                    result = await self.transports[execution_id].request("status")
                    row = self._save_result(store, execution_id, row["generation"], result)
                except Exception:
                    current = store.get(execution_id, row["generation"])
                    if current["state"] not in {"stopped", "rejected"}:
                        row = store.update(
                            execution_id, row["generation"], state="unreachable", represented=False,
                            allowed_states=(*_LIVE, "stopping"),
                        )
                    transport = self.transports.get(execution_id)
                    if transport is not None:
                        try:
                            await transport.close()
                            self.transports.pop(execution_id, None)
                        except Exception:
                            pass
        return receipt(store.get(execution_id, row["generation"]))

    async def resolve(self, target: str) -> dict | None:
        store = self.store()
        if store is None:
            return None
        if target.startswith("native:"):
            return await self.status(target.removeprefix("native:"))
        matches = [
            row for row in store.active()
            if target in {row["id"], row["codespace"], f"codespace:{row['codespace']}", row["data"].get("sessionId")}
        ]
        if len(matches) > 1:
            raise NativeError("ambiguous_target", "Native target is ambiguous")
        return await self.status(matches[0]["id"]) if matches else None

    async def endpoint(self, execution_id: str, generation: str) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "Native controller is rebinding", 503)
        await self.status(execution_id, generation)
        row = self.store().get(execution_id, generation)
        endpoint = row["data"].get("endpoint")
        if (
            execution_id not in self.transports or execution_id in self.tasks
            or row["state"] in {"stopped", "stopping", "rejected"}
            or not endpoint or not endpoint.get("activated") or not endpoint.get("localPort")
        ):
            raise NativeError("not_ready", "Native transport is not ready; no replacement will be launched", 503)
        await self._prove_endpoint(endpoint)
        return endpoint

    async def stop(self, execution_id: str, generation: str) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "Native controller is rebinding", 503)
        store = self.store()
        if store is None:
            raise NativeError("not_found", "Native execution is not recorded", 404)
        row = store.get(execution_id, generation)
        if row["state"] == "stopped" and row["data"].get("retired"):
            return receipt(row)
        store.update(execution_id, generation, state="stopping", represented=False, phase="stopping")
        pending = self.tasks.get(execution_id)
        if pending is not None:
            if not store.get(execution_id, generation)["data"].get("launchRequested"):
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        row = store.get(execution_id, generation)
        if not row["data"].get("launchRequested"):
            transport = self.transports.get(execution_id)
            if transport is not None:
                await transport.close()
                self.transports.pop(execution_id, None)
            from agent_procutil import no_window_kwargs

            proc = await asyncio.create_subprocess_exec(
                *self.provider_command(), "native-abort", row["codespace"],
                "--owner", row["owner"], "--execution-id", execution_id, "--generation", generation,
                cwd=row["owner"], stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
            )
            await asyncio.wait_for(proc.communicate(), 30)
            if proc.returncode:
                raise NativeError("retirement_unconfirmed", "Unlaunched native ownership could not be released")
            proof = await self._retirement_receipt(row)
            if not proof or proof.get("noLaunch") is not True:
                raise NativeError("retirement_unconfirmed", "Unlaunched native retirement receipt is unavailable")
            return receipt(store.update(execution_id, generation, state="stopped", retired=True, phase="stopped"))
        if execution_id not in self.transports:
            cached = await self._retirement_receipt(row)
            if cached:
                return receipt(store.update(
                    execution_id, generation, state="stopped", retired=True, represented=False,
                    recovery=cached.get("recovery"), exitCode=cached.get("exitCode"), phase="stopped",
                ))
            await self._prepare(execution_id, generation, retirement_only=True)
        transport = self.transports.get(execution_id)
        if transport is None:
            raise NativeError("retirement_unconfirmed", "Native infrastructure is unavailable; ownership retained", 503)
        result = await transport.request("stop")
        if result.get("retired") is not True:
            raise NativeError("retirement_unconfirmed", "Native retirement was not verified")
        await transport.close()
        self.transports.pop(execution_id, None)
        row = store.update(execution_id, generation, state="stopped", retired=True, represented=False,
                           recovery=result.get("recovery"), exitCode=result.get("exitCode"), phase="stopped")
        return receipt(row)

    async def _retirement_receipt(self, row: dict) -> dict | None:
        from agent_procutil import no_window_kwargs

        prefix = self.provider_command()
        if not prefix:
            raise NativeError("provider_unavailable", "Native provider is unavailable", 503)
        proc = await asyncio.create_subprocess_exec(
            *prefix, "native-retirement", row["codespace"], "--owner", row["owner"],
            "--execution-id", row["id"], "--generation", row["generation"], cwd=row["owner"],
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **no_window_kwargs(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        if proc.returncode:
            raise NativeError("retirement_unconfirmed", "Native retirement receipt could not be read", 503)
        value = json.loads(out).get("receipt")
        if value is not None and (
            value.get("executionId") != row["id"] or value.get("generation") != row["generation"]
            or value.get("owner") != row["owner"] or value.get("retired") is not True
        ):
            raise NativeError("identity_mismatch", "Native retirement receipt does not match")
        return value

    async def represented(self, execution_id: str, generation: str, operation: str, params: dict) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "Native controller is rebinding", 503)
        if operation == "message":
            import math

            if not isinstance(params.get("body"), str) or not params["body"].strip() or len(params["body"].encode()) > 262144:
                raise NativeError("invalid_message", "Expected a bounded nonblank message", 400)
            identifier(params.get("messageId"))
            try:
                wait = float(params.get("waitTimeout", 120))
            except (ValueError, TypeError) as exc:
                raise NativeError("invalid_timeout", "Native reply timeout must be numeric", 400) from exc
            if not math.isfinite(wait) or not 0 < wait <= 300:
                raise NativeError("invalid_timeout", "Native reply timeout must be in (0,300]", 400)
        current = await self.status(execution_id, generation)
        if not current["ready"] or not current["sessionId"]:
            raise NativeError("unrepresented", "Native representation is unavailable; ACP fallback is forbidden", 503)
        if params.get("expectedSessionId") != current["sessionId"]:
            raise NativeError("identity_mismatch", "Native session identity changed")
        return await self.transports[execution_id].request(operation, params)

    def start_monitor(self) -> None:
        async def monitor():
            while True:
                await asyncio.sleep(3)
                if not self.serving():
                    await self.detach_infrastructure()
                    continue
                for row in self.records():
                    try:
                        current = await self.status(row["id"], row["generation"])
                        if current["state"] == "stopping":
                            await self.stop(row["id"], row["generation"])
                    except Exception:
                        continue
        self.monitor_task = asyncio.create_task(monitor())

    async def shutdown(self) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        await self.detach_infrastructure()

    async def detach_infrastructure(self) -> None:
        pending = list(self.tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for execution_id, transport in list(self.transports.items()):
            try:
                await transport.close()
                self.transports.pop(execution_id, None)
            except Exception:
                pass
