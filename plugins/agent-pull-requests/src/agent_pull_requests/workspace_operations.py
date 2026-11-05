"""Finite workspace operations inside the existing PR cache/replay owners."""

from __future__ import annotations

import base64
from collections import OrderedDict
from collections.abc import Callable
import copy
import hashlib
import json
import math
import threading
import time

from .generation_cache import GenerationCache
from .attention import Cursor
from .attention_acquisition import AttentionInputs
from .native_read import NativeReadHandler
from .workspace_conversation import conversation_key as conversation_key
from .owner_read import OwnerReadBudgetExceeded, OwnerReadHandler, REQUEST_SCHEMA
from .review_executor import execute_review
from .review_feedback import FeedbackInputs
from .review_intent import identifier
from .review_workspace import MAX_GENERATION_BYTES, _generation, _scope, compare_generations

PROTOCOL = "agent-pull-requests.workspace/v1"
CHUNK_BYTES = 8192
OPERATIONS = ("capabilities", "load", "sync", "lookup", "chunk", "inspect", "submit", "events")


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
                 write_context=None, checkpoint, max_work_seconds, conversations=False,
                 attention_owner: Callable[[dict], AttentionInputs] | None = None,
                 feedback_owner: Callable[[dict], FeedbackInputs] | None = None) -> None:
        if (
            not isinstance(cache, GenerationCache)
            or not isinstance(read_owner, (OwnerReadHandler, NativeReadHandler))
            or not callable(checkpoint)
            or write_context is not None and not callable(write_context)
            or type(max_work_seconds) not in (int, float)
            or not math.isfinite(max_work_seconds) or max_work_seconds <= 0
            or type(conversations) is not bool
            or attention_owner is not None and (not callable(attention_owner) or not conversations)
            or feedback_owner is not None and (
                not callable(feedback_owner) or not conversations or write_context is None
            )
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
        self.conversations = conversations
        self.attention_owner = attention_owner
        self.feedback_owner = feedback_owner
        self._materializations = OrderedDict()
        self._generation_positions: OrderedDict[str, dict | None] = OrderedDict()
        self._materialization_lock = threading.Lock()

    def validate_storage(self, *, expires_at: int, checkpoint: Callable[[], None]) -> None:
        """Check already-adopted state under the existing service writer."""
        self.execute("capabilities", {}, expires_at=expires_at, checkpoint=checkpoint)
        with self.cache._transaction() as lock:
            self.cache._check(lock)

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
            "events": ({"number", "after", "limit"}, set()),
        }
        required, optional = fields[operation]
        if not required <= payload.keys() or payload.keys() - required - optional:
            raise ValueError("invalid workspace fields")
        deadline = None
        if "budget_seconds" in payload:
            seconds = payload["budget_seconds"]
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("finite workspace operation budget required")
            seconds = min(seconds, self.max_work_seconds)
            deadline = time.monotonic() + seconds

        def check():
            checkpoint()
            self.checkpoint()
            if self.cache.checkpoint is not None:
                self.cache.checkpoint()
            self.store.check_current(expires_at, checkpoint)
            if deadline is not None and time.monotonic() >= deadline:
                raise OwnerReadBudgetExceeded("workspace operation budget exhausted")

        check()
        if operation == "capabilities":
            from .conversation_store import ConversationStore
            conversation_contract = None
            if self.conversations:
                conversation_contract = ConversationStore(self.store).capabilities(
                    expires_at=expires_at, checkpoint=check,
                )
            return {
                "protocol": PROTOCOL,
                "operations": [
                    name for name in OPERATIONS
                    if (name != "submit" or self.write_context is not None)
                    and (name != "events" or self.conversations)
                ],
                "generation_versions": [1], "owner_store_versions": [2],
                **({"conversation_contract": conversation_contract} if self.conversations else {}),
                **({"conversation_acquisition": "workspace-projection/v1"} if self.conversations else {}),
                "max_generation_bytes": MAX_GENERATION_BYTES, "chunk_bytes": CHUNK_BYTES,
                "refresh": "full_sections", "source": "existing_client_local_owner",
                **({"read_acquisition": self.read_owner.profile}
                   if isinstance(self.read_owner, NativeReadHandler) else {}),
                "provision_or_adopt": False,
            }
        number = payload["number"]
        if type(number) is not int or number <= 0:
            raise ValueError("positive workspace PR number required")
        scope = _scope({**self.scope, "number": number})
        if operation == "events":
            from .conversation_store import ConversationStore
            if not self.conversations:
                raise WorkspaceUnavailable("conversation operation not enabled by the existing owner")
            after = payload["after"]
            if after is not None:
                if not isinstance(after, dict) or set(after) != {"epoch", "sequence"}:
                    raise ValueError("invalid conversation cursor fields")
                after = Cursor(**after)
            result = ConversationStore(self.store).events(
                conversation_key(scope, self.store.namespace), after=after, limit=payload["limit"],
                expires_at=expires_at, checkpoint=check,
            )
            check()
            return {"protocol": PROTOCOL, "scope": scope, "result": result}
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
        if operation in ("load", "sync"):
            previous = None
            if operation == "sync":
                previous = cache.lookup(scope, generation_id=payload["previous_id"])["generation"]
                if previous is None:
                    raise WorkspaceUnavailable("selected comparison generation unavailable")
            observed = self.read_owner.read({
                "schema": REQUEST_SCHEMA, "version": 1, "binding_ref": self.binding_ref,
                "number": number, "budget_seconds": deadline - time.monotonic(),
                "expected_principal": scope["principal_id"],
            }, request_checkpoint=check)
            generation = observed["generation"]
            _generation(generation, scope)
            check()
            publication = cache.publish(generation, expected_current=payload["expected_current"])
            result = {
                "publication": publication, "descriptor": self._descriptor(generation),
                "acquisition": copy.deepcopy(observed["acquisition"]),
            }
            if self.conversations:
                from .conversation_store import ConversationStore
                attention = None
                if publication["status"] == "published" and self.attention_owner is not None:
                    attention = self.attention_owner(copy.deepcopy(generation))
                    if type(attention) is not AttentionInputs:
                        raise ValueError("actual attention owner inputs required")
                result["conversation"] = (
                    ConversationStore(self.store).observe_workspace(
                        generation, expires_at=expires_at, checkpoint=check,
                        attention=attention,
                    )
                    if publication["status"] == "published"
                    else {"status": "not_observed", "reason": "cache_publication_not_confirmed"}
                )
                observation = result["conversation"]
                if self.feedback_owner is not None and observation["status"] in ("observed", "unchanged"):
                    acquired_position = copy.deepcopy(observation["cursor"])
                    with self._materialization_lock:
                        previous_position = self._generation_positions.get(generation["id"])
                        if generation["id"] in self._generation_positions and previous_position != acquired_position:
                            acquired_position = None
                        self._generation_positions[generation["id"]] = acquired_position
                        self._generation_positions.move_to_end(generation["id"])
                        while len(self._generation_positions) > self.cache.max_generations:
                            self._generation_positions.popitem(last=False)
            if previous is not None:
                result["comparison"] = compare_generations(generation, previous)
                result["comparison"].pop("generation")
                result["comparison"]["generation_id"] = generation["id"]
        elif operation in ("lookup", "chunk"):
            descriptor, data = self._materialized(cache, scope, payload.get("generation_id"))
            result = {"descriptor": descriptor}
            if operation == "chunk":
                offset = payload["offset"]
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
            feedback = None
            feedback_position = None
            if self.feedback_owner is not None:
                feedback = self.feedback_owner(copy.deepcopy(generation))
                if type(feedback) is not FeedbackInputs:
                    raise ValueError("actual feedback owner inputs required")
                with self._materialization_lock:
                    acquired = self._generation_positions.get(generation["id"])
                    if acquired is not None:
                        feedback_position = Cursor(**acquired)
            result = execute_review(
                self.store, intent, generation, payload["source_attachment"],
                context_factory=lambda: self.write_context(number), checkpoint=check,
                expires_at=expires_at, budget_seconds=seconds, feedback=feedback,
                feedback_position=feedback_position,
            )
        check()
        return {"protocol": PROTOCOL, "scope": scope, "result": copy.deepcopy(result)}

    def _materialized(self, cache, scope, generation_id):
        from .generation_cache import _plain
        from .review_workspace import _digest

        with self._materialization_lock:
            key = (_digest(scope), generation_id)
            retained = self._materializations.get(key) if generation_id is not None else None
            if retained is not None:
                descriptor, data, stamp = retained
                # Small descriptor/file-identity checks retain the existing owner
                # boundary without reparsing/reserializing the full object N times.
                with cache._transaction() as lock:
                    path = cache.generations / (generation_id + ".json")
                    try:
                        _plain(path)
                        current = path.stat()
                    except FileNotFoundError:
                        del self._materializations[key]
                        raise WorkspaceUnavailable("selected immutable generation unavailable") from None
                    if (current.st_ino, current.st_size, current.st_mtime_ns) != stamp:
                        refreshed = cache._load_generation(generation_id, scope)
                        if encoded(refreshed) != data:
                            del self._materializations[key]
                            raise WorkspaceUnavailable("immutable generation changed")
                        stamp = (current.st_ino, current.st_size, current.st_mtime_ns)
                        self._materializations[key] = (descriptor, data, stamp)
                    cache._check(lock)
                self._materializations.move_to_end(key)
                return copy.deepcopy(descriptor), data
            with cache._transaction() as lock:
                selected_id = generation_id if generation_id is not None else cache._current(scope)
                if selected_id is None:
                    raise WorkspaceUnavailable("selected immutable generation unavailable")
                path = cache.generations / (selected_id + ".json")
                if not path.exists():
                    raise WorkspaceUnavailable("selected immutable generation unavailable")
                selected = cache._load_generation(selected_id, scope)
                _plain(path)
                state = path.stat()
                stamp = (state.st_ino, state.st_size, state.st_mtime_ns)
                cache._check(lock)
            descriptor, data = self._descriptor(selected), encoded(selected)
            key = (_digest(scope), selected["id"])
            while self._materializations and (
                sum(len(value[1]) for value in self._materializations.values()) + len(data) > MAX_GENERATION_BYTES
            ):
                self._materializations.popitem(last=False)
            self._materializations[key] = (descriptor, data, stamp)
            return copy.deepcopy(descriptor), data

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

    def preparation_contract(self):
        return {"schema": "agent-pull-requests.client-preparation",
                "version": 2 if _native_source_supported(self) else 1, "io": "worker"}

    def exchange(self, operation, payload, *, checkpoint=None):
        def current():
            self.checkpoint()
            if checkpoint is not None:
                checkpoint()
        return self.operations.execute(
            operation, payload, expires_at=self.expires_at(),
            checkpoint=current if checkpoint is not None else self.checkpoint,
        )

    def materialize(self, number, generation_id, *, checkpoint=None):
        if checkpoint is not None:
            checkpoint()
            if callable(getattr(getattr(checkpoint, "__self__", None), "_submit_native", None)) and not _native_source_supported(self):
                raise ValueError("workspace_preparation_binding_unsupported")
        options = {"checkpoint": checkpoint} if checkpoint is not None else {}
        descriptor = self.exchange("lookup", {"number": number, "generation_id": generation_id}, **options)["result"]["descriptor"]
        def chunk(offset):
            if checkpoint is not None:
                checkpoint()
                if callable(getattr(getattr(checkpoint, "__self__", None), "_submit_native", None)) and not _native_source_supported(self):
                    raise ValueError("workspace_preparation_binding_unsupported")
            return self.exchange("chunk", {
                "number": number, "generation_id": generation_id, "offset": offset,
            }, **options)["result"]
        return materialize(descriptor, chunk)

    @staticmethod
    def attach_source(generation, source_owner, **limits):
        from .source_attachment import attach_source
        return attach_source(generation, source_owner, **limits)


def _stock_preparation(client, methods):
    from types import MethodType
    return all(
        type(bound := getattr(client, name, None)) is MethodType
        and bound.__self__ is client and bound.__func__ is method
        for name, method in methods
    )


_NATIVE_PREPARATION_METHODS = tuple(
    (name, getattr(NativeWorkspaceClient, name)) for name in ("exchange", "materialize")
)


def _native_source_supported(client):
    return (
        type(client) is NativeWorkspaceClient
        and not callable(getattr(client, "ensure_capabilities", None))
        and _stock_preparation(client, _NATIVE_PREPARATION_METHODS)
    )


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
