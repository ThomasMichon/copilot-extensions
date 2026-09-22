"""Bridge-owned native executions over the registered CodeSpace provider.

These are execution-host records, never phantom ACP SessionManager sessions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath

from .native_store import NativeError, NativeStore, identifier, receipt, signature
from .session_host.client import SessionHostClient
from .session_host.launcher import host_spawn_kwargs
from .native_venue import key as venue_key, venue, record_venue

CAPABILITY = "codespace-native-control-v1"
_LIVE = ("starting", "ready", "unrepresented", "unreachable")
# A native execution continuously unreachable for this long is treated as a dead
# venue (deleted CodeSpace / permanently gone) and force-retired by the monitor,
# so the store + UI stop listing it. Conservative: transient dev-tunnel resets
# recover in seconds, far inside this window; only a truly gone venue reaches it.
_REAP_UNREACHABLE_SECONDS = 600.0

# Bound how long a stop will wait to (re)establish a retirement transport before
# it stops hanging. A dead/deleted venue used to block up to 30 min re-connecting
# for a retirement receipt it could never obtain; a force stop past this bound
# fast-paths to a truthful forced retirement instead.
_RETIREMENT_ESTABLISH_TIMEOUT = 60
_PROGRESS_PHASES = frozenset({
    "local-config", "owner-admission", "ssh-to-target", "target-auth-env",
    "target-binstub", "worktree", "native-host",
})
_PROGRESS_STATUSES = frozenset({"started", "reached", "failed"})


def validate_request(request: dict) -> dict:
    allowed = {"requestId", "codespace", "owner", "cwd", "command", "noPluginStaging",
               "requireRelay", "localForward", "reverseForward", "hostResources", "remoteCommand", "target"}
    if set(request) - allowed:
        raise NativeError("invalid_request", "Unknown native launch fields", 400)
    identifier(request.get("requestId"))
    venue(request)
    if "target" in request and "remoteCommand" not in request:
        raise NativeError("remote_command_required", "Provider-qualified launches require a pinned remote command", 400)
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
    if "hostResources" in request:
        from .native_resources import validate_definitions
        validate_definitions(request["hostResources"])
    if "remoteCommand" in request:
        from ssh_manager.remote_command import validate_descriptor
        try:
            validate_descriptor(request["remoteCommand"])
        except ValueError as exc:
            raise NativeError("invalid_remote_command", str(exc), 400) from exc
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
        self.close_lock = asyncio.Lock()
        self.closing = False
        self.disconnected = False
        self.drain_task = None

    async def start(self, *, resume: bool, retirement_only: bool = False) -> None:
        spec = self.row["data"]["spec"]
        name = record_venue(self.row)[1]
        argv = [
            *self.prefix, "native-transport", name,
            "--owner", self.row["owner"], "--execution-id", self.row["id"],
            "--generation", self.row["generation"], "--no-plugin-staging", "--require-relay",
        ]
        if resume:
            argv.append("--resume-infrastructure")
        if "remoteCommand" in spec:
            argv += ["--remote-command-json", json.dumps(spec["remoteCommand"])]
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
                    namespace = self.row["data"].get("spec", {}).get("target", "codespace:").split(":", 1)[0]
                    if value.get("capability") != f"{namespace}-native-transport-v1":
                        raise NativeError("provider_unavailable", "Native transport capability does not match", 503)
                    if "hostResources" in self.row["data"]["spec"] and value.get("hostResources") != "native-host-resources-v1":
                        raise NativeError("resource_capability_unavailable", "Native provider lacks host resources", 503)
                    if not self.ready.done():
                        self.ready.set_result(True)
                elif value.get("id") in self.pending:
                    future = self.pending.pop(value["id"])
                    if not future.done():
                        if value.get("ok"):
                            future.set_result(value["result"])
                        else:
                            code = "reply_transport_timeout" if value.get("errorCode") == "reply_transport_timeout" else "provider_operation_failed"
                            future.set_exception(NativeError(code, str(value.get("error")), 504 if code == "reply_transport_timeout" else 503))
        except NativeError as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
        except (ValueError, OSError):
            if not self.ready.done():
                self.ready.set_exception(NativeError("provider_unavailable", "Invalid native transport response", 503))
        finally:
            self.disconnected = True
            error = NativeError("provider_unavailable", "Native infrastructure transport disconnected", 503)
            if not self.ready.done():
                self.ready.set_exception(error)
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()

    async def request(self, method: str, params: dict | None = None) -> dict:
        if self.closing or self.disconnected:
            raise NativeError("provider_unavailable", "Native transport is closing or disconnected", 503)
        timeout = 180.0
        if method == "message" and (params or {}).get("wait"):
            import math
            wait = float(params.get("waitTimeout", 120))
            if not math.isfinite(wait) or not 0 < wait <= 300:
                raise NativeError("invalid_timeout", "Native reply timeout must be in (0,300]", 400)
            timeout = wait + 60
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
            return await asyncio.wait_for(future, timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise NativeError(
                "reply_transport_timeout", "Provider reply deadline expired; delivery remains uncertain. "
                "Inspect the result or reuse the same messageId.", 504,
            ) from exc
        except OSError as exc:
            self.disconnected = True
            raise NativeError("provider_unavailable", "Native transport disconnected; delivery is uncertain", 503) from exc
        finally:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def close(self) -> None:
        from .native_process import DRAIN_SECONDS, close_owned_process

        self.closing = True
        async with self.close_lock:
            await close_owned_process(self.process)
            if self.drain_task is None:
                self.drain_task = asyncio.gather(
                    *(task for task in (self.reader_task, self.stderr_task) if task is not None),
                    return_exceptions=True,
                )
            try:
                await asyncio.wait_for(asyncio.shield(self.drain_task), DRAIN_SECONDS)
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise NativeError("retirement_unconfirmed", "Owned local transport streams remain open", 503) from exc


class NativeManager:
    def __init__(self, root: Path, provider_command, *, acp_busy=lambda name: False,
                 serving=lambda: True, transport_factory=ProviderTransport) -> None:
        self.root, self.provider_command = Path(root), provider_command
        self.acp_busy, self.serving, self.transport_factory = acp_busy, serving, transport_factory
        self.transports = {}
        self.tasks = {}
        self.locks = {}
        self.stop_locks = {}
        self.monitor_task = None
        self.resource_tasks = {}
        self._host_resources = None
        # execId -> monotonic time it was first seen continuously unreachable, so
        # the monitor can reap a genuinely-dead venue (deleted/permanently gone)
        # after a grace window without evicting a transiently-reset live one.
        self._unreachable_since = {}

    def host_resources(self):
        if self._host_resources is None:
            from .native_resources import HostResources
            self._host_resources = HostResources(self.store(), self.serving)
        return self._host_resources

    def _schedule_resources(self, row, result):
        if row["state"] != "ready" or "hostResources" not in row["data"]["spec"]:
            return
        requests = result.get("resourceRequests", [])
        if not isinstance(requests, list) or len(requests) > 16:
            return
        for request in requests:
            if not isinstance(request, dict) or not isinstance(request.get("requestId"), str):
                continue
            key = (row["id"], row["generation"], request["requestId"])
            if key not in self.resource_tasks:
                task = asyncio.create_task(self._serve_resource(row["id"], row["generation"], request))
                self.resource_tasks[key] = task
                task.add_done_callback(lambda _, key=key: self.resource_tasks.pop(key, None))

    async def _serve_resource(self, execution, generation, request):
        from .native_resources import resource_result
        import logging

        try:
            try:
                result = await self.host_resources().ensure(execution, generation, request)
            except NativeError as exc:
                result = resource_result(request, error={"code": exc.code, "detail": exc.detail})
            transport = self.transports.get(execution)
            if self.serving() and transport is not None:
                await transport.request("resource-complete", result)
        except Exception:
            logging.getLogger("agent-bridge").warning(
                "Native resource result remains pending; retrying through the owning transport",
                exc_info=True,
            )

    async def _finish_retirement(self, execution, generation, **changes):
        """Persist provider settlement after its owned transport has closed."""
        store = self.store()
        current = store.get(execution, generation)
        if execution in self.transports or current["data"].get("transportCleanupPending"):
            raise NativeError("retirement_unconfirmed", "Owned local transport cleanup is still unconfirmed", 503)
        proof = current["data"].get("providerRetirementProof")
        if proof is not None:
            self._validate_retirement_proof(current, proof)
            changes.update(recovery=proof.get("recovery"), exitCode=proof.get("exitCode"))
        row = store.update(execution, generation, retired=True, represented=False,
                           providerRetirementConfirmed=True, **changes)
        if row["data"].get("spec", {}).get("hostResources"):
            await self.host_resources().cleanup(execution, generation)
        return receipt(store.update(execution, generation, state="stopped", phase="stopped", error=None))

    async def _close_transport(self, execution, generation, transport) -> None:
        store = self.store()
        row = store.get(execution, generation)
        if transport is None:
            if row["data"].get("transportCleanupPending"):
                raise NativeError("retirement_unconfirmed", "Owned local transport cleanup is still unconfirmed", 503)
            return
        if row["state"] == "stopping":
            store.update(execution, generation, transportCleanupPending=True)
        try:
            await transport.close()
        except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
            raise NativeError("retirement_unconfirmed", "Owned local transport cleanup is unconfirmed", 503) from exc
        store.update(execution, generation, transportCleanupPending=False)
        if self.transports.get(execution) is transport:
            self.transports.pop(execution)

    @staticmethod
    def _validate_retirement_proof(row, proof, *, require_owner=False):
        if not isinstance(proof, dict) or proof.get("retired") is not True:
            raise NativeError("retirement_unconfirmed", "Native retirement was not verified", 503)
        namespace, name = record_venue(row)
        if (
            proof.get("executionId") != row["id"] or proof.get("generation") != row["generation"]
            or (require_owner and proof.get("owner") != row["owner"])
            or ("owner" in proof and proof["owner"] != row["owner"])
            or any(field in proof and (field != namespace or proof[field] != name)
                   for field in ("codespace", "container"))
            or ("target" in proof and proof["target"] != f"{namespace}:{name}")
        ):
            raise NativeError("identity_mismatch", "Native retirement receipt does not match")
        return proof

    def _remember_retirement(self, row, proof):
        self._validate_retirement_proof(row, proof)
        store = self.store()
        current = store.get(row["id"], row["generation"])
        store.update(
            row["id"], row["generation"], providerRetirementProof=proof, retired=True, represented=False,
            recovery=proof.get("recovery"), exitCode=proof.get("exitCode"),
            transportCleanupPending=bool(
                current["data"].get("transportCleanupPending") or row["id"] in self.transports
            ),
        )

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
        if codespace.startswith("codespace:"):
            codespace = codespace.removeprefix("codespace:")
        if any(row["codespace"] == codespace for row in self.records()):
            raise NativeError("native_incumbent", "Native execution ownership blocks ACP fallback")

    async def start(self, request: dict) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "The owning bridge is not accepting native launches", 503)
        spec = validate_request(request)
        if self.acp_busy(venue_key(spec)):
            raise NativeError("venue_busy", "An ACP execution already owns the CodeSpace")
        prefix = self._provider(spec)
        if not prefix:
            raise NativeError("provider_unavailable", "No attributable venue provider is registered", 503)
        store = self.store(create=True)
        row, created = store.reserve(
            uuid.uuid4().hex, uuid.uuid4().hex, spec["requestId"], venue_key(spec),
            spec["owner"], signature(spec), {"spec": spec, "phase": "preparing", "represented": False,
                                            "providerCommand": prefix},
        )
        if row["state"] not in {"stopped", "rejected"} and row["id"] not in self.tasks:
            if not created and row["id"] not in self.transports:
                row = store.update(row["id"], row["generation"], state="unreachable", represented=False)
            self._schedule(row, launch=created or not row["data"].get("launchRequested"))
        return receipt(row)

    def _provider(self, spec: dict, row: dict | None = None):
        if row is not None and row["data"].get("providerCommand"):
            return row["data"]["providerCommand"]
        namespace, _ = venue(spec)
        return self.provider_command() if namespace == "codespace" else self.provider_command(namespace)

    def _row_provider(self, row):
        namespace, name = record_venue(row)
        return self._provider({"target": f"{namespace}:{name}"}, row)

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
                        self._row_provider(row), row,
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
                    launch_request = {
                        "command": row["data"]["spec"]["command"], "cwd": row["data"]["spec"]["cwd"],
                    }
                    if "hostResources" in row["data"]["spec"]:
                        from .native_resources import public_definitions
                        launch_request["hostResources"] = public_definitions(row["data"]["spec"]["hostResources"])
                    result = await transport.request("launch", launch_request)
                else:
                    result = await transport.request("status")
                if result.get("host") and not result.get("retired") and result.get("state") != "stopping":
                    await self._prove_endpoint(result)
                    if not result.get("activated"):
                        current = store.get(execution_id, generation)
                        if current["state"] == "stopping":
                            return
                        result = await transport.request("activate")
                    result = await transport.request("status")
                current = self._save_result(store, execution_id, generation, result)
                self._schedule_resources(current, result)
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
                        await self._close_transport(execution_id, generation, transport)
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
        current = store.get(execution_id, generation)
        if current["data"].get("providerRetirementProof") or current["data"].get("providerRetirementConfirmed"):
            return current
        stopping = current["state"] == "stopping"
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
                    self._schedule_resources(row, result)
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
                            await self._close_transport(execution_id, row["generation"], transport)
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
            if target in {row["id"], row["codespace"], ":".join(record_venue(row)), row["data"].get("sessionId")}
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

    async def stop(self, execution_id: str, generation: str, *, force: bool = False) -> dict:
        async with self.stop_locks.setdefault(execution_id, asyncio.Lock()):
            return await self._stop(execution_id, generation, force=force)

    async def _forced_retirement(self, row: dict, reason: str) -> dict:
        """Bounded forced retirement for an unreachable/gone venue.

        Releases the claim through the provider's ``native-force-retire`` (which
        records a TRUTHFUL forced receipt -- ``forced: True`` + an unsuccessful
        recovery), forcibly closes any owned transport, and marks the execution
        stopped, instead of hanging on a retirement receipt that a dead/deleted
        venue can never produce.
        """
        execution, generation = row["id"], row["generation"]
        store = self.store()
        transport = self.transports.pop(execution, None)
        if transport is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(transport.close(), timeout=10)
        if store is not None:
            store.update(execution, generation, transportCleanupPending=False)
        payload = json.loads(await self._provider_control(row, "native-force-retire"))
        proof = payload.get("receipt")
        if not isinstance(proof, dict):
            raise NativeError("retirement_unconfirmed", "Forced retirement receipt is unavailable", 503)
        self._remember_retirement(row, proof)
        return await self._finish_retirement(execution, generation)

    async def _stop(self, execution_id: str, generation: str, *, force: bool = False) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "Native controller is rebinding", 503)
        store = self.store()
        if store is None:
            raise NativeError("not_found", "Native execution is not recorded", 404)
        row = store.get(execution_id, generation)
        if (
            row["state"] == "stopped" and row["data"].get("retired")
            and execution_id not in self.transports and not row["data"].get("transportCleanupPending")
        ):
            return receipt(row)
        store.update(execution_id, generation, state="stopping", represented=False, phase="stopping")
        resources = [task for key, task in self.resource_tasks.items() if key[:2] == (execution_id, generation)]
        if resources:
            await asyncio.gather(*resources, return_exceptions=True)
        pending = self.tasks.get(execution_id)
        if pending is not None:
            if not store.get(execution_id, generation)["data"].get("launchRequested"):
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        row = store.get(execution_id, generation)
        transport = self.transports.get(execution_id)
        if row["data"].get("providerRetirementConfirmed"):
            await self._close_transport(execution_id, generation, transport)
            return await self._finish_retirement(execution_id, generation)
        proof = row["data"].get("providerRetirementProof")
        if proof is not None:
            self._validate_retirement_proof(row, proof)
            await self._close_transport(execution_id, generation, transport)
            return await self._finish_retirement(execution_id, generation)
        if not row["data"].get("launchRequested"):
            await self._close_transport(execution_id, generation, transport)
            await self._provider_control(row, "native-abort")
            proof = await self._retirement_receipt(row)
            if not proof or proof.get("noLaunch") is not True:
                raise NativeError("retirement_unconfirmed", "Unlaunched native retirement receipt is unavailable")
            self._remember_retirement(row, proof)
            return await self._finish_retirement(execution_id, generation)
        if transport is None:
            await self._close_transport(execution_id, generation, None)
            cached = await self._retirement_receipt(row)
            if cached:
                self._remember_retirement(row, cached)
                return await self._finish_retirement(execution_id, generation)
            try:
                await asyncio.wait_for(
                    self._prepare(execution_id, generation, retirement_only=True),
                    timeout=_RETIREMENT_ESTABLISH_TIMEOUT,
                )
            except (asyncio.TimeoutError, TimeoutError):
                if force:
                    return await self._forced_retirement(
                        row, "retirement transport did not become available (venue unreachable)")
                raise NativeError(
                    "retirement_unconfirmed",
                    "Native infrastructure did not become available; ownership retained (pass force to release)", 503,
                )
        transport = self.transports.get(execution_id)
        if transport is None:
            if force:
                return await self._forced_retirement(row, "retirement transport unavailable (venue unreachable)")
            raise NativeError("retirement_unconfirmed", "Native infrastructure is unavailable; ownership retained", 503)
        try:
            result = await asyncio.wait_for(transport.request("stop"), timeout=_RETIREMENT_ESTABLISH_TIMEOUT)
        except (OSError, TimeoutError, asyncio.TimeoutError, NativeError) as exc:
            if isinstance(exc, NativeError) and exc.code not in {
                "provider_unavailable", "provider_operation_failed", "reply_transport_timeout",
            }:
                raise
            proof = None
            try:
                proof = await self._retirement_receipt(row)
                if proof is not None:
                    self._remember_retirement(row, proof)
            finally:
                await self._close_transport(execution_id, generation, transport)
            if proof is None:
                if force:
                    return await self._forced_retirement(
                        row, "native transport failed and no retirement proof is available (venue unreachable)")
                raise NativeError(
                    "retirement_unconfirmed", "Native transport failed and no retirement proof is available; ownership retained", 503,
                ) from exc
        else:
            self._remember_retirement(row, result)
            await self._close_transport(execution_id, generation, transport)
        return await self._finish_retirement(execution_id, generation)

    async def _provider_control(self, row: dict, action: str) -> bytes:
        from agent_procutil import no_window_kwargs
        from .native_process import close_owned_process

        prefix = self._row_provider(row)
        if not prefix:
            raise NativeError("provider_unavailable", "Native provider is unavailable", 503)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *prefix, action, record_venue(row)[1], "--owner", row["owner"],
                "--execution-id", row["id"], "--generation", row["generation"], cwd=row["owner"],
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **no_window_kwargs(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 30)
        except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
            await close_owned_process(proc, grace=0)
            raise NativeError("retirement_unconfirmed", "Native provider settlement could not be read", 503) from exc
        except asyncio.CancelledError:
            await close_owned_process(proc, grace=0)
            raise
        if proc.returncode:
            raise NativeError("retirement_unconfirmed", "Native retirement receipt could not be read", 503)
        return out

    async def _retirement_receipt(self, row: dict) -> dict | None:
        try:
            payload = json.loads(await self._provider_control(row, "native-retirement"))
        except (ValueError, UnicodeError) as exc:
            raise NativeError("retirement_unconfirmed", "Native retirement receipt is unreadable", 503) from exc
        if not isinstance(payload, dict) or "receipt" not in payload:
            raise NativeError("retirement_unconfirmed", "Native retirement receipt payload is invalid", 503)
        value = payload["receipt"]
        return self._validate_retirement_proof(row, value, require_owner=True) if value is not None else None

    async def represented(self, execution_id: str, generation: str, operation: str, params: dict) -> dict:
        if not self.serving():
            raise NativeError("not_ready", "Native controller is rebinding", 503)
        if params.get("position") is not None and (
            not isinstance(params["position"], str) or len(params["position"]) > 2048
        ):
            raise NativeError("invalid_cursor", "Expected a bounded observation position", 400)
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

    async def _monitor_row(self, row) -> None:
        current = await self.status(row["id"], row["generation"])
        state = current["state"]
        if state == "stopping":
            await self.stop(row["id"], row["generation"])
        elif state == "unreachable":
            # Reap a venue that stays unreachable past the grace window (a
            # deleted/permanently-gone CodeSpace), so it stops lingering in the
            # store + console list and no longer churns doomed recovery each pass.
            first = self._unreachable_since.setdefault(row["id"], time.monotonic())
            if time.monotonic() - first >= _REAP_UNREACHABLE_SECONDS:
                self._unreachable_since.pop(row["id"], None)
                with contextlib.suppress(Exception):
                    await self._forced_retirement(
                        self.store().get(row["id"], row["generation"]),
                        "reaped: venue continuously unreachable beyond grace",
                    )
        else:
            # Any reachable/live state resets the unreachable clock so a
            # transiently-reset box is never reaped.
            self._unreachable_since.pop(row["id"], None)

    def start_monitor(self) -> None:
        async def monitor():
            while True:
                await asyncio.sleep(3)
                if not self.serving():
                    await self.detach_infrastructure()
                    continue
                for row in self.records():
                    try:
                        await self._monitor_row(row)
                    except Exception:
                        continue
        self.monitor_task = asyncio.create_task(monitor())

    async def shutdown(self) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        await self.detach_infrastructure()

    async def detach_infrastructure(self) -> None:
        resources = list(self.resource_tasks.values())
        for task in resources:
            task.cancel()
        await asyncio.gather(*resources, return_exceptions=True)
        pending = list(self.tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for execution_id, transport in list(self.transports.items()):
            try:
                row = self.store().get(execution_id)
                await self._close_transport(execution_id, row["generation"], transport)
            except Exception:
                pass
