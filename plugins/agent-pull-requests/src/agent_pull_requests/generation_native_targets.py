"""Original native targets captured before workspace acquisition, within its owner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .conversation_journal import ConversationJournal
from .review_workspace import _digest, _scope
from .watch_replay import ConsumerScope
from .watch_replay_store import ReplayStore, ReplayStoreInvalid
from .workspace_conversation import conversation_key

Document = dict[str, Any]
PROTOCOL = "agent-pull-requests.workspace-native-generation-associations/v1"
FIELDS = {"native_target", "acquisition_epoch", "acquisition_state"}
_STATES = {"open", "merged", "closed-unmerged"}


def contract() -> Document:
    return {
        "protocol": PROTOCOL, "storage_extension": "generation_associations_version:2",
        "selection": "fresh_baseline_only", "capture": "before_acquisition",
        "legacy_backfill": False, "terminal_adoption": False,
    }


@dataclass(frozen=True)
class NativeGenerationCapture:
    root: Path
    namespace: str
    service: str
    workspace: str
    account: str
    repo: str
    number: int
    epoch: int | None
    state: str | None


def _service(owner: ReplayStore) -> str:
    return _digest(owner.service_identity.document() if owner.service_identity else None)


def _position(document: Document, scope: Document) -> tuple[int | None, str | None]:
    record = ConversationJournal(document["conversations"])._find(
        conversation_key(scope, document["namespace"]),
    )
    return (record["epoch"], record["current"]["state"]) if record is not None else (None, None)


def validate(row: Document) -> None:
    target = row["native_target"]
    if not isinstance(target, dict) or set(target) != {"account", "repo", "number"}:
        raise ValueError("invalid original native target")
    ConsumerScope("native-association", target["account"], target["repo"])
    epoch, state = row["acquisition_epoch"], row["acquisition_state"]
    if (
        type(target["number"]) is not int or target["number"] != row["scope"]["number"]
        or (epoch is None) != (state is None)
        or epoch is not None and (
            type(epoch) is not int or not 1 <= epoch <= row["cursor"]["epoch"]
            or state not in _STATES
        )
    ):
        raise ValueError("invalid original native acquisition position")


def capture(
    owner: ReplayStore, scope: Document, native: tuple[str, str], *,
    expires_at: int, checkpoint: Callable[[], None],
) -> NativeGenerationCapture:
    if not owner.native_generation_associations:
        raise ValueError("native generation associations require explicit selection")
    scope = _scope(scope)
    if not isinstance(native, tuple) or len(native) != 2:
        raise ValueError("original native account/repository required")
    admitted = ConsumerScope("native-association", *native)

    def original(document: Document) -> NativeGenerationCapture:
        if document.get("generation_associations_version") != 2:
            raise ReplayStoreInvalid("native generation association storage unavailable")
        if owner.recorded_terminal_cache:
            target = {"account": admitted.account, "repo": admitted.repo, "number": scope["number"]}
            if any(
                row["scope"] == scope and row["target"] != target
                for row in document.get("terminal_retention", {}).get("admissions", [])
            ):
                raise ReplayStoreInvalid("native selection differs from the original imported admission")
        epoch, state = _position(document, scope)
        return NativeGenerationCapture(
            owner.root, owner.namespace, _service(owner), _digest(scope),
            admitted.account, admitted.repo, scope["number"], epoch, state,
        )

    return owner._review_transaction(
        original, expires_at=expires_at, checkpoint=checkpoint, persist=False,
    )


def check(
    owner: ReplayStore, document: Document, scope: Document,
    captured: NativeGenerationCapture | None, row: Document | None,
) -> None:
    if type(captured) is not NativeGenerationCapture or (
        captured.root != owner.root or captured.namespace != owner.namespace
        or captured.service != _service(owner) or captured.workspace != _digest(scope)
        or captured.number != scope["number"]
    ):
        raise ReplayStoreInvalid("original pre-acquisition native target required")
    target = {"account": captured.account, "repo": captured.repo, "number": captured.number}
    position = _position(document, scope)
    if row is not None and row["native_target"] != target:
        raise ReplayStoreInvalid("generation belongs to a different original native target")
    if position == (captured.epoch, captured.state):
        return
    # A reservation may have created the journal before its acknowledgement
    # was lost. Reconcile that original row, not a newly inferred acquisition.
    if row is not None and (
        position == (row["cursor"]["epoch"], row["state"])
        and (captured.epoch, captured.state) == (
            row["acquisition_epoch"], row["acquisition_state"],
        )
    ):
        return
    raise ReplayStoreInvalid("workspace changed during original native acquisition")


def fields(captured: NativeGenerationCapture) -> Document:
    return {
        "native_target": {
            "account": captured.account, "repo": captured.repo, "number": captured.number,
        },
        "acquisition_epoch": captured.epoch, "acquisition_state": captured.state,
    }
