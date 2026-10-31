"""Finite workspace operations inside the existing PR cache/replay owners."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math

from .generation_cache import GenerationCache
from .owner_read import OwnerReadHandler, REQUEST_SCHEMA
from .review_executor import execute_review
from .review_intent import identifier
from .review_workspace import MAX_GENERATION_BYTES, _generation, _scope, compare_generations

PROTOCOL = "agent-pull-requests.workspace/v1"
CHUNK_BYTES = 8192
OPERATIONS = ("capabilities", "load", "sync", "lookup", "chunk", "inspect", "submit")


class WorkspaceUnavailable(RuntimeError):
    """Missing executing owner/data, not a permission or empty-success result."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class WorkspaceOperations:
    """Trusted embedding input; neither callbacks nor selected data grant authority.

    The read resolver and write factory belong to the normal authorized owner.
    Construction never provisions/adopts storage or creates a transport.
    """

    def __init__(self, store, cache, *, scope, read_owner, binding_ref,
                 write_context=None, checkpoint, max_work_seconds):
        if (
            not isinstance(cache, GenerationCache)
            or not isinstance(read_owner, OwnerReadHandler)
            or not callable(checkpoint)
            or write_context is not None and not callable(write_context)
            or type(max_work_seconds) not in (int, float)
            or not math.isfinite(max_work_seconds) or max_work_seconds <= 0
        ):
            raise ValueError("actual workspace owner inputs required")
        self.scope = _scope({**scope, "number": 1})
        self.scope.pop("number")
        if (
            cache.owner_root != store.root
            or cache.expected_descriptor["namespace"] != store.namespace
            or cache.expected_descriptor["service"] != (
                store.service_identity.document() if store.service_identity else None
            )
        ):
            raise ValueError("workspace cache must share the existing storage owner")
        self.store, self.cache = store, cache
        self.read_owner, self.binding_ref = read_owner, binding_ref
        self.write_context, self.checkpoint = write_context, checkpoint
        self.max_work_seconds = max_work_seconds

    def execute(self, operation, payload, *, expires_at, checkpoint):
        if operation not in OPERATIONS or not isinstance(payload, dict):
            raise ValueError("unsupported workspace operation")
        fields = {
            "capabilities": (set(), set()),
            "load": ({"number", "expected_current", "budget_seconds"}, set()),
            "sync": ({"number", "previous_id", "expected_current", "budget_seconds"}, set()),
            "lookup": ({"number"}, {"generation_id"}),
            "chunk": ({"number", "generation_id", "offset"}, set()),
            "inspect": ({"number", "operation_id"}, set()),
            "submit": ({"number", "intent", "source_attachment", "budget_seconds"}, set()),
        }
        required, optional = fields[operation]
        if not required <= payload.keys() or payload.keys() - required - optional:
            raise ValueError("invalid workspace fields")

        def check():
            checkpoint()
            self.checkpoint()
            if self.cache.checkpoint is not None:
                self.cache.checkpoint()
            self.store.check_current(expires_at, checkpoint)

        check()
        if operation == "capabilities":
            return {
                "protocol": PROTOCOL,
                "operations": [name for name in OPERATIONS if name != "submit" or self.write_context is not None],
                "generation_versions": [1], "owner_store_versions": [2],
                "max_generation_bytes": MAX_GENERATION_BYTES, "chunk_bytes": CHUNK_BYTES,
                "refresh": "full_sections", "source": "existing_client_local_owner",
                "provision_or_adopt": False,
            }
        number = payload["number"]
        if type(number) is not int or number <= 0:
            raise ValueError("positive workspace PR number required")
        scope = _scope({**self.scope, "number": number})
        for field in ("generation_id", "previous_id"):
            if field in payload:
                identifier(payload[field])
        # Request-local wrapper shares the existing descriptor/lock and does not
        # mutate a shared cache checkpoint while concurrent callers execute.
        cache = GenerationCache(
            self.cache.owner_root, self.store.namespace,
            max_bytes=self.cache.max_bytes, max_generations=self.cache.max_generations,
            service_identity=self.store.service_identity, checkpoint=check,
        )
        if "budget_seconds" in payload:
            seconds = payload["budget_seconds"]
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("finite workspace operation budget required")
            seconds = min(seconds, self.max_work_seconds)
        if operation in ("load", "sync"):
            previous = None
            if operation == "sync":
                previous = cache.lookup(scope, generation_id=payload["previous_id"])["generation"]
                if previous is None:
                    raise WorkspaceUnavailable("selected comparison generation unavailable")
            observed = self.read_owner.read({
                "schema": REQUEST_SCHEMA, "version": 1, "binding_ref": self.binding_ref,
                "number": number, "budget_seconds": seconds,
                "expected_principal": scope["principal_id"],
            })
            generation = observed["generation"]
            _generation(generation, scope)
            check()
            publication = cache.publish(generation, expected_current=payload["expected_current"])
            result = {
                "publication": publication, "descriptor": self._descriptor(generation),
                "acquisition": copy.deepcopy(observed["acquisition"]),
            }
            if previous is not None:
                result["comparison"] = compare_generations(generation, previous)
                result["comparison"].pop("generation")
                result["comparison"]["generation_id"] = generation["id"]
        elif operation in ("lookup", "chunk"):
            retained = cache.lookup(scope, generation_id=payload.get("generation_id"))
            generation = retained["generation"]
            if generation is None:
                raise WorkspaceUnavailable("selected immutable generation unavailable")
            result = {"descriptor": self._descriptor(generation), "current_id": retained["current_id"]}
            if operation == "chunk":
                offset = payload["offset"]
                data = encoded(generation)
                if type(offset) is not int or not 0 <= offset < len(data) or offset % CHUNK_BYTES:
                    raise ValueError("invalid immutable chunk offset")
                result.update(offset=offset, data=base64.b64encode(data[offset:offset + CHUNK_BYTES]).decode("ascii"))
        elif operation == "inspect":
            result = self.store.inspect_review(payload["operation_id"], scope, expires_at=expires_at, checkpoint=check)
        else:
            if self.write_context is None:
                raise WorkspaceUnavailable("existing protected write context unavailable")
            intent = payload["intent"]
            if not isinstance(intent, dict) or intent.get("scope") != scope:
                raise ValueError("selected review differs from authorized workspace scope")
            generation = cache.lookup(scope, generation_id=intent.get("generation_id"))["generation"]
            if generation is None:
                raise WorkspaceUnavailable("reviewed generation unavailable")
            result = execute_review(
                self.store, intent, generation, payload["source_attachment"],
                context_factory=lambda: self.write_context(number), checkpoint=check,
                expires_at=expires_at, budget_seconds=seconds,
            )
        check()
        return {"protocol": PROTOCOL, "scope": scope, "result": copy.deepcopy(result)}

    @staticmethod
    def _descriptor(generation):
        data = encoded(generation)
        if len(data) > MAX_GENERATION_BYTES:
            raise ValueError("materialized generation exceeds contract")
        return {
            "id": generation["id"], "scope": copy.deepcopy(generation["scope"]),
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }


class NativeWorkspaceClient:
    """Same finite operations under the caller's existing native owner."""

    def __init__(self, operations, *, checkpoint, expires_at):
        self.operations, self.checkpoint, self.expires_at = operations, checkpoint, expires_at

    def exchange(self, operation, payload):
        return self.operations.execute(
            operation, payload, expires_at=self.expires_at(), checkpoint=self.checkpoint,
        )

    def materialize(self, number, generation_id):
        descriptor = self.exchange("lookup", {"number": number, "generation_id": generation_id})["result"]["descriptor"]
        return materialize(descriptor, lambda offset: self.exchange(
            "chunk", {"number": number, "generation_id": generation_id, "offset": offset},
        )["result"])

    @staticmethod
    def attach_source(generation, source_owner, **limits):
        from .source_attachment import attach_source
        return attach_source(generation, source_owner, **limits)


def materialize(descriptor, read_chunk):
    if (
        not isinstance(descriptor, dict) or set(descriptor) != {"id", "scope", "bytes", "sha256"}
        or type(descriptor["bytes"]) is not int or not 0 < descriptor["bytes"] <= MAX_GENERATION_BYTES
    ):
        raise ValueError("invalid materialization descriptor")
    identifier(descriptor["id"])
    identifier(descriptor["sha256"])
    _scope(descriptor["scope"])
    data = bytearray()
    while len(data) < descriptor["bytes"]:
        chunk = read_chunk(len(data))
        if (
            not isinstance(chunk, dict) or chunk.get("descriptor") != descriptor
            or type(chunk.get("offset")) is not int or chunk["offset"] != len(data)
            or not isinstance(chunk.get("data"), str)
        ):
            raise ValueError("immutable materialization changed")
        block = base64.b64decode(chunk["data"], validate=True)
        if len(block) != min(CHUNK_BYTES, descriptor["bytes"] - len(data)):
            raise ValueError("incomplete immutable chunk")
        data.extend(block)
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ValueError("materialization content differs")
    from .watch_replay_store import json_object
    generation = json_object(bytes(data))
    _generation(generation, _scope(descriptor["scope"]))
    if generation["id"] != descriptor["id"]:
        raise ValueError("materialized generation differs")
    return generation
