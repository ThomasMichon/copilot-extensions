"""Original acquisition identities in the existing replay envelope, not cache guesses."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

from .attention import Cursor, PRKey
from .conversation_journal import ConversationJournal
from .conversation_store import ConversationStore
from .generation_cache import GenerationCache
from .review_workspace import _digest, _generation, _scope
from .watch_replay_store import ReplayStore, ReplayStoreInvalid
from .workspace_conversation import conversation_key
from . import generation_native_targets as native

Document = dict[str, Any]
PROTOCOL = "agent-pull-requests.workspace-generation-associations/v1"
_FIELDS = {
    "scope", "generation_id", "cursor", "observation_id", "complete", "state",
    "expected_current", "publication",
}


class _ObservationRefused(RuntimeError):
    """A read-only observation refusal prevents the enclosing cache write."""


def contract() -> Document:
    """Declare optional support from the executing core, independently of its build."""
    return {
        "protocol": PROTOCOL, "storage_extension": "generation_associations_version:1",
        "selection": "fresh_baseline_only", "legacy_backfill": False,
    }


def _identifier(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _bound(owner: ReplayStore, cache: GenerationCache) -> None:
    if (
        cache.owner_root != owner.root
        or cache.expected_descriptor["namespace"] != owner.namespace
        or cache.expected_descriptor["service"] != (
            owner.service_identity.document() if owner.service_identity else None
        )
    ):
        raise ValueError("generation associations require the original cache owner")


def validate(document: Document, namespace: str) -> None:
    """Validate bounded identity-only metadata without opening the cache."""
    state = document["generation_associations"]
    if (
        type(document["generation_associations_version"]) is not int
        or document["generation_associations_version"] not in (1, 2)
        or document["version"] != 3
        or not isinstance(state, dict) or set(state) != {"protocol", "limits", "records"}
        or state["protocol"] != (
            native.PROTOCOL if document["generation_associations_version"] == 2 else PROTOCOL
        )
    ):
        raise ValueError("invalid generation association contract")
    limits = state["limits"]
    if (
        not isinstance(limits, dict) or set(limits) != {"max_bytes", "max_generations"}
        or type(limits["max_bytes"]) is not int or not 65536 <= limits["max_bytes"] <= 1024 ** 3
        or type(limits["max_generations"]) is not int or not 1 <= limits["max_generations"] <= 10000
        or not isinstance(state["records"], list)
        or len(state["records"]) > limits["max_generations"]
    ):
        raise ValueError("invalid generation association limits")
    journal = ConversationJournal(document["conversations"])
    records = {PRKey(**item["current"]["key"]): item for item in journal.records}
    seen: set[str] = set()
    for row in state["records"]:
        if not isinstance(row, dict) or set(row) != _FIELDS | (
            native.FIELDS if document["generation_associations_version"] == 2 else set()
        ):
            raise ValueError("invalid generation association")
        scope = _scope(row["scope"])
        cursor = row["cursor"]
        if not isinstance(cursor, dict) or set(cursor) != {"epoch", "sequence"}:
            raise ValueError("invalid original generation cursor")
        position = Cursor(**cursor)
        record = records.get(conversation_key(scope, namespace))
        if (
            not _identifier(row["generation_id"]) or row["generation_id"] in seen
            or not _identifier(row["observation_id"])
            or type(row["complete"]) is not bool
            or row["state"] not in ("open", "merged", "closed-unmerged")
            or row["publication"] not in ("pending", "confirmed")
            or row["expected_current"] is not None and not _identifier(row["expected_current"])
            or record is None or position.epoch > record["epoch"]
            or position.epoch == record["epoch"] and position.sequence > record["sequence"]
            or position.sequence < 1
        ):
            raise ValueError("invalid original generation association")
        seen.add(row["generation_id"])
        if document["generation_associations_version"] == 2:
            native.validate(row)
    from .review_intent import encoded
    if len(encoded(state)) > document["capacities"]["generation_association_bytes"]:
        raise ValueError("generation associations exceed their reserved capacity")


def selected(document: Document, cache: GenerationCache) -> Document:
    """Require already-provisioned support and the original cache limits."""
    state = document.get("generation_associations")
    if not isinstance(state, dict) or state["limits"] != cache.expected_descriptor["limits"]:
        raise ReplayStoreInvalid("selected generation association storage unavailable or mismatched")
    return state


def provision(
    owner: ReplayStore, cache: GenerationCache, *, expires_at: int,
    checkpoint: Callable[[], None], lifecycle_checkpoint: Callable[[], None],
) -> None:
    """Initialize only the explicitly selected fresh baseline under its writer lease."""
    if not owner.workspace_generation_associations or owner.recorded_terminal_cache:
        raise ValueError("generation associations require explicit selection")
    _bound(owner, cache)

    def initialize(document: Document) -> None:
        lifecycle_checkpoint()
        if (
            document["version"] != 3 or document["reviews"] or document["replay"]["records"]
            or document["conversations"]["records"]
            or any(name.endswith("_version") for name in document)
            or cache.root.exists() or cache.root.is_symlink()
        ):
            raise ReplayStoreInvalid("generation associations require a fresh baseline owner")
        limits = {"max_bytes": cache.max_bytes, "max_generations": cache.max_generations}
        document["generation_associations_version"] = 2 if owner.native_generation_associations else 1
        document["generation_associations"] = {
            "protocol": native.PROTOCOL if owner.native_generation_associations else PROTOCOL,
            "limits": limits, "records": [],
        }
        document["capacities"]["generation_association_bytes"] = (
            1024 + 4096 * limits["max_generations"]
        )
        lifecycle_checkpoint()

    owner._review_transaction(initialize, expires_at=expires_at, checkpoint=checkpoint)


def publish(
    owner: ReplayStore, cache: GenerationCache, generation: Document, *,
    expected_current: str | None, expires_at: int, checkpoint: Callable[[], None],
    native_capture: native.NativeGenerationCapture | None = None,
) -> tuple[Document, Document]:
    """Reserve original custody after cache CAS admission but before its writes."""
    if not owner.workspace_generation_associations or owner.recorded_terminal_cache:
        raise ValueError("generation associations require explicit selection")
    _bound(owner, cache)
    scope = _scope(generation["scope"])
    _generation(generation, scope)
    if expected_current is not None and not _identifier(expected_current):
        raise ValueError("invalid expected generation")

    def execute(document: Document, commit: Callable[[], None]) -> tuple[Document, Document]:
        state = selected(document, cache)
        row = next(
            (item for item in state["records"] if item["generation_id"] == generation["id"]),
            None,
        )
        if owner.native_generation_associations:
            native.check(owner, document, scope, native_capture, row)
        elif native_capture is not None:
            raise ValueError("native generation capture requires its selected writer")
        journal = ConversationJournal(document["conversations"])
        record = journal._find(conversation_key(scope, owner.namespace))
        if row is not None and (
            row["scope"] != scope or record is None or row["cursor"]["epoch"] != record["epoch"]
        ):
            raise ReplayStoreInvalid("generation belongs to a different original scope or epoch")
        pending = row is not None and row["publication"] == "pending"
        if pending and row is not None:
            if row["expected_current"] != expected_current:
                raise ReplayStoreInvalid("uncertain generation publication requires its original CAS")
            if record is None or row["state"] != record["current"]["state"]:
                raise ReplayStoreInvalid("original publication no longer has its observed state")
        observation: Document = {
            "status": "not_observed", "reason": "cache_publication_not_confirmed",
        }
        current = cache.lookup(scope)["current_id"]
        # A lost pointer result can be re-acknowledged, never promoted over a newer pointer.
        expected = (
            generation["id"] if row is not None and row["expected_current"] == expected_current
            and current == generation["id"]
            else expected_current
        )
        if pending and row is not None and record is not None and (
            row["cursor"] != {"epoch": record["epoch"], "sequence": record["sequence"]}
            and current != generation["id"]
        ):
            return {"status": "not_published", "effects": "none"}, {
                "status": "unavailable", "reason": "original_publication_no_longer_current",
            }
        def reserve() -> None:
            nonlocal row, observation
            if pending and row is not None:
                observation = {"status": "unchanged", "cursor": copy.deepcopy(row["cursor"])}
                return
            observation = ConversationStore(owner)._observe_workspace(document, generation)
            if observation["status"] not in ("observed", "unchanged"):
                raise _ObservationRefused
            if row is not None and row["cursor"]["epoch"] != observation["cursor"]["epoch"]:
                raise ReplayStoreInvalid("generation cannot be reassigned to a new observation epoch")
            if row is None:
                if len(state["records"]) >= state["limits"]["max_generations"]:
                    raise ReplayStoreInvalid("generation association capacity exhausted")
                original = ConversationJournal(document["conversations"])._find(
                    conversation_key(scope, owner.namespace),
                )
                if original is None:
                    raise ReplayStoreInvalid("original generation observation unavailable")
                row = {
                    "scope": copy.deepcopy(scope), "generation_id": generation["id"],
                    "cursor": copy.deepcopy(observation["cursor"]),
                    "observation_id": _digest(original["current"]),
                    "complete": original["current"]["complete"],
                    "state": original["current"]["state"],
                    "expected_current": expected_current, "publication": "pending",
                    **(
                        native.fields(native_capture)
                        if owner.native_generation_associations and native_capture is not None else {}
                    ),
                }
                state["records"].append(row)
                commit()

        try:
            publication = cache.publish(generation, expected_current=expected, before_write=reserve)
        except _ObservationRefused:
            return {"status": "not_published", "effects": "none"}, observation
        if publication["status"] == "published":
            if row is None:
                raise ReplayStoreInvalid("publication has no original generation reservation")
            row["publication"] = "confirmed"
        return publication, observation

    return owner._review_transaction(
        execute, expires_at=expires_at, checkpoint=checkpoint, staged=True,
    )
