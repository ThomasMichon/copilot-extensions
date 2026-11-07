"""Protected selected edits through actual preparation, disk and native owners."""

from __future__ import annotations

import copy
import json

import pytest

from agent_pull_requests import watch_replay_store as storage
from agent_pull_requests.review_executor import execute_review
from agent_pull_requests.review_selection_store import ReviewSelectionStore
from agent_pull_requests.review_workspace import _digest, _record
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.watch_storage import StateCommitUncertain
from test_review_executor import environment as environment
from test_review_owner_store import expires, response
from test_workspace_comment_preparation import comment_preparer as comment_preparer


@pytest.fixture
def edits(environment: tuple, comment_preparer: tuple) -> tuple:
    store, selected, _, _, _, _ = environment
    store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires())
    selections = ReviewSelectionStore(store)
    selections.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    selected["actions"] = selected["actions"][:1]
    return environment, selections, *comment_preparer


def allocate(edits: tuple, *, source: bool = True) -> dict:
    (store, selected, generation, attachment, _, _), selections, compose, _ = edits
    return selections.allocate(
        selected, compose=compose, expires_at=expires(),
        generation=generation if source else None, source_attachment=attachment if source else None,
    )["operation"]["intent"]


def submit(edits: tuple, chosen: dict) -> dict:
    (store, _, generation, attachment, backend, _), _, _, _ = edits
    return execute_review(
        store, chosen, generation, attachment, context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires(), budget_seconds=60, prepared_intent=True,
    )


def refresh(edits: tuple, operation: str, *, content: str | None = None) -> None:
    (_, selected, generation, attachment, backend, _), _, _, _ = edits
    if content is not None:
        backend.threads[0]["comments"][0]["content"] = content
    generation["sections"]["discussions"]["data"] = [
        _record("discussions", thread) for thread in backend.threads
    ]
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    selected.update(operation_id=operation, generation_id=generation["id"])
    attachment["generation_id"] = generation["id"]
    selected["actions"] = [{
        "id": "finding", "kind": "edit_owned_comment", "body": "Intentionally revised note.",
        "thread_id": backend.threads[0]["id"], "comment_id": backend.threads[0]["comments"][0]["id"],
        "expected_content_id": _digest(backend.threads[0]["comments"][0]["content"]),
    }]


def seed_legacy(edits: tuple, content: str = "Original untagged note.") -> None:
    (_, selected, _, _, backend, _), _, _, _ = edits
    backend.threads = [{"id": 100, "status": 1, "comments": [{
        "id": 1, "parentCommentId": 0, "commentType": 1, "content": content,
        "author": {"id": selected["scope"]["principal_id"]},
    }]}]
    refresh(edits, "selected-legacy-edit")


def test_native_create_then_repeated_edits_preserve_logical_identity_and_original_receipts(edits: tuple) -> None:
    (store, selected, _, _, backend, _), selections, _, state = edits
    first = allocate(edits)
    assert backend.calls == []
    assert submit(edits, first)["status"] == "complete"
    logical = first["actions"][0]["preparation"]["identity"]["comment_id"]
    writes = {first["actions"][0]["preparation"]["identity"]["write_id"]}
    for index in range(2):
        refresh(edits, f"intentional-edit-{index}")
        calls = list(backend.calls)
        # The real preparer may replace only this owner's bound original marker.
        selected["actions"][0]["body"] = backend.threads[0]["comments"][0]["content"].replace(
            "Synthetic selected finding" if index == 0 else "Changed once", "Changed once" if index == 0 else "Changed twice",
        )
        chosen = allocate(edits)
        identity = chosen["actions"][0]["preparation"]["identity"]
        assert identity["comment_id"] == logical and identity["write_id"] not in writes
        writes.add(identity["write_id"])
        assert backend.calls == calls
        original = copy.deepcopy(selected)
        before = store.path.read_bytes()
        state["profile"]["rules"][0]["header"] = "[Changed after preview] "
        selections.owner = ReplayStore(store.root, store.namespace)
        recovered = allocate(edits, source=False)
        assert recovered == chosen and store.path.read_bytes() == before and selected == original
        assert submit(edits, chosen)["status"] == "complete"
        count = len(backend.calls)
        assert submit(edits, chosen)["entered_writes"] == 0
        assert len([row for row in backend.calls[count:] if row[0] != "GET"]) == 0
        assert store.prepared_review_receipt(
            chosen["operation_id"], chosen["scope"], "finding", expires_at=expires(),
        )["identity"] == identity
    assert len(writes) == 3
    assert store.prepared_review_receipt(
        first["operation_id"], first["scope"], "finding", expires_at=expires(),
    )["identity"] == first["actions"][0]["preparation"]["identity"]


@pytest.mark.parametrize("quoted", [False, True])
def test_selected_legacy_edit_allocates_once_without_backfill(edits: tuple, quoted: bool) -> None:
    (store, selected, _, _, backend, _), selections, _, state = edits
    content = "Original untagged note."
    if quoted:
        content += "\n\n> <!-- agent-comment:v1 copied example -->"
    seed_legacy(edits, content)
    selected["actions"][0]["body"] = content + "\n\nSelected correction."
    original = copy.deepcopy(selected)
    chosen = allocate(edits)
    identity = chosen["actions"][0]["preparation"]["identity"]
    assert identity["comment_id"] != identity["write_id"] and backend.calls == []
    assert state["results"][-1]["body"] == chosen["actions"][0]["body"]
    assert selections.recover(original, expires_at=expires())["selection"]["stage"] == "prepared"
    assert submit(edits, chosen)["status"] == "complete"
    assert backend.threads[0]["comments"][0]["content"] == chosen["actions"][0]["body"]
    assert len(json.loads(store.path.read_bytes())["reviews"]) == 1


@pytest.mark.parametrize("change", ["author", "target", "content", "source", "scope"])
def test_wrong_protected_selection_refuses_without_composition_or_commit(edits: tuple, change: str) -> None:
    (store, selected, generation, attachment, backend, _), _, _, state = edits
    seed_legacy(edits)
    if change == "author":
        backend.threads[0]["comments"][0]["author"]["id"] = "other-native-author"
        refresh(edits, selected["operation_id"])
    elif change == "target":
        selected["actions"][0]["comment_id"] = 2
    elif change == "content":
        selected["actions"][0]["expected_content_id"] = _digest("Wrong baseline")
    elif change == "source":
        attachment["source"]["revision"]["target_sha"] = "9" * 40
        attachment["source"]["id"] = _digest({
            key: value for key, value in attachment["source"].items() if key != "id"
        })
        selected["source_id"] = attachment["source"]["id"]
    else:
        selected["scope"]["number"] += 1
    before = store.path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        allocate(edits)
    assert store.path.read_bytes() == before and state["results"] == [] and backend.calls == []


@pytest.mark.parametrize("change", ["unbound", "changed", "foreign-scope"])
def test_copied_marker_or_unmatched_prepared_history_cannot_become_legacy(edits: tuple, change: str) -> None:
    (store, selected, _, _, _, _), _, compose, _ = edits
    if change in ("unbound", "foreign-scope"):
        prepared = compose(
            "comment", "Unbound note.", scope=selected["scope"],
            comment_id="11111111-1111-4111-8111-111111111111",
            write_id="22222222-2222-4222-8222-222222222222", previous=None,
        )
        if change == "foreign-scope":
            chosen = copy.deepcopy(selected)
            chosen.update(version=2, operation_id="foreign-prepared")
            chosen["scope"]["number"] += 1
            chosen["actions"][0].update(
                body=prepared["body"],
                preparation={"identity": prepared["identity"], "body_id": prepared["body_id"]},
            )
            store.admit_review(chosen, expires_at=expires())
            begun = store.begin_review_action(chosen["operation_id"], chosen["scope"], "finding", expires_at=expires())
            store.record_review_receipt(
                chosen["operation_id"], chosen["scope"], "finding", begun["action"]["attempt_id"],
                response(thread_id=100, comment_id=1), expires_at=expires(),
            )
        seed_legacy(edits, prepared["body"])
    else:
        chosen = allocate(edits)
        assert submit(edits, chosen)["status"] == "complete"
        refresh(edits, "manually-changed-baseline", content="Manually edited; marker removed.")
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="unbound outer|unambiguous original"):
        allocate(edits)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("kind,ids,blocked", [
    ("comment", {}, True),
    ("comment", {"thread_id": 100, "comment_id": "1"}, True),
    ("reply", {"thread_id": 100}, True),
    ("edit_owned_comment", {}, True),
    ("comment", {"thread_id": 200, "comment_id": 1}, False),
])
def test_unknown_effects_only_exclude_a_target_when_native_ids_do(
    edits: tuple, kind: str, ids: dict, blocked: bool,
) -> None:
    (store, selected, _, _, backend, _), _, _, _ = edits
    seed_legacy(edits)
    uncertain = copy.deepcopy(selected)
    uncertain["operation_id"] = "earlier-unknown"
    action = uncertain["actions"][0]
    if kind == "comment":
        for field in ("thread_id", "comment_id", "expected_content_id"):
            action.pop(field)
    elif kind == "reply":
        action.pop("comment_id")
        action.pop("expected_content_id")
    action["kind"] = kind
    store.admit_review(uncertain, expires_at=expires())
    begun = store.begin_review_action("earlier-unknown", selected["scope"], "finding", expires_at=expires())
    if ids:
        store.record_review_receipt(
            "earlier-unknown", selected["scope"], "finding", begun["action"]["attempt_id"],
            response(**ids), expires_at=expires(), confirm=False,
        )
    before = store.path.read_bytes()
    if blocked:
        with pytest.raises(ValueError, match="unresolved native effect"):
            allocate(edits)
        assert store.path.read_bytes() == before
    else:
        assert allocate(edits)["actions"][0]["kind"] == "edit_owned_comment"
    assert store.inspect_review(
        "earlier-unknown", selected["scope"], expires_at=expires(),
    )["actions"][0]["state"] == "unknown"
    assert backend.calls == []


def test_conflicting_native_histories_refuse_even_when_one_body_matches(edits: tuple) -> None:
    (store, selected, _, _, _, _), selections, compose, _ = edits
    first = allocate(edits)
    assert submit(edits, first)["status"] == "complete"
    other = copy.deepcopy(selected)
    other.update(operation_id="conflicting-create")
    other["actions"][0]["body"] = "Different logical comment."
    chosen = selections.allocate(other, compose=compose, expires_at=expires())["operation"]["intent"]
    store.admit_review(chosen, expires_at=expires())
    begun = store.begin_review_action(chosen["operation_id"], chosen["scope"], "finding", expires_at=expires())
    store.record_review_receipt(
        chosen["operation_id"], chosen["scope"], "finding", begun["action"]["attempt_id"],
        response(thread_id=100, comment_id=1), expires_at=expires(),
    )
    refresh(edits, "ambiguous-edit", content=chosen["actions"][0]["body"])
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="unambiguous original"):
        allocate(edits)
    assert store.path.read_bytes() == before


def test_lost_edit_preview_recovers_without_source_or_another_identity(
    edits: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (store, selected, _, _, backend, _), selections, _, state = edits
    seed_legacy(edits)
    write = storage._atomic_write_json

    def lost(path: object, document: dict) -> None:
        write(path, document)
        raise StateCommitUncertain("synthetic committed edit preview lost")

    with monkeypatch.context() as failure:
        failure.setattr(storage, "_atomic_write_json", lost)
        with pytest.raises(StateCommitUncertain):
            allocate(edits)
    retained = selections.recover(selected, expires_at=expires())
    selections.owner = ReplayStore(store.root, store.namespace)
    assert allocate(edits, source=False) == retained["intent"]
    assert len(state["results"]) == 1 and backend.calls == []
    selected["actions"][0]["body"] += " Changed input."
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="different original selection"):
        allocate(edits)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("duplicate", [False, True])
def test_selected_legacy_targets_each_get_one_distinct_logical_identity(edits: tuple, duplicate: bool) -> None:
    (store, selected, _, _, backend, _), _, _, state = edits
    seed_legacy(edits)
    backend.threads[0]["comments"].append({
        **copy.deepcopy(backend.threads[0]["comments"][0]), "id": 2,
    })
    refresh(edits, "multiple-selected-legacy-edits")
    second = {**copy.deepcopy(selected["actions"][0]), "id": "second", "comment_id": 1 if duplicate else 2}
    selected["actions"].append(second)
    before = store.path.read_bytes()
    if duplicate:
        with pytest.raises(ValueError, match="one selected edit per native comment"):
            allocate(edits)
        assert store.path.read_bytes() == before and state["results"] == []
    else:
        chosen = allocate(edits)
        assert len({action["preparation"]["identity"]["comment_id"] for action in chosen["actions"]}) == 2
        assert len(state["results"]) == 2
    assert backend.calls == []
