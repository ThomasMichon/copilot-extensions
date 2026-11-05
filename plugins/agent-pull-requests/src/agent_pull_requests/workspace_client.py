"""Workspace peer over the existing authenticated Fleet ServiceCaller."""

from __future__ import annotations

import base64
import copy
import asyncio

from fleet_manager.gateway_service_channel import BODY_BYTES, CONTROL_RATE, TransportError

from .watch_replay_binding import ReplayBinding
from .watch_replay_client import ReplayClient, ReplayClientError
from .watch_replay_store import json_object
from .workspace_operations import (
    CHUNK_BYTES, MAX_GENERATION_BYTES, PROTOCOL, NativeWorkspaceClient, encoded, materialize,
    _stock_preparation,
)


class WorkspaceClientError(ReplayClientError):
    pass


class WorkspaceClient:
    """No retry/fallback, local journal, credential source or service activation."""

    def __init__(self, caller, *, route_prefix, binding: ReplayBinding, scope, read_admission=None):
        # Reuse the existing path/binding validator; do not duplicate auth.
        ReplayClient(
            caller, route_prefix=route_prefix, target=binding.target,
            repo=binding.repo, binding=binding,
        )
        self._caller, self._binding = caller, binding
        from fleet_manager.gateway_service_caller import ServiceCaller
        binder = getattr(ServiceCaller, "_bind_source_request", None)
        self._source_request = binder(caller) if callable(binder) and type(caller) is ServiceCaller else None
        from .review_workspace import _scope
        self._scope = _scope({**scope, "number": 1})
        self._scope.pop("number")
        self._path = route_prefix + "/v2/review/" + binding.target
        self._capabilities = None
        self._discovery_epoch = 0
        self._ensure_lock = asyncio.Lock()
        if read_admission is not None and not callable(read_admission):
            raise ValueError("invalid existing transport read-admission callback")
        self._read_admission = read_admission

    async def _exchange(self, operation, payload, *, checkpoint=None):
        data = encoded({"binding": self._binding.document(), "request": payload})
        if len(data) > BODY_BYTES:
            raise WorkspaceClientError("workspace_request_limit", outcome_unknown=False)
        try:
            if checkpoint is not None:
                checkpoint()
            submit = getattr(getattr(checkpoint, "__self__", None), "_submit_native", None)
            if callable(submit) and not WorkspaceClient._source_supported(self):
                raise WorkspaceClientError("workspace_native_completion_unavailable", outcome_unknown=False)
            request = self._source_request[1] if callable(submit) else self._caller.request
            args = (submit,) if callable(submit) else ()
            response = await request(
                *args, "POST", self._path + "/" + operation,
                request_headers=(("Content-Type", "application/json"),),
                body=data, response_limit=BODY_BYTES,
            )
        except TransportError as error:
            raise WorkspaceClientError(error.code, http_status=error.status,
                                       outcome_unknown=operation == "submit" and error.code == "outcome-unknown") from None
        if response.status != 200:
            raise WorkspaceClientError(
                "workspace_unsupported" if response.status == 404 else (
                    "workspace_access_refused" if response.status == 403 else "workspace_unavailable"
                ),
                http_status=response.status, outcome_unknown=operation == "submit",
            )
        try:
            types = [value for key, value in response.headers if key.lower() == "content-type"]
            if len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json":
                raise ValueError
            value = json_object(response.body)
            if value.get("binding") != self._binding.document() or value.get("protocol") != PROTOCOL:
                raise ValueError
            value.pop("binding")
            return value
        except (ValueError, UnicodeError, RecursionError):
            raise WorkspaceClientError("invalid_workspace_response", outcome_unknown=operation == "submit") from None

    async def capabilities(self, *, checkpoint=None):
        self._discovery_epoch += 1
        epoch = self._discovery_epoch
        self._capabilities = None
        value = await self._exchange("capabilities", {}, **(
            {"checkpoint": checkpoint} if checkpoint is not None else {}))
        if (
            value.get("generation_versions") != [1] or value.get("owner_store_versions") != [2]
            or value.get("chunk_bytes") != CHUNK_BYTES
            or value.get("max_generation_bytes") != MAX_GENERATION_BYTES
            or not isinstance(value.get("operations"), list)
            or any(not isinstance(name, str) for name in value["operations"])
        ):
            raise WorkspaceClientError("unsupported_workspace_contract")
        if "events" in value["operations"]:
            from .conversation_journal import PROTOCOL as CONVERSATION_PROTOCOL
            if value.get("conversation_contract") != {
                "protocol": CONVERSATION_PROTOCOL, "owner_store_versions": [3],
                "ingest": "trusted_owner_only", "bodies": False, "acknowledges": False,
            }:
                value["operations"] = [name for name in value["operations"] if name != "events"]
                value["unavailable_operations"] = {"events": "unsupported_conversation_contract"}
        if epoch != self._discovery_epoch:
            raise WorkspaceClientError("workspace_discovery_superseded")
        self._capabilities = copy.deepcopy(value)
        return value

    async def ensure_capabilities(self, *, checkpoint=None):
        """Share negotiation for overlapping tool calls; explicit refresh stays separate."""
        async with self._ensure_lock:
            if checkpoint is not None:
                checkpoint()
            if self._capabilities is None:
                return await self.capabilities(**({"checkpoint": checkpoint} if checkpoint is not None else {}))
            return copy.deepcopy(self._capabilities)

    async def exchange(self, operation, payload, *, checkpoint=None):
        if operation == "capabilities":
            if payload != {}:
                raise WorkspaceClientError("invalid_workspace_capability_request")
            return await self.capabilities(**({"checkpoint": checkpoint} if checkpoint is not None else {}))
        if self._capabilities is None or operation not in self._capabilities["operations"]:
            raise WorkspaceClientError("workspace_capability_required", outcome_unknown=False)
        value = await self._exchange(operation, payload, **(
            {"checkpoint": checkpoint} if checkpoint is not None else {}))
        if (
            value.get("scope") != {**self._scope, "number": payload.get("number")}
            or not isinstance(value.get("result"), dict)
        ):
            raise WorkspaceClientError("workspace_scope_changed", outcome_unknown=operation == "submit")
        return value

    def preparation_contract(self):
        version = 2 if self._source_supported() else 1
        return {"schema": "agent-pull-requests.client-preparation", "version": version, "io": "loop-with-prepare"}

    def _source_supported(self):
        if (
            self._source_request is None or self._caller._access is not self._source_request[0]
            or self._read_admission is not None or not _stock_preparation(self, _SOURCE_PREPARATION_METHODS)
        ):
            return False
        selected = self._source_request[2]
        return _stock_preparation(self._caller, (("request", selected.__func__),))

    async def materialize(self, number, generation_id, *, prepare=None, checkpoint=None):
        def source_binding():
            submit = getattr(getattr(checkpoint, "__self__", None), "_submit_native", None)
            if callable(submit) and not WorkspaceClient._source_supported(self):
                raise WorkspaceClientError("workspace_native_completion_unavailable", outcome_unknown=False)
        source_binding()
        options = {"checkpoint": checkpoint} if checkpoint is not None else {}
        lookup = await self.exchange("lookup", {"number": number, "generation_id": generation_id}, **options)
        source_binding()
        descriptor = lookup["result"].get("descriptor")
        # The same validator assembles both native and remote generations. Network
        # IO stays asynchronous, with no implicit retry after a lost chunk.
        if (
            not isinstance(descriptor, dict) or set(descriptor) != {"id", "scope", "bytes", "sha256"}
            or type(descriptor.get("bytes")) is not int or not 0 < descriptor["bytes"] <= MAX_GENERATION_BYTES
            or descriptor["id"] != generation_id or descriptor["scope"] != lookup["scope"]
        ):
            raise WorkspaceClientError("invalid_workspace_descriptor")
        from .review_intent import identifier
        try:
            identifier(descriptor["sha256"])
            identifier(descriptor["id"])
        except ValueError:
            raise WorkspaceClientError("invalid_workspace_descriptor") from None
        chunks = {}
        for offset in range(0, descriptor["bytes"], CHUNK_BYTES):
            source_binding()
            if checkpoint is not None:
                checkpoint()
            if self._read_admission is None:
                # Each SDK request takes a fresh shared Gateway challenge. Do not
                # spend its burst on one large immutable read; leave headroom.
                await asyncio.sleep(2 / CONTROL_RATE)
            else:
                await self._read_admission()
            source_binding()
            value = await self.exchange("chunk", {
                "number": number, "generation_id": generation_id, "offset": offset,
            }, **options)
            chunks[offset] = value["result"]
            # Reject corruption immediately rather than retain another full
            # invalid generation while waiting for later pages.
            result = chunks[offset]
            if (
                result.get("descriptor") != descriptor or type(result.get("offset")) is not int
                or result["offset"] != offset or not isinstance(result.get("data"), str)
            ):
                raise WorkspaceClientError("workspace_chunk_changed")
            try:
                block = base64.b64decode(result["data"], validate=True)
            except ValueError:
                raise WorkspaceClientError("workspace_chunk_invalid") from None
            if len(block) != min(CHUNK_BYTES, descriptor["bytes"] - offset):
                raise WorkspaceClientError("workspace_chunk_incomplete")
        def assemble():
            try:
                return materialize(descriptor, chunks.__getitem__)
            except (ValueError, KeyError):
                raise WorkspaceClientError("invalid_workspace_materialization") from None
        if prepare is not None:
            return await prepare(assemble)
        return assemble()

    attach_source = staticmethod(NativeWorkspaceClient.attach_source)


_SOURCE_PREPARATION_METHODS = tuple(
    (name, getattr(WorkspaceClient, name))
    for name in ("_exchange", "exchange", "capabilities", "ensure_capabilities", "materialize")
)
