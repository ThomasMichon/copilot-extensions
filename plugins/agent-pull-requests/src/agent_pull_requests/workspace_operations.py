"""Finite workspace operations inside the existing PR cache/replay owners."""

from __future__ import annotations

import base64
from collections import OrderedDict
from collections.abc import Callable
import copy
import hashlib
from inspect import getattr_static, iscoroutinefunction
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
from .review_comment_observation import (
    contract as comment_observation_contract, observe_prepared_comment,
    supports as supports_comment_observation,
)
from .workspace_selection import (
    allocate_selection, needs_owned_edits, owned_edit_contract, selection_contract,
    supports_owned_edits, supports_selection,
)
from .review_workspace import MAX_GENERATION_BYTES, _generation, _scope, compare_generations
from .workspace_comment_mapping import (
    available as mapping_available, contract as mapping_contract, supports as supports_mapping,
)
from . import workspace_terminal_mapping as terminal_mapping

PROTOCOL = "agent-pull-requests.workspace/v1"
CHUNK_BYTES = 8192
OPERATIONS = ("capabilities", "load", "sync", "lookup", "chunk", "inspect", "submit", "events", "receipt",
              "allocate", "observe-comment", "retained-comment")


def prepared_review_contract() -> dict[str, object]:
    return {
        "protocol": "agent-pull-requests.prepared-review/v1",
        "intent_versions": [2],
        "receipt_protocol": "agent-pull-requests.prepared-comment-receipt/v1",
    }


def supports_prepared_review(value: object) -> bool:
    expected = prepared_review_contract()
    return (
        isinstance(value, dict) and set(value) == set(expected)
        and value["protocol"] == expected["protocol"]
        and value["receipt_protocol"] == expected["receipt_protocol"]
        and type(value["intent_versions"]) is list and len(value["intent_versions"]) == 1
        and type(value["intent_versions"][0]) is int and value["intent_versions"] == [2]
    )


def needs_prepared_review(operation: str, payload: object) -> bool:
    return operation in ("receipt", "observe-comment", "retained-comment") or (
        operation == "submit" and isinstance(payload, dict) and isinstance(payload.get("intent"), dict)
        and payload["intent"].get("version") == 2
    )


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
                 feedback_owner: Callable[[dict], FeedbackInputs] | None = None,
                 prepared_reviews: bool = False,
                 comment_mappings: bool = False,
                 selection_preparer: Callable[..., Callable[..., dict]] | None = None,
                 selection_preparer_deadline: bool = False,
                 selection_owned_edits: bool = False,
                 retention_target: Callable | None = None,
                 retention_target_deadline: bool = False,
                 terminal_reads: bool = False,
                 terminal_mapping_reads: bool = False,
                 write_context_factory: Callable[..., Callable] | None = None) -> None:
        writers = (write_context, write_context_factory)
        write_available = sum(value is not None for value in writers) == 1
        if (
            not isinstance(cache, GenerationCache)
            or not isinstance(read_owner, (OwnerReadHandler, NativeReadHandler))
            or not callable(checkpoint)
            or any(value is not None and (
                not callable(value) or iscoroutinefunction(value)
            ) for value in writers)
            or all(value is not None for value in writers)
            or type(max_work_seconds) not in (int, float)
            or not math.isfinite(max_work_seconds) or max_work_seconds <= 0
            or type(conversations) is not bool
            or type(prepared_reviews) is not bool
            or type(comment_mappings) is not bool
            or type(terminal_reads) is not bool
            or type(terminal_mapping_reads) is not bool
            or terminal_mapping_reads and (not terminal_reads or not store.terminal_comment_mapping)
            or type(retention_target_deadline) is not bool
            or retention_target_deadline and retention_target is None
            or terminal_reads and (
                not conversations or retention_target is None or any(writers)
                or feedback_owner is not None or prepared_reviews or comment_mappings
                or selection_preparer is not None
                or not all((store.terminal_retention, store.terminal_recovery,
                            store.terminal_cache_retirement))
            )
            or comment_mappings and not (conversations and prepared_reviews)
            or type(selection_owned_edits) is not bool
            or type(selection_preparer_deadline) is not bool
            or selection_preparer_deadline and selection_preparer is None
            or selection_owned_edits and selection_preparer is None
            or selection_preparer is not None and (
                not callable(selection_preparer) or iscoroutinefunction(selection_preparer)
                or not prepared_reviews
            )
            or attention_owner is not None and (not callable(attention_owner) or not conversations)
            or feedback_owner is not None and (
                not callable(feedback_owner) or not conversations or not write_available
            )
            or retention_target is not None and (
                not callable(retention_target) or iscoroutinefunction(retention_target)
                or not conversations or not store.terminal_retention
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
        self.write_context_factory = write_context_factory
        self.max_work_seconds = max_work_seconds
        self.conversations = conversations
        self.attention_owner = attention_owner
        self.feedback_owner = feedback_owner
        self.prepared_reviews = prepared_reviews
        self.comment_mappings = comment_mappings
        self.selection_preparer = selection_preparer
        self.selection_preparer_deadline = selection_preparer_deadline
        self.selection_owned_edits = selection_owned_edits
        self.retention_target = retention_target
        self.retention_target_deadline = retention_target_deadline
        self.terminal_reads = terminal_reads
        self.terminal_mapping_reads = terminal_mapping_reads
        from .workspace_retention import WorkspaceRetention
        self.retention = WorkspaceRetention(self)
        self._materializations = OrderedDict()
        self._generation_positions: OrderedDict[str, dict | None] = OrderedDict()
        self._materialization_lock = threading.Lock()

    @property
    def write_available(self) -> bool:
        """Whether exactly one synchronous trusted writer is currently selected."""
        return sum(
            callable(value) and not iscoroutinefunction(value)
            for value in (self.write_context, self.write_context_factory)
        ) == 1 and not all(
            value is not None for value in (self.write_context, self.write_context_factory)
        )

    def _review_context_factory(self, scope, checkpoint, deadline):
        from agent_worktrees.providers.azure_owned_review import ReviewContextUnavailable

        legacy, resolve = self.write_context, self.write_context_factory
        selected, attempted = None, False

        def current():
            checkpoint()
            if not self.write_available or (
                self.write_context is not legacy or self.write_context_factory is not resolve
            ):
                raise ReviewContextUnavailable("selected protected write owner changed")

        def context():
            nonlocal selected, attempted
            current()
            if resolve is None:
                return legacy(scope["number"])
            if not attempted:
                attempted = True
                selected = resolve(copy.deepcopy(scope), checkpoint=current, deadline=deadline)
            if not callable(selected) or iscoroutinefunction(selected):
                raise ReviewContextUnavailable("request-local protected write factory unavailable")
            current()
            return selected()

        return context

    def validate_storage(self, *, expires_at: int, checkpoint: Callable[[], None]) -> None:
        """Check already-adopted state under the existing service writer."""
        self.execute("capabilities", {}, expires_at=expires_at, checkpoint=checkpoint)
        with self.cache._transaction() as lock:
            self.cache._check(lock)

    def execute(self, operation, payload, *, expires_at, checkpoint,
                selection_validator: Callable[[dict], None] | None = None):
        if self.terminal_reads:
            from .workspace_terminal_read import OPERATIONS as READ_OPERATIONS
            if operation not in READ_OPERATIONS and not (
                operation == "retained-comment" and terminal_mapping.available(
                    self, expires_at=expires_at, checkpoint=checkpoint,
                )
            ):
                raise WorkspaceUnavailable("terminal workspace is read-only")
        if operation not in OPERATIONS or not isinstance(payload, dict):
            raise ValueError("unsupported workspace operation")
        with self.retention.request(
            operation, payload, expires_at=expires_at, checkpoint=checkpoint,
        ) as epoch:
            return self._execute(
                operation, payload, expires_at=expires_at, checkpoint=checkpoint,
                selection_validator=selection_validator, retention_epoch=epoch,
            )

    def _execute(self, operation, payload, *, expires_at, checkpoint,
                 selection_validator: Callable[[dict], None] | None = None,
                 retention_epoch: int | None = None):
        if operation not in OPERATIONS or not isinstance(payload, dict):
            raise ValueError("unsupported workspace operation")
        fields = {
            "capabilities": (set(), set()),
            "load": ({"number", "expected_current", "budget_seconds"}, set()),
            "sync": ({"number", "previous_id", "expected_current", "budget_seconds"}, set()),
            "lookup": ({"number"}, {"generation_id"}),
            "chunk": ({"number", "generation_id", "offset"}, set()),
            "inspect": ({"number", "operation_id"}, set()),
            "receipt": ({"number", "operation_id", "action_id"}, set()),
            "observe-comment": ({"number", "operation_id", "action_id", "generation_id"}, set()),
            "retained-comment": ({"number", "operation_id", "action_id"}, set()),
            "submit": ({"number", "intent", "source_attachment", "budget_seconds"}, set()),
            "allocate": ({"number", "intent", "source_attachment", "budget_seconds"}, set()),
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
            if self.terminal_reads:
                from .workspace_terminal_read import capabilities
                return capabilities(self, expires_at=expires_at, checkpoint=check)
            from .conversation_store import ConversationStore
            conversation_contract = None
            if self.conversations:
                conversation_contract = ConversationStore(self.store).capabilities(
                    expires_at=expires_at, checkpoint=check,
                )
            prepared = self.prepared_reviews is True and self.store.review_intent_versions(
                expires_at=expires_at, checkpoint=check,
            ) == (1, 2)
            from .review_selection_store import ReviewSelectionStore
            selection = (
                prepared and self.selection_preparer is not None
                and ReviewSelectionStore(self.store).supported(expires_at=expires_at, checkpoint=check)
            )
            mapping = mapping_available(self, expires_at=expires_at, checkpoint=check)
            return {
                "protocol": PROTOCOL,
                "operations": [
                    name for name in OPERATIONS
                    if (name != "submit" or self.write_available)
                    and (name != "events" or self.conversations)
                    and (name not in ("receipt", "observe-comment") or prepared)
                    and (name != "allocate" or selection)
                    and (name != "retained-comment" or mapping)
                ],
                "generation_versions": [1], "owner_store_versions": [2],
                **({"conversation_contract": conversation_contract} if self.conversations else {}),
                **({"conversation_acquisition": "workspace-projection/v1"} if self.conversations else {}),
                "max_generation_bytes": MAX_GENERATION_BYTES, "chunk_bytes": CHUNK_BYTES,
                "refresh": "full_sections", "source": "existing_client_local_owner",
                **({"read_acquisition": self.read_owner.profile}
                   if isinstance(self.read_owner, NativeReadHandler) else {}),
                **({"prepared_review_contract": prepared_review_contract()} if prepared else {}),
                **({"comment_observation_contract": comment_observation_contract()} if prepared else {}),
                **({"comment_mapping_contract": mapping_contract()} if mapping else {}),
                **({"selection_contract": selection_contract()} if selection else {}),
                **({"owned_edit_selection_contract": owned_edit_contract()}
                   if selection and self.selection_owned_edits is True else {}),
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
            return {"protocol": self.protocol, "scope": scope, "result": result}
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
        if operation == "allocate":
            result = allocate_selection(
                self, payload, scope, cache, expires_at=expires_at, checkpoint=check,
                validate_intent=selection_validator, deadline=deadline,
            )
            check()
            return {"protocol": self.protocol, "scope": scope, "result": result}
        if operation in ("load", "sync"):
            previous = None
            if operation == "sync":
                previous = self.retention.guarded(
                    scope, lambda: cache.lookup(scope, generation_id=payload["previous_id"])["generation"],
                    expires_at=expires_at, checkpoint=check, epoch=retention_epoch,
                    generation_id=payload["previous_id"],
                )
                if previous is None:
                    raise WorkspaceUnavailable("selected comparison generation unavailable")
            terminal_target = None
            if retention_epoch is not None and self.retention_target is not None:
                terminal_target = (
                    self.retention_target(
                        copy.deepcopy(scope), expires_at=expires_at, checkpoint=check,
                    ) if self.retention_target_deadline else self.retention_target(copy.deepcopy(scope))
                )
                check()
            observed = self.read_owner.read({
                "schema": REQUEST_SCHEMA, "version": 1, "binding_ref": self.binding_ref,
                "number": number, "budget_seconds": deadline - time.monotonic(),
                "expected_principal": scope["principal_id"],
            }, request_checkpoint=check)
            generation = observed["generation"]
            _generation(generation, scope)
            check()
            retention_observation = None
            if retention_epoch is not None:
                attention = self.attention_owner(copy.deepcopy(generation)) if self.attention_owner else None
                publication, retention_observation = self.retention.publish(
                    generation, cache, expected_current=payload["expected_current"],
                    expires_at=expires_at, checkpoint=check, attention=attention,
                    comment_mappings=mapping_available(self, expires_at=expires_at, checkpoint=check),
                    target=terminal_target,
                )
            else:
                publication = cache.publish(generation, expected_current=payload["expected_current"])
            result = {
                "publication": publication,
                "descriptor": (
                    self._descriptor(generation)
                    if retention_observation is None or publication["status"] == "published" else None
                ),
                "acquisition": copy.deepcopy(observed["acquisition"]),
            }
            if self.conversations:
                from .conversation_store import ConversationStore
                attention = None
                if (
                    retention_observation is None and publication["status"] == "published"
                    and self.attention_owner is not None
                ):
                    attention = self.attention_owner(copy.deepcopy(generation))
                    if type(attention) is not AttentionInputs:
                        raise ValueError("actual attention owner inputs required")
                result["conversation"] = retention_observation if retention_observation is not None else (
                    ConversationStore(self.store).observe_workspace(
                        generation, expires_at=expires_at, checkpoint=check,
                        attention=attention,
                        comment_mappings=mapping_available(self, expires_at=expires_at, checkpoint=check),
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
            descriptor, data = self.retention.materialize(
                cache, scope, payload.get("generation_id"), expires_at=expires_at,
                checkpoint=check, epoch=retention_epoch,
            )
            result = {"descriptor": descriptor}
            if operation == "chunk":
                offset = payload["offset"]
                if type(offset) is not int or not 0 <= offset < len(data) or offset % CHUNK_BYTES:
                    raise ValueError("invalid immutable chunk offset")
                result.update(offset=offset, data=base64.b64encode(data[offset:offset + CHUNK_BYTES]).decode("ascii"))
        elif operation == "inspect":
            from .review_selection_store import ReviewSelectionStore
            owner = (
                ReviewSelectionStore(self.store).inspect
                if self.prepared_reviews and ReviewSelectionStore(self.store).supported(
                    expires_at=expires_at, checkpoint=check,
                ) else self.store.inspect_review
            )
            result = owner(payload["operation_id"], scope, expires_at=expires_at, checkpoint=check)
        elif operation == "receipt":
            if self.prepared_reviews is not True:
                raise WorkspaceUnavailable("prepared review receiver unavailable")
            result = self.store.prepared_review_receipt(
                payload["operation_id"], scope, payload["action_id"],
                expires_at=expires_at, checkpoint=check,
            )
        elif operation == "retained-comment":
            from .review_comment_mapping import CommentMappingStore
            if self.terminal_reads:
                result = terminal_mapping.read(
                    self, payload, scope, expires_at=expires_at, checkpoint=check,
                )
            else:
                if not mapping_available(self, expires_at=expires_at, checkpoint=check):
                    raise WorkspaceUnavailable("retained comment receiver unavailable")
                result = CommentMappingStore(self.store).read(
                    payload["operation_id"], scope, payload["action_id"],
                    expires_at=expires_at, checkpoint=check,
                )
            result["effects"] = "none"
        elif operation == "observe-comment":
            if self.prepared_reviews is not True or self.store.review_intent_versions(
                expires_at=expires_at, checkpoint=check,
            ) != (1, 2):
                raise WorkspaceUnavailable("prepared comment observation receiver unavailable")
            result = observe_prepared_comment(
                self.store, cache, payload["operation_id"], scope, payload["action_id"],
                payload["generation_id"], expires_at=expires_at, checkpoint=check,
            )
        else:
            if not self.write_available:
                raise WorkspaceUnavailable("existing protected write context unavailable")
            intent = payload["intent"]
            if not isinstance(intent, dict) or intent.get("scope") != scope:
                raise ValueError("selected review differs from authorized workspace scope")
            prepared = intent.get("version") == 2
            if prepared and self.prepared_reviews is not True:
                raise WorkspaceUnavailable("prepared review receiver unavailable")
            generation = cache.lookup(scope, generation_id=intent.get("generation_id"))["generation"]
            if generation is None:
                raise WorkspaceUnavailable("reviewed generation unavailable")
            feedback = None
            feedback_position = None
            feedback_current = None
            if self.feedback_owner is not None:
                feedback = self.feedback_owner(copy.deepcopy(generation))
                if type(feedback) is not FeedbackInputs:
                    raise ValueError("actual feedback owner inputs required")
                with self._materialization_lock:
                    acquired = self._generation_positions.get(generation["id"])
                    if acquired is not None:
                        feedback_position = Cursor(**acquired)

                def feedback_current():
                    latest = cache.lookup(scope)["generation"]
                    if latest is None:
                        return None
                    with self._materialization_lock:
                        position = self._generation_positions.get(latest["id"])
                        return (latest, Cursor(**position)) if position is not None else None
            result = execute_review(
                self.store, intent, generation, payload["source_attachment"],
                context_factory=self._review_context_factory(scope, check, deadline), checkpoint=check,
                expires_at=expires_at, budget_seconds=seconds, feedback=feedback,
                feedback_position=feedback_position,
                feedback_current=feedback_current,
                enclosing_deadline=deadline,
                prepared_intent=prepared,
            )
        check()
        # Storage adoption alone does not negotiate new wire metadata.
        result = copy.deepcopy(result)
        result.pop("comment_observations", None)
        if isinstance(result.get("operation"), dict):
            result["operation"].pop("comment_observations", None)
        return {"protocol": self.protocol, "scope": scope, "result": result}

    @property
    def protocol(self) -> str:
        from .workspace_terminal_read import PROTOCOL as TERMINAL_PROTOCOL
        return TERMINAL_PROTOCOL if self.terminal_reads else PROTOCOL

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
        self._prepared_contract: object = None
        self._selection_contract: object = None
        self._owned_edit_contract: object = None
        self._observation_contract: object = None
        self._mapping_contract: object = None
        self._terminal_contract: object = None
        self._terminal_mapping_contract: object = None
        self._prepared_lock = threading.Lock()
        self._discovery_epoch = 0

    def preparation_contract(self):
        return {"schema": "agent-pull-requests.client-preparation",
                "version": 2 if _native_source_supported(self) else 1, "io": "worker"}

    def exchange(self, operation, payload, *, checkpoint=None):
        epoch: int | None = None

        def current():
            self.checkpoint()
            if checkpoint is not None:
                checkpoint()
        if operation == "capabilities":
            with self._prepared_lock:
                self._discovery_epoch += 1
                epoch = self._discovery_epoch
                self._prepared_contract = None
                self._selection_contract = None
                self._owned_edit_contract = None
                self._observation_contract = None
                self._mapping_contract = None
                self._terminal_contract = None
                self._terminal_mapping_contract = None
        with self._prepared_lock:
            compact_mapping = operation == "retained-comment" and terminal_mapping.supports(
                self._terminal_mapping_contract,
            )
        if operation == "retained-comment":
            current()
            with self._prepared_lock:
                supported = compact_mapping or supports_mapping(self._mapping_contract)
            if not supported:
                raise WorkspaceUnavailable("retained comment capability required")
        if operation == "observe-comment":
            current()
            with self._prepared_lock:
                supported = supports_comment_observation(self._observation_contract)
            if not supported:
                raise WorkspaceUnavailable("comment observation capability required")
        if operation == "allocate":
            current()
            with self._prepared_lock:
                supported = supports_selection(self._selection_contract)
            if not supported:
                raise WorkspaceUnavailable("pre-approval selection capability required")
            if needs_owned_edits(operation, payload):
                with self._prepared_lock:
                    supported = supports_owned_edits(self._owned_edit_contract)
                if not supported:
                    raise WorkspaceUnavailable("owned-edit selection capability required")
        if needs_prepared_review(operation, payload) and not compact_mapping:
            current()
            with self._prepared_lock:
                supported = supports_prepared_review(self._prepared_contract)
            if not supported:
                raise WorkspaceUnavailable("prepared review capability required")
        from .workspace_terminal_read import serve, supports, PROTOCOL as TERMINAL_PROTOCOL
        with self._prepared_lock:
            terminal_contract = self._terminal_contract
        result = serve(
            self.operations, operation, payload, expires_at=self.expires_at(),
            checkpoint=current if checkpoint is not None else self.checkpoint,
            selection=terminal_mapping.SELECTION if compact_mapping else terminal_contract,
        )
        if operation == "capabilities":
            if result.get("protocol") not in (PROTOCOL, TERMINAL_PROTOCOL):
                raise WorkspaceUnavailable("unsupported workspace contract")
            if result.get("protocol") == TERMINAL_PROTOCOL and not supports(result):
                raise WorkspaceUnavailable("unsupported terminal workspace read contract")
            with self._prepared_lock:
                if epoch != self._discovery_epoch:
                    raise WorkspaceUnavailable("workspace discovery superseded")
                self._prepared_contract = copy.deepcopy(result.get("prepared_review_contract"))
                self._terminal_contract = (
                    result["terminal_read_contract"]["protocol"]
                    if result.get("protocol") == TERMINAL_PROTOCOL else None
                )
                self._terminal_mapping_contract = (
                    copy.deepcopy(result[terminal_mapping.FIELD])
                    if terminal_mapping.selected(result) else None
                )
                self._selection_contract = copy.deepcopy(result.get("selection_contract"))
                self._owned_edit_contract = copy.deepcopy(result.get("owned_edit_selection_contract"))
                self._observation_contract = (
                    copy.deepcopy(result.get("comment_observation_contract"))
                    if "observe-comment" in result.get("operations", []) else None
                )
                self._mapping_contract = (
                    copy.deepcopy(result.get("comment_mapping_contract"))
                    if "retained-comment" in result.get("operations", []) else None
                )
        if compact_mapping and not terminal_mapping.valid_result(result, payload):
            raise WorkspaceUnavailable("invalid terminal comment mapping response")
        return result

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
    from types import GetSetDescriptorType, MethodType
    namespace = getattr_static(type(client), "__dict__", None)
    if (
        type(namespace) is not GetSetDescriptorType
        or namespace.__objclass__ is not type(client) or namespace.__name__ != "__dict__"
    ):
        return False
    own = namespace.__get__(client, type(client))
    return all(
        ((bound := getattr_static(client, name, None)) is method and name not in own)
        or type(bound) is MethodType and bound.__self__ is client and bound.__func__ is method
        for name, method in methods
    )


_NATIVE_PREPARATION_METHODS = tuple(
    (name, getattr(NativeWorkspaceClient, name)) for name in ("exchange", "materialize")
)


def _native_source_supported(client):
    if type(client) is not NativeWorkspaceClient:
        return False
    missing = object()
    discovery = getattr_static(client, "ensure_capabilities", missing)
    if discovery is not missing and (
        callable(discovery) or getattr_static(type(discovery), "__get__", missing) is not missing
    ):
        return False
    return _stock_preparation(client, _NATIVE_PREPARATION_METHODS)


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
