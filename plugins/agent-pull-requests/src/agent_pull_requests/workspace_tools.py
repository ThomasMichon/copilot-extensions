"""Callable source tool façade; existing hosts retain auth, bindings and lifetime."""

from __future__ import annotations

import copy
import inspect
from collections import OrderedDict
import hashlib

from .source_attachment import attach_source
from .workspace_operations import PROTOCOL, encoded
from .review_workspace import MAX_GENERATION_BYTES, _generation, _scope
from .review_intent import identifier

TOOL_PROTOCOL = "agent-pull-requests.workspace-tools/v1"
COMMANDS = ("capabilities", "load", "sync", "materialize", "source", "source-read", "submit", "inspect")
FIELDS = {
    "capabilities": (),
    "load": ("number", "expected_current", "budget_seconds"),
    "sync": ("number", "expected_current", "previous_id", "budget_seconds"),
    "materialize": ("number", "generation_id"),
    "source": ("number", "generation_id", "budget_seconds"),
    "source-read": ("number", "generation_id", "budget_seconds", "source_attachment", "path", "side"),
    "submit": ("number", "intent", "source_attachment", "budget_seconds"),
    "inspect": ("number", "operation_id"),
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


class WorkspaceTools:
    """Supply only inside the normal authenticated owner's current input pipeline.

    Native calls stay under their existing synchronous owner. Async service calls
    stay on the caller's loop; this façade starts no tasks, processes or servers.
    Neither a Python type nor a capability declaration authenticates a binding.
    """

    def __init__(self, client=None, *, source_owner=None):
        if client is not None and not all(callable(getattr(client, name, None)) for name in ("exchange", "materialize")):
            raise ValueError("existing workspace client required")
        self.client, self.source_owner = client, source_owner
        self._generations = OrderedDict()
        self._native_execution = client is not None and not inspect.iscoroutinefunction(client.exchange)

    async def capabilities(self):
        operations = ["capabilities"]
        backend = None
        if self.client is not None:
            ensure = getattr(self.client, "ensure_capabilities", None)
            backend = await _call(ensure) if callable(ensure) else await _call(self.client.exchange, "capabilities", {})
            if not isinstance(backend, dict) or backend.get("protocol") != PROTOCOL:
                raise WorkspaceToolUnavailable("workspace_contract_unsupported")
            available = backend.get("operations", [])
            operations += [name for name in ("load", "sync", "submit", "inspect") if name in available]
            if all(name in available for name in ("lookup", "chunk")):
                operations.append("materialize")
                source_contract = getattr(self.source_owner, "contract", None)
                declared = source_contract() if callable(source_contract) else None
                if (
                    self._native_execution and isinstance(declared, dict)
                    and type(declared.get("version")) is int and declared == SOURCE_CONTRACT
                ):
                    operations.append("source")
                    reader = getattr(self.source_owner, "read_contract", None)
                    read = reader() if callable(reader) else None
                    if (
                        isinstance(read, dict) and type(read.get("version")) is int and read == READ_CONTRACT
                        and callable(getattr(self.source_owner, "read_retained", None))
                    ):
                        operations.append("source-read")
        return {
            "protocol": TOOL_PROTOCOL, "operations": operations,
            "owner_binding": "supplied" if self.client is not None else "unavailable",
            "backend_contract": backend, "installed_tool_adoption": False,
            "source_acquisition": False, "source_preparation": "existing_owner_not_adopted",
            "source_execution": "native_synchronous" if self._native_execution else "async_owner_unavailable",
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
        capabilities = await self.capabilities()
        if command not in capabilities["operations"]:
            raise WorkspaceToolUnavailable("workspace_operation_unsupported")
        if command in ("load", "sync", "submit", "inspect"):
            return await _call(self.client.exchange, command, copy.deepcopy(request))
        required = {"number", "generation_id"}
        if command in ("source", "source-read"):
            required.add("budget_seconds")
        if command == "source-read":
            required.update(("source_attachment", "path", "side"))
        if set(request) != required:
            raise ValueError("invalid workspace tool request fields")
        generation = await self._materialize(request["number"], request["generation_id"])
        _generation(generation, _scope(generation.get("scope")))
        if command == "materialize":
            return generation
        if command == "source":
            return attach_source(generation, self.source_owner, budget=request["budget_seconds"])
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
        return self.source_owner.read_retained(
            attachment["source"], request["path"], request["side"], budget=request["budget_seconds"],
        )

    async def _materialize(self, number, identity):
        identifier(identity)
        cached = self._generations.get(identity)
        if cached is not None:
            observed = await _call(self.client.exchange, "lookup", {"number": number, "generation_id": identity})
            if (
                observed.get("scope") != cached[0]["scope"]
                or observed.get("result", {}).get("descriptor") != cached[1]
            ):
                self._generations.pop(identity, None)
                raise WorkspaceToolUnavailable("immutable_generation_changed")
            if self._generations.get(identity) is cached:
                self._generations.move_to_end(identity)
            return copy.deepcopy(cached[0])
        generation = await _call(self.client.materialize, number, identity)
        scope = _scope(generation.get("scope"))
        _generation(generation, scope)
        if scope["number"] != number or generation["id"] != identity:
            raise ValueError("materialized selection differs")
        data = encoded(generation)
        descriptor = {"id": identity, "scope": scope, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        while self._generations and sum(item[1]["bytes"] for item in self._generations.values()) + len(data) > MAX_GENERATION_BYTES:
            self._generations.popitem(last=False)
        self._generations[identity] = (copy.deepcopy(generation), descriptor)
        return generation


async def dispatch_tool(host, name, arguments):
    if not isinstance(host, WorkspaceTools) or not isinstance(name, str) or not name.startswith("pr_workspace_"):
        raise WorkspaceToolUnavailable("workspace_tool_host_unavailable")
    if not isinstance(arguments, dict) or set(arguments) != {"request"}:
        raise ValueError("invalid workspace tool arguments")
    return await host.invoke(name.removeprefix("pr_workspace_").replace("_", "-"), arguments["request"])
