"""Callable source tool façade; existing hosts retain auth, bindings and lifetime."""

from __future__ import annotations

import copy
import inspect
from collections import OrderedDict
import hashlib
import sys

from .source_attachment import EXECUTION_CONTRACT, attach_source, attach_source_async
from .workspace_operations import PROTOCOL, encoded, needs_prepared_review, supports_prepared_review
from .review_workspace import MAX_GENERATION_BYTES, _generation, _scope
from .review_intent import identifier
from .workspace_selection import supports_selection
from .review_comment_observation import supports as supports_comment_observation
from .workspace_comment_mapping import supports as supports_mapping
from .workspace_terminal_mapping import selected as terminal_mapping_selected

TOOL_PROTOCOL = "agent-pull-requests.workspace-tools/v1"
COMMANDS = ("capabilities", "load", "sync", "materialize", "source", "source-read", "submit", "inspect", "events", "receipt",
            "prepare", "allocate", "observe-comment", "retained-comment")
FIELDS = {
    "capabilities": (),
    "load": ("number", "expected_current", "budget_seconds"),
    "sync": ("number", "expected_current", "previous_id", "budget_seconds"),
    "materialize": ("number", "generation_id"),
    "source": ("number", "generation_id", "budget_seconds"),
    "source-read": ("number", "generation_id", "budget_seconds", "source_attachment", "path", "side"),
    "submit": ("number", "intent", "source_attachment", "budget_seconds"),
    "inspect": ("number", "operation_id"),
    "receipt": ("number", "operation_id", "action_id"),
    "observe-comment": ("number", "operation_id", "action_id", "generation_id"),
    "retained-comment": ("number", "operation_id", "action_id"),
    "prepare": ("number", "intent", "identities"),
    "allocate": ("number", "intent", "source_attachment", "budget_seconds"),
    "events": ("number", "after", "limit"),
}
READ_CONTRACT = {
    "schema": "agent-pull-requests.local-source-read-contract", "version": 1,
    "fetch": False, "checkout": False, "working_tree": False,
    "range": "verified_retained_refs", "effects": "none",
}
SOURCE_CONTRACT = {
    "schema": "agent-pull-requests.local-source-contract", "version": 1,
    "backend_access": False, "fetch": False, "checkout": False,
    "retention": "immutable_git_refs", "resource_owner": "existing_workspace_owner",
}


class WorkspaceToolUnavailable(RuntimeError):
    pass


async def _call(function, *args, **kwargs):
    result = function(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _snapshot_generation(generation, number, identity):
    generation = copy.deepcopy(generation)
    scope = _scope(generation.get("scope"))
    _generation(generation, scope)
    if scope["number"] != number or generation["id"] != identity:
        raise ValueError("materialized selection differs")
    data = encoded(generation)
    return generation, {"id": identity, "scope": scope, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}, data


def _read_identity(source):
    if type(source) is not dict:
        raise ValueError("invalid retained source selection")
    identifier(source.get("id"))
    scope, revision = source.get("scope"), source.get("revision")
    if (
        type(scope) is not dict or len(scope) != 6
        or type(revision) is not dict or len(revision) not in (5, 6)
        or any(type(value) not in (str, int, type(None)) for value in (*scope.values(), *revision.values()))
    ):
        raise ValueError("invalid retained source selection")
    return source["id"], tuple(sorted(scope.items())), tuple(sorted(revision.items()))


class WorkspaceTools:
    """Supply only inside the normal authenticated owner's current input pipeline.

    Native calls stay under their existing synchronous owner. Async service calls
    stay on the caller's loop; this façade starts no tasks, processes or servers.
    Neither a Python type nor a capability declaration authenticates a binding.
    """

    def __init__(self, client=None, *, source_owner=None, comment_preparer=None):
        if client is not None and not all(callable(getattr(client, name, None)) for name in ("exchange", "materialize")):
            raise ValueError("existing workspace client required")
        self.client, self.source_owner = client, source_owner
        if comment_preparer is not None and not callable(comment_preparer):
            raise ValueError("existing comment preparation binding required")
        self.comment_preparer = comment_preparer
        self._generations = OrderedDict()
        self._native_execution = client is not None and not inspect.iscoroutinefunction(client.exchange)

    def _async_source(self):
        declaration = getattr(self.source_owner, "execution_contract", None)
        value = declaration() if callable(declaration) else None
        return (
            isinstance(value, dict) and type(value.get("version")) is int and value == EXECUTION_CONTRACT
            and inspect.iscoroutinefunction(getattr(self.source_owner, "execute", None))
            and inspect.iscoroutinefunction(getattr(self.source_owner, "attach", None))
            and callable(getattr(self.source_owner, "drain", None))
            and callable(getattr(self.source_owner, "operation", None))
        )

    def for_one_shot(self):
        """One-shot CLI cannot adopt or drain another loop's source lifetime."""
        if not self._async_source():
            return self
        view = copy.copy(self)
        view.source_owner = None
        return view

    def _preparation_mode(self):
        from .workspace_operations import NativeWorkspaceClient, _native_source_supported
        if type(self.client) is NativeWorkspaceClient:
            return "worker" if _native_source_supported(self.client) else None
        module = sys.modules.get(__package__ + ".workspace_client")
        client_type = getattr(module, "WorkspaceClient", None) if module is not None else None
        if client_type is not None and type(self.client) is client_type:
            return "loop-with-prepare" if client_type._source_supported(self.client) else None
        return None

    async def _client_call(self, function, *args, operation=None, **kwargs):
        if operation is None:
            return await _call(function, *args, **kwargs)
        mode = self._preparation_mode()
        if mode is None:
            raise WorkspaceToolUnavailable("workspace_preparation_binding_unsupported")
        if mode == "loop-with-prepare" or inspect.iscoroutinefunction(function):
            return await operation.call(function, *args, checkpoint=operation.remaining, **kwargs)
        def invoke_native(*selected_args, **selected_kwargs):
            return function(*selected_args, checkpoint=operation.remaining, **selected_kwargs)
        return await operation.call(operation.prepare, invoke_native, *args, **kwargs)

    async def capabilities(self, *, operation=None):
        operations = ["capabilities"]
        backend = None
        if self.client is not None:
            ensure = getattr(self.client, "ensure_capabilities", None)
            backend = await self._client_call(ensure, operation=operation) if callable(ensure) else await self._client_call(
                self.client.exchange, "capabilities", {}, operation=operation)
            from .workspace_terminal_read import supports as supports_terminal
            if not isinstance(backend, dict) or (
                backend.get("protocol") != PROTOCOL and not supports_terminal(backend)
            ):
                raise WorkspaceToolUnavailable("workspace_contract_unsupported")
            available = backend.get("operations", [])
            operations += [
                name for name in ("load", "sync", "submit", "inspect", "events", "receipt", "allocate",
                                  "observe-comment", "retained-comment")
                if name in available and (
                    name != "receipt" or supports_prepared_review(backend.get("prepared_review_contract"))
                ) and (name != "allocate" or supports_selection(backend.get("selection_contract")))
                and (name != "observe-comment" or (
                    supports_comment_observation(backend.get("comment_observation_contract"))
                    and supports_prepared_review(backend.get("prepared_review_contract"))
                ))
                and (name != "retained-comment" or (
                    supports_mapping(backend.get("comment_mapping_contract"))
                    and supports_prepared_review(backend.get("prepared_review_contract"))
                ))
            ]
            if terminal_mapping_selected(backend):
                operations.append("retained-comment")
            if (
                callable(self.comment_preparer) and "submit" in available
                and supports_prepared_review(backend.get("prepared_review_contract"))
            ):
                operations.append("prepare")
            if all(name in available for name in ("lookup", "chunk")):
                operations.append("materialize")
                source_contract = getattr(self.source_owner, "contract", None)
                declared = source_contract() if callable(source_contract) else None
                if (
                    ((self._native_execution and not self._async_source())
                     or (self._async_source() and self._preparation_mode() is not None))
                    and isinstance(declared, dict)
                    and type(declared.get("version")) is int and declared == SOURCE_CONTRACT
                ):
                    operations.append("source")
                    reader = getattr(self.source_owner, "read_contract", None)
                    read = reader() if callable(reader) else None
                    if (
                        isinstance(read, dict) and type(read.get("version")) is int and read == READ_CONTRACT
                        and (self._async_source() or callable(getattr(self.source_owner, "read_retained", None)))
                    ):
                        operations.append("source-read")
        return {
            "protocol": TOOL_PROTOCOL, "operations": operations,
            "owner_binding": "supplied" if self.client is not None else "unavailable",
            "backend_contract": backend, "installed_tool_adoption": False,
            "source_acquisition": False, "source_preparation": "existing_owner_not_adopted",
            **({"source_input": "immutable_generation_bytes"} if self._async_source() else {}),
            "source_execution": ("owned_async" if self._preparation_mode() is not None
                                 else "async_owner_unavailable") if self._async_source() else (
                "native_synchronous" if self._native_execution else "async_owner_unavailable"),
        }

    async def definitions(self):
        """MCP-shaped descriptors for an existing host; no registration side effect."""
        capabilities = await self.capabilities()
        identity = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
        properties = {
            "number": {"type": "integer", "minimum": 1},
            "budget_seconds": {"type": "number", "exclusiveMinimum": 0},
            "generation_id": identity, "previous_id": identity,
            "expected_current": {"anyOf": [identity, {"type": "null"}]},
            "intent": {"type": "object"}, "source_attachment": {"type": "object"},
            "path": {"type": "string", "pattern": "^/"},
            "side": {"enum": ["before", "after"]},
            "operation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "action_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "identities": {"type": "object"},
            "after": {"anyOf": [{"type": "null"}, {
                "type": "object", "properties": {
                    "epoch": {"type": "integer", "minimum": 1},
                    "sequence": {"type": "integer", "minimum": 0},
                }, "required": ["epoch", "sequence"], "additionalProperties": False,
            }]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 16},
        }
        return [{
            "name": "pr_workspace_" + command.replace("-", "_"),
            "description": f"Existing-owner PR workspace {command}; no implicit authority or replay.",
            "inputSchema": {"type": "object", "properties": {"request": {
                "type": "object", "properties": {key: copy.deepcopy(properties[key]) for key in FIELDS[command]},
                "required": list(FIELDS[command]), "additionalProperties": False,
            }},
                            "required": ["request"], "additionalProperties": False},
        } for command in capabilities["operations"]]

    async def invoke(self, command, request):
        if command not in COMMANDS or not isinstance(request, dict):
            raise ValueError("invalid workspace tool operation")
        if command == "capabilities":
            if request:
                raise ValueError("capabilities takes no request fields")
            return await self.capabilities()
        if self.client is None:
            raise WorkspaceToolUnavailable("workspace_owner_unavailable")
        if set(request) != set(FIELDS[command]):
            raise ValueError("invalid workspace tool request fields")
        request = dict(request)
        if command in ("source", "source-read") and self._async_source():
            if command == "source-read" and type(request["source_attachment"]) is not dict:
                raise ValueError("invalid source attachment")
            read_identity = _read_identity(request["source_attachment"].get("source")) if command == "source-read" else None
            if self._preparation_mode() is None:
                raise WorkspaceToolUnavailable("workspace_preparation_binding_unsupported")
            async with self.source_owner.operation(request["budget_seconds"]) as operation:
                capabilities = await self.capabilities(operation=operation)
                if command not in capabilities["operations"]:
                    raise WorkspaceToolUnavailable("workspace_operation_unsupported")
                return await self._source_command(command, request, operation=operation, read_identity=read_identity)
        capabilities = await self.capabilities()
        if command not in capabilities["operations"]:
            raise WorkspaceToolUnavailable("workspace_operation_unsupported")
        if needs_prepared_review(command, request) and not (
            command == "retained-comment" and terminal_mapping_selected(capabilities["backend_contract"])
        ) and not supports_prepared_review(
            capabilities["backend_contract"].get("prepared_review_contract")
        ):
            raise WorkspaceToolUnavailable("prepared_review_contract_unsupported")
        if command == "prepare":
            return await self._prepare_comments(request)
        if command in ("load", "sync", "submit", "inspect", "events", "receipt", "allocate",
                       "observe-comment", "retained-comment"):
            return await _call(self.client.exchange, command, copy.deepcopy(request))
        required = {"number", "generation_id"}
        if command in ("source", "source-read"):
            required.add("budget_seconds")
        if command == "source-read":
            required.update(("source_attachment", "path", "side"))
        if set(request) != required:
            raise ValueError("invalid workspace tool request fields")
        return await self._source_command(command, request)

    async def _prepare_comments(self, request: dict) -> dict:
        from .review_intent import MAX_INTENT_BYTES, intent
        from .review_preparation import _guid, validate

        request = copy.deepcopy(request)
        if len(encoded(request)) > 2 * MAX_INTENT_BYTES:
            raise ValueError("comment preparation request exceeds its byte limit")
        selected = intent(request["intent"])
        if type(request["number"]) is not int or request["number"] != selected["scope"]["number"]:
            raise ValueError("comment preparation target differs")
        identities = request["identities"]
        bodies = [action for action in selected["actions"] if "body" in action]
        if not isinstance(identities, dict) or set(identities) != {action["id"] for action in bodies}:
            raise ValueError("comment preparation requires exact selected identities")
        comments, writes = set(), set()
        for action in bodies:
            identity = identities[action["id"]]
            if not isinstance(identity, dict) or set(identity) != {"comment_id", "write_id", "previous"}:
                raise ValueError("invalid selected comment preparation")
            comment, write = _guid(identity["comment_id"]), _guid(identity["write_id"])
            if comment == write or comment in comments or write in writes:
                raise ValueError("selected comment identities must be distinct")
            comments.add(comment)
            writes.add(write)
        preparer = self.comment_preparer
        if not callable(preparer):
            raise WorkspaceToolUnavailable("comment_preparation_binding_unavailable")
        for action in bodies:
            identity = identities[action["id"]]
            prepared = await _call(
                preparer, action["kind"], action["body"], scope=copy.deepcopy(selected["scope"]), **identity,
            )
            if (
                not isinstance(prepared, dict)
                or set(prepared) != {"protocol", "identity", "body", "body_id", "effects"}
                or prepared["protocol"] != "agent-forge-comment.preparation/v1"
                or prepared["effects"] != "none"
            ):
                raise ValueError("unsupported executing comment preparation")
            binding = validate(
                {"identity": prepared["identity"], "body_id": prepared["body_id"]}, prepared["body"],
            )
            if any(binding["identity"][key] != identity[key] for key in ("comment_id", "write_id")):
                raise ValueError("preparer changed the selected comment identities")
            action.update(body=prepared["body"], preparation=binding)
        selected["version"] = 2
        return {
            "protocol": TOOL_PROTOCOL, "intent": intent(selected, versions=(2,)),
            "effects": "none", "approval_required": True,
        }

    async def _source_command(self, command, request, *, operation=None, read_identity=None):
        immutable = operation is not None and command == "source"
        materialized = await self._materialize(
            request["number"], request["generation_id"], operation=operation, immutable=immutable,
        )
        generation, data = materialized if immutable else (materialized, None)
        if command == "materialize":
            return generation
        if command == "source":
            attachment = attach_source_async if self._async_source() else attach_source
            return await _call(attachment, data if immutable else generation, self.source_owner,
                               **({"selection": generation} if immutable else {}),
                               **({"operation": operation} if operation is not None else {}),
                               budget=operation.remaining() if operation is not None else request["budget_seconds"])
        attachment = request["source_attachment"]
        if (
            not isinstance(attachment, dict) or attachment.get("schema") != "agent-pull-requests.source-attachment"
            or type(attachment.get("version")) is not int or attachment["version"] != 1
            or attachment.get("generation_id") != generation["id"]
            or attachment.get("scope") != generation["scope"]
            or attachment.get("revision") != generation["revision"] or attachment.get("state") != "ready"
            or not isinstance(attachment.get("source"), dict)
            or attachment["source"].get("scope") != generation["scope"]
            or attachment["source"].get("revision") != generation["revision"]
        ):
            raise ValueError("source attachment differs from selected generation")
        if operation is not None:
            if _read_identity(attachment["source"]) != read_identity:
                raise ValueError("source_selection_changed")
            return await operation.execute(
                "read_retained", attachment["source"], request["path"], request["side"],
                budget=operation.remaining(),
            )
        return self.source_owner.read_retained(
            attachment["source"], request["path"], request["side"], budget=request["budget_seconds"],
        )

    async def _materialize(self, number, identity, *, operation=None, immutable=False):
        identifier(identity)
        cached = self._generations.get(identity)
        if cached is not None:
            observed = await self._client_call(self.client.exchange, "lookup",
                                               {"number": number, "generation_id": identity},
                                               operation=operation)
            if (
                observed.get("scope") != cached[0]["scope"]
                or observed.get("result", {}).get("descriptor") != cached[1]
            ):
                self._generations.pop(identity, None)
                raise WorkspaceToolUnavailable("immutable_generation_changed")
            if self._generations.get(identity) is cached:
                self._generations.move_to_end(identity)
            value = await operation.prepare(copy.deepcopy, cached[0]) if operation else copy.deepcopy(cached[0])
            return (value, cached[2]) if immutable else value
        options = {}
        materializer = self.client.materialize
        if operation is not None and self._preparation_mode() == "loop-with-prepare":
            options["prepare"] = operation.prepare
        generation = await self._client_call(materializer, number, identity,
                                             operation=operation, **options)
        snapshot, descriptor, data = await operation.prepare(
            _snapshot_generation, generation, number, identity,
        ) if operation else _snapshot_generation(generation, number, identity)
        while self._generations and sum(item[1]["bytes"] for item in self._generations.values()) + descriptor["bytes"] > MAX_GENERATION_BYTES:
            self._generations.popitem(last=False)
        self._generations[identity] = (snapshot, descriptor, data)
        value = await operation.prepare(copy.deepcopy, snapshot) if operation else copy.deepcopy(snapshot)
        return (value, data) if immutable else value


async def dispatch_tool(host, name, arguments):
    if not isinstance(host, WorkspaceTools) or not isinstance(name, str) or not name.startswith("pr_workspace_"):
        raise WorkspaceToolUnavailable("workspace_tool_host_unavailable")
    if not isinstance(arguments, dict) or set(arguments) != {"request"}:
        raise ValueError("invalid workspace tool arguments")
    return await host.invoke(name.removeprefix("pr_workspace_").replace("_", "-"), arguments["request"])
