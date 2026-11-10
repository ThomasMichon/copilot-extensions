"""Workspace peer over the existing authenticated Fleet ServiceCaller."""

from __future__ import annotations

import base64
import copy
import asyncio

from fleet_manager.gateway_service_channel import BODY_BYTES, CONTROL_RATE, TransportError

from .watch_replay_binding import ReplayBinding
from .watch_replay_client import ReplayClient, ReplayClientError
from .watch_replay_store import json_object
from .workspace_selection import needs_owned_edits, selection_contract, supports_owned_edits, supports_selection
from .review_comment_observation import contract as comment_observation_contract, supports as supports_comment_observation
from .workspace_comment_mapping import supports as supports_mapping, valid_result as valid_mapping_result
from .workspace_operations import (
    CHUNK_BYTES, MAX_GENERATION_BYTES, PROTOCOL, NativeWorkspaceClient, encoded, materialize,
    needs_prepared_review, prepared_review_contract, supports_prepared_review,
    _stock_preparation,
)
from .workspace_terminal_read import PROTOCOL as TERMINAL_PROTOCOL, supports as supports_terminal
from . import workspace_terminal_mapping as terminal_mapping


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
        terminal = self._capabilities is not None and self._capabilities["protocol"] == TERMINAL_PROTOCOL
        selection = self._capabilities["terminal_read_contract"]["protocol"] if terminal else None
        if operation == "retained-comment" and terminal_mapping.selected(self._capabilities):
            selection = terminal_mapping.SELECTION
        data = encoded({
            "binding": self._binding.document(), "request": payload,
            **({"workspace_contract": selection}
               if terminal and operation != "capabilities" else {}),
        })
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
                                       outcome_unknown=operation in ("submit", "allocate")
                                       and error.code == "outcome-unknown") from None
        if response.status != 200:
            raise WorkspaceClientError(
                "workspace_unsupported" if response.status == 404 else (
                    "workspace_access_refused" if response.status == 403 else "workspace_unavailable"
                ),
                http_status=response.status, outcome_unknown=operation in ("submit", "allocate"),
            )
        try:
            types = [value for key, value in response.headers if key.lower() == "content-type"]
            if len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json":
                raise ValueError
            value = json_object(response.body)
            protocols = (PROTOCOL, TERMINAL_PROTOCOL) if operation == "capabilities" else (
                TERMINAL_PROTOCOL if terminal else PROTOCOL,
            )
            if value.get("binding") != self._binding.document() or value.get("protocol") not in protocols:
                raise ValueError
            value.pop("binding")
            return value
        except (ValueError, UnicodeError, RecursionError):
            raise WorkspaceClientError(
                "invalid_workspace_response", outcome_unknown=operation in ("submit", "allocate"),
            ) from None

    async def capabilities(self, *, checkpoint=None):
        self._discovery_epoch += 1
        epoch = self._discovery_epoch
        self._capabilities = None
        value = await self._exchange("capabilities", {}, **(
            {"checkpoint": checkpoint} if checkpoint is not None else {}))
        terminal = value.get("protocol") == TERMINAL_PROTOCOL
        if (
            value.get("generation_versions") != [1]
            or (not supports_terminal(value) if terminal else value.get("owner_store_versions") != [2])
            or value.get("chunk_bytes") != CHUNK_BYTES
            or value.get("max_generation_bytes") != MAX_GENERATION_BYTES
            or not isinstance(value.get("operations"), list)
            or any(not isinstance(name, str) for name in value["operations"])
        ):
            raise WorkspaceClientError("unsupported_workspace_contract")
        if "events" in value["operations"] and not terminal:
            from .conversation_journal import PROTOCOL as CONVERSATION_PROTOCOL
            if value.get("conversation_contract") != {
                "protocol": CONVERSATION_PROTOCOL, "owner_store_versions": [3],
                "ingest": "trusted_owner_only", "bodies": False, "acknowledges": False,
            }:
                value["operations"] = [name for name in value["operations"] if name != "events"]
                value["unavailable_operations"] = {"events": "unsupported_conversation_contract"}
        if "receipt" in value["operations"] and not supports_prepared_review(value.get("prepared_review_contract")):
            value["operations"] = [name for name in value["operations"] if name != "receipt"]
            unavailable = value.get("unavailable_operations")
            value["unavailable_operations"] = {
                **(unavailable if isinstance(unavailable, dict) else {}),
                "receipt": "unsupported_prepared_review_contract",
            }
        if "allocate" in value["operations"] and not supports_selection(value.get("selection_contract")):
            value["operations"] = [name for name in value["operations"] if name != "allocate"]
            unavailable = value.get("unavailable_operations")
            value["unavailable_operations"] = {
                **(unavailable if isinstance(unavailable, dict) else {}),
                "allocate": "unsupported_selection_contract",
            }
        if "observe-comment" in value["operations"] and (
            not supports_comment_observation(value.get("comment_observation_contract"))
            or not supports_prepared_review(value.get("prepared_review_contract"))
        ):
            value["operations"] = [name for name in value["operations"] if name != "observe-comment"]
            unavailable = value.get("unavailable_operations")
            value["unavailable_operations"] = {
                **(unavailable if isinstance(unavailable, dict) else {}),
                "observe-comment": "unsupported_comment_observation_contract",
            }
        if not terminal and "retained-comment" in value["operations"] and (
            not supports_mapping(value.get("comment_mapping_contract"))
            or not supports_prepared_review(value.get("prepared_review_contract"))
        ):
            value["operations"] = [name for name in value["operations"] if name != "retained-comment"]
            unavailable = value.get("unavailable_operations")
            value["unavailable_operations"] = {
                **(unavailable if isinstance(unavailable, dict) else {}),
                "retained-comment": "unsupported_comment_mapping_contract",
            }
        if epoch != self._discovery_epoch:
            raise WorkspaceClientError("workspace_discovery_superseded")
        self._capabilities = copy.deepcopy(value)
        return value

    async def ensure_capabilities(self, *, checkpoint=None):
        """Share negotiation for overlapping tool calls; explicit refresh stays separate."""
        async with self._ensure_lock:
            if checkpoint is not None:
                checkpoint()
            submit = getattr(getattr(checkpoint, "__self__", None), "_submit_native", None)
            if callable(submit) and not WorkspaceClient._source_supported(self):
                raise WorkspaceClientError("workspace_native_completion_unavailable", outcome_unknown=False)
            if self._capabilities is None:
                return await self.capabilities(**({"checkpoint": checkpoint} if checkpoint is not None else {}))
            return copy.deepcopy(self._capabilities)

    async def exchange(self, operation, payload, *, checkpoint=None):
        if operation == "capabilities":
            if payload != {}:
                raise WorkspaceClientError("invalid_workspace_capability_request")
            return await self.capabilities(**({"checkpoint": checkpoint} if checkpoint is not None else {}))
        compact_mapping = operation == "retained-comment" and terminal_mapping.selected(self._capabilities)
        if self._capabilities is None or (
            operation not in self._capabilities["operations"] and not compact_mapping
        ):
            raise WorkspaceClientError("workspace_capability_required", outcome_unknown=False)
        if needs_prepared_review(operation, payload) and not compact_mapping:
            if checkpoint is not None:
                checkpoint()
            if not supports_prepared_review(self._capabilities.get("prepared_review_contract")):
                raise WorkspaceClientError("prepared_review_contract_required", outcome_unknown=False)
        if operation == "observe-comment" and not supports_comment_observation(
            self._capabilities.get("comment_observation_contract"),
        ):
            raise WorkspaceClientError("comment_observation_contract_required", outcome_unknown=False)
        if operation == "retained-comment" and not compact_mapping and not supports_mapping(
            self._capabilities.get("comment_mapping_contract"),
        ):
            raise WorkspaceClientError("comment_mapping_contract_required", outcome_unknown=False)
        if operation == "allocate" and not supports_selection(self._capabilities.get("selection_contract")):
            raise WorkspaceClientError("selection_contract_required", outcome_unknown=False)
        if needs_owned_edits(operation, payload) and not supports_owned_edits(
            self._capabilities.get("owned_edit_selection_contract"),
        ):
            raise WorkspaceClientError("owned_edit_selection_contract_required", outcome_unknown=False)
        value = await self._exchange(operation, payload, **(
            {"checkpoint": checkpoint} if checkpoint is not None else {}))
        if (
            value.get("scope") != {**self._scope, "number": payload.get("number")}
            or not isinstance(value.get("result"), dict)
        ):
            raise WorkspaceClientError(
                "workspace_scope_changed", outcome_unknown=operation in ("submit", "allocate"),
            )
        if operation == "receipt" and (
            value["result"].get("protocol") != prepared_review_contract()["receipt_protocol"]
            or value["result"].get("scope") != value["scope"]
            or value["result"].get("operation_id") != payload.get("operation_id")
            or value["result"].get("action_id") != payload.get("action_id")
        ):
            raise WorkspaceClientError("invalid_prepared_receipt_response", outcome_unknown=False)
        if operation == "observe-comment" and (
            value["result"].get("protocol") != comment_observation_contract()["result_protocol"]
            or value["result"].get("scope") != value["scope"]
            or any(value["result"].get(name) != payload.get(name)
                   for name in ("operation_id", "action_id", "generation_id"))
            or value["result"].get("effects") != "none"
            or value["result"].get("original_version_proven") is not False
            or value["result"].get("feedback_progress") is not False
            or value["result"].get("fresh_native_read") is not False
        ):
            raise WorkspaceClientError("invalid_comment_observation_response", outcome_unknown=False)
        if operation == "retained-comment" and not (
            terminal_mapping.valid_result(value, payload) if compact_mapping else valid_mapping_result(value, payload)
        ):
            raise WorkspaceClientError("invalid_retained_comment_response", outcome_unknown=False)
        if operation == "allocate":
            from .review_selection_store import ReviewSelectionStore
            try:
                result = value["result"]
                if (
                    set(result) != {"protocol", "intent", "effects", "approval_required"}
                    or result["protocol"] != selection_contract()["protocol"]
                    or result["effects"] != "none" or result["approval_required"] is not True
                ):
                    raise ValueError
                from .review_intent import intent
                ReviewSelectionStore._prepared(intent(payload["intent"]), result["intent"])
            except (ValueError, KeyError, TypeError):
                raise WorkspaceClientError("invalid_selection_response", outcome_unknown=True) from None
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
