"""Body-free cursors and atomic existing-owner storage with synthetic observations."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent_pull_requests.attention import Candidate, Cursor, PRKey
from agent_pull_requests.conversation_journal import ConversationJournal, Observation
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain


def observation(**changes):
    return replace(
        Observation(
            PRKey("github", "https://github.com", "example/project", 7, "account-a", "owner-a"),
            Candidate("a" * 40, "b" * 40),
            100,
            101,
            "open",
            True,
            "author-a",
            ("reviewer-a",),
            ("reviewer",),
            "unknown",
            None,
            (("comments", 1),),
            (("comments", "1" * 64),),
        ),
        **changes,
    )


def test_same_head_dialogue_allocates_a_cursor_but_duplicate_polls_and_stale_reads_do_not():
    journal = ConversationJournal()
    initial = observation()
    assert journal.observe(initial, expected=None)["cursor"] == {"epoch": 1, "sequence": 1}
    assert journal.observe(initial, expected=None)["status"] == "unchanged"
    poll = replace(initial, observed_from=102, observed_to=103)
    assert journal.observe(poll, expected=Cursor(1, 0))["status"] == "conflict"
    assert journal.observe(poll, expected=Cursor(1, 1))["status"] == "unchanged"
    assert journal.observe(initial, expected=Cursor(1, 1))["status"] == "stale_observation"
    changed = replace(poll, observed_from=104, observed_to=105, sections=(("comments", "2" * 64),))
    assert journal.observe(changed, expected=Cursor(1, 0))["status"] == "conflict"
    assert journal.observe(changed, expected=Cursor(1, 1))["cursor"] == {"epoch": 1, "sequence": 2}
    page = journal.events(initial.key, after=Cursor(1, 1), limit=1, now=106)
    assert page["events"] == [
        {
            "sequence": 2,
            "kind": "changed",
            "candidate": {"head": "a" * 40, "base": "b" * 40},
            "observed_at": 105,
            "changed": ["comments"],
        }
    ]
    assert page["next"] == {"epoch": 1, "sequence": 2}
    assert journal.events(initial.key, after=Cursor(1, 1), limit=1, now=106) == page
    assert ConversationJournal(journal.snapshot()).snapshot() == journal.snapshot()


@pytest.mark.parametrize("state", ["merged", "closed-unmerged"])
def test_terminal_compacts_only_the_journal_and_reopen_starts_a_new_epoch(state):
    journal = ConversationJournal()
    initial = observation()
    journal.observe(initial, expected=None)
    partial = observation(observed_from=102, observed_to=103, state=state, complete=False)
    journal.observe(partial, expected=Cursor(1, 1))
    assert len(journal.snapshot()["records"][0]["events"]) == 2
    terminal = replace(
        partial,
        observed_from=104,
        observed_to=105,
        complete=True,
        verdict="approved",
        review_rounds=2,
    )
    journal.observe(terminal, expected=Cursor(1, 2))
    page = journal.events(initial.key, after=Cursor(1, 0), limit=16, now=106)
    assert page["status"] == "resync_required" and page["next"] is None
    current = page["current"]
    assert current["sections"] == {} and current["author"] == "author-a"
    assert current["reviewers"] == ["reviewer-a"] and current["our_roles"] == ["reviewer"]
    assert current["review_rounds"] == 2 and current["counts"] == {"comments": 1}
    assert len(journal.snapshot()["records"][0]["events"]) == 1
    reopened = observation(observed_from=106, observed_to=107)
    with pytest.raises(ValueError, match="explicit complete reopen"):
        journal.observe(reopened, expected=Cursor(1, 3))
    assert journal.observe(reopened, expected=Cursor(1, 3), reopen=True)["cursor"] == {
        "epoch": 2,
        "sequence": 1,
    }
    restored = ConversationJournal(journal.snapshot())
    page = restored.events(initial.key, after=Cursor(1, 3), limit=16, now=108)
    assert page["status"] == "resync_required"
    assert page["previous_terminal"] == current
    assert restored.events(initial.key, after=None, limit=16, now=108)["status"] == "snapshot"


def test_count_age_gaps_and_pages_are_explicit_and_never_advance_the_requested_cursor(monkeypatch):
    import agent_pull_requests.conversation_journal as module

    monkeypatch.setattr(module, "MAX_EVENTS", 2)
    monkeypatch.setattr(module, "RETENTION_SECONDS", 10)
    journal = ConversationJournal()
    initial = observation()
    journal.observe(initial, expected=None)
    for sequence in (2, 3, 4):
        journal.observe(
            observation(
                observed_from=100 + sequence * 2,
                observed_to=101 + sequence * 2,
                sections=(("comments", str(sequence) * 64),),
            ),
            expected=Cursor(1, sequence - 1),
        )
    assert (
        journal.events(initial.key, after=Cursor(1, 1), limit=1, now=110)["status"]
        == "resync_required"
    )
    page = journal.events(initial.key, after=Cursor(1, 2), limit=1, now=110)
    assert page["more"] and page["next"] == {"epoch": 1, "sequence": 3}
    page = journal.events(initial.key, after=Cursor(**page["next"]), limit=1, now=110)
    assert not page["more"] and page["next"] == {"epoch": 1, "sequence": 4}
    assert journal.events(initial.key, after=Cursor(1, 4), limit=1, now=120)["events"] == []
    assert (
        journal.events(initial.key, after=Cursor(1, 3), limit=1, now=120)["status"]
        == "resync_required"
    )


def test_quiet_polls_expire_persisted_suffix_without_allocating_or_acknowledging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_pull_requests.conversation_journal as module

    monkeypatch.setattr(module, "RETENTION_SECONDS", 10)
    journal = ConversationJournal()
    initial = observation()
    journal.observe(initial, expected=None)
    for sequence in (2, 3):
        journal.observe(
            observation(
                observed_from=100 + sequence * 2,
                observed_to=101 + sequence * 2,
                sections=(("comments", str(sequence) * 64),),
            ),
            expected=Cursor(1, sequence - 1),
        )
    before = journal.snapshot()
    poll = observation(
        observed_from=110, observed_to=115, sections=(("comments", "3" * 64),),
    )
    assert journal.observe(poll, expected=Cursor(1, 2))["status"] == "conflict"
    assert journal.snapshot() == before
    assert journal.observe(
        replace(poll, observed_from=106), expected=Cursor(1, 3),
    )["status"] == "stale_observation"
    assert journal.snapshot() == before
    assert journal.observe(poll, expected=Cursor(1, 3)) == {
        "status": "unchanged", "cursor": {"epoch": 1, "sequence": 3},
    }
    record = journal.snapshot()["records"][0]
    assert record["events"] == before["records"][0]["events"][-1:]
    page = journal.events(initial.key, after=Cursor(1, 1), limit=16, now=115)
    assert page["status"] == "resync_required" and page["low_water"] == 2
    page = journal.events(initial.key, after=Cursor(1, 2), limit=16, now=115)
    assert page["events"] == record["events"]
    assert page["next"] == {"epoch": 1, "sequence": 3}

    later = replace(poll, observed_from=116, observed_to=120)
    journal.observe(later, expected=Cursor(1, 3))
    restored = ConversationJournal(journal.snapshot())
    assert restored.snapshot()["records"][0]["events"] == record["events"]
    page = restored.events(initial.key, after=Cursor(1, 2), limit=16, now=120)
    assert page["status"] == "resync_required" and page["low_water"] == 3
    assert page["events"] == []
    assert restored.observe(later, expected=None)["status"] == "unchanged"
    assert restored.snapshot() == journal.snapshot()


@pytest.mark.parametrize("state", ["merged", "closed-unmerged"])
def test_quiet_terminal_poll_preserves_tombstone_until_explicit_reopen(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    import agent_pull_requests.conversation_journal as module

    monkeypatch.setattr(module, "RETENTION_SECONDS", 10)
    journal = ConversationJournal()
    terminal = observation(state=state, verdict="approved", review_rounds=2)
    journal.observe(terminal, expected=None)
    before = journal.snapshot()["records"][0]["events"]
    poll = replace(terminal, observed_from=200, observed_to=201)
    assert journal.observe(poll, expected=Cursor(1, 1))["status"] == "unchanged"
    restored = ConversationJournal(journal.snapshot())
    page = restored.events(terminal.key, after=Cursor(1, 0), limit=16, now=202)
    assert page["events"] == before and page["current"]["sections"] == {}
    assert page["current"]["review_rounds"] == 2
    restored.observe(observation(observed_from=202, observed_to=203), expected=Cursor(1, 1), reopen=True)
    page = restored.events(terminal.key, after=Cursor(1, 1), limit=16, now=204)
    assert page["status"] == "resync_required"
    assert page["previous_terminal"] == journal.snapshot()["records"][0]["current"]


@pytest.mark.parametrize(
    "changes",
    [
        {"author": "other-author"},
        {"reviewers": ()},
        {"our_roles": ()},
    ],
)
def test_confirmed_relationships_do_not_silently_disappear(changes):
    journal = ConversationJournal()
    journal.observe(observation(), expected=None)
    with pytest.raises(ValueError, match="author|relationships"):
        journal.observe(
            observation(observed_from=102, observed_to=103, **changes), expected=Cursor(1, 1)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"counts": (("patch", 1),)},
        {"sections": (("comments", "message body"),)},
        {"sections": (("comments", "1" * 64), ("comments", "2" * 64))},
        {"complete": 1},
        {"review_rounds": True},
        {"reviewers": ("x" * 2048, "y" * 2048)},
    ],
)
def test_nonmetadata_or_unbounded_inputs_are_refused(changes):
    with pytest.raises(ValueError):
        observation(**changes)


@pytest.fixture
def owner(tmp_path):
    store = ReplayStore(tmp_path.resolve(), "owner-a", clock=lambda: 120)
    store.provision()
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=200)
    return store


@pytest.mark.parametrize("rounds", [None, 1])
def test_terminal_statistics_do_not_erase_a_previously_known_review_round_count(rounds):
    journal = ConversationJournal()
    initial = observation(review_rounds=2)
    journal.observe(initial, expected=None)
    before = journal.snapshot()
    with pytest.raises(ValueError, match="cumulative review rounds"):
        journal.observe(
            replace(
                initial, observed_from=102, observed_to=103, state="merged", review_rounds=rounds
            ),
            expected=Cursor(1, 1),
        )
    assert journal.snapshot() == before


def test_explicit_layout_adoption_preserves_replay_and_refuses_unsafe_capacity(owner):
    scope = ConsumerScope("consumer-a", "account-a", "example/project")
    owner.execute(
        scope,
        "register",
        {
            "request_id": "original",
            "issued_at": 100,
            "number": 7,
            "until": ("merged",),
            "deadline_at": 110,
        },
        expires_at=200,
    )
    before = json.loads(owner.path.read_bytes())
    journal = ConversationStore(owner)
    with pytest.raises(ReplayStoreInvalid, match="explicit owner adoption"):
        journal.capabilities(expires_at=200)
    with pytest.raises(ReplayStoreInvalid, match="capacity"):
        journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200, conversation_bytes=2**40)
    assert json.loads(owner.path.read_bytes()) == before
    assert journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)["version"] == 3
    assert (
        journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)["status"]
        == "already_adopted"
    )
    assert json.loads(owner.path.read_bytes())["replay"] == before["replay"]
    assert owner.execute(scope, "pending", {}, expires_at=200)["events"][0]["timed_out"]
    with pytest.raises(ValueError, match="namespace"):
        journal.observe(
            observation(key=replace(observation().key, namespace="foreign")),
            expected=None,
            expires_at=200,
        )


@pytest.mark.parametrize("fail_at", [1, 2])
def test_adoption_lifecycle_withdrawal_preserves_the_original_envelope(owner, fail_at):
    before = owner.path.read_bytes()
    calls = []

    def lifecycle():
        calls.append(True)
        if len(calls) == fail_at:
            raise PermissionError("synthetic custody withdrawal")

    with pytest.raises(PermissionError):
        ConversationStore(owner).adopt(lifecycle_checkpoint=lifecycle, expires_at=200)
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_restart_reconciles_pre_and_post_replace_failures_without_duplicate_event(
    owner, monkeypatch, uncertain
):
    import agent_pull_requests.watch_replay_store as module

    journal = ConversationStore(owner)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    write = module._atomic_write_json

    def fail(path, value):
        if uncertain:
            write(path, value)
            raise StateCommitUncertain("synthetic")
        raise OSError("synthetic")

    monkeypatch.setattr(module, "_atomic_write_json", fail)
    with pytest.raises(StateCommitUncertain if uncertain else OSError):
        journal.observe(observation(), expected=None, expires_at=200)
    monkeypatch.setattr(module, "_atomic_write_json", write)
    restarted = ConversationStore(ReplayStore(owner.root, owner.namespace, clock=owner.clock))
    result = restarted.observe(observation(), expected=None, expires_at=200)
    assert result["status"] == ("unchanged" if uncertain else "observed")
    before = owner.path.read_bytes()
    page = restarted.events(observation().key, after=Cursor(1, 0), limit=16, expires_at=200)
    assert len(page["events"]) == 1
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_quiet_poll_retention_reconciles_restart_and_preserves_other_owner_state(
    owner: ReplayStore, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    import agent_pull_requests.conversation_journal as journal_module
    import agent_pull_requests.watch_replay_store as store_module

    monkeypatch.setattr(journal_module, "RETENTION_SECONDS", 10)
    scope = ConsumerScope("consumer-a", "account-a", "example/project")
    owner.execute(
        scope, "register",
        {"request_id": "original", "issued_at": 100, "number": 7,
         "until": ("merged",), "deadline_at": 110},
        expires_at=200,
    )
    journal = ConversationStore(owner)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    journal.observe(observation(), expected=None, expires_at=200)
    changed = observation(observed_from=104, observed_to=105, sections=(("comments", "2" * 64),))
    journal.observe(changed, expected=Cursor(1, 1), expires_at=200)
    before = json.loads(owner.path.read_bytes())
    write = store_module._atomic_write_json

    def fail(path: Path, value: dict) -> None:
        if uncertain:
            write(path, value)
            raise StateCommitUncertain("synthetic")
        raise OSError("synthetic")

    poll = replace(changed, observed_from=110, observed_to=111)
    monkeypatch.setattr(store_module, "_atomic_write_json", fail)
    with pytest.raises(StateCommitUncertain if uncertain else OSError):
        journal.observe(poll, expected=Cursor(1, 2), expires_at=200)
    monkeypatch.setattr(store_module, "_atomic_write_json", write)
    restarted = ConversationStore(ReplayStore(owner.root, owner.namespace, clock=owner.clock))
    assert restarted.observe(poll, expected=Cursor(1, 2), expires_at=200) == {
        "status": "unchanged", "cursor": {"epoch": 1, "sequence": 2},
    }
    after = json.loads(owner.path.read_bytes())
    assert after["conversations"]["records"][0]["events"] == (
        before["conversations"]["records"][0]["events"][-1:]
    )
    assert {key: value for key, value in after.items() if key != "conversations"} == {
        key: value for key, value in before.items() if key != "conversations"
    }
    page = restarted.events(observation().key, after=Cursor(1, 0), limit=16, expires_at=200)
    assert page["status"] == "resync_required"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row["current"].update(body="not metadata"),
        lambda row: row["events"][0].update(sequence=2),
        lambda row: row["current"]["key"].update(namespace="foreign"),
    ],
)
def test_corrupt_state_is_not_reinitialized(owner, mutation):
    journal = ConversationStore(owner)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    journal.observe(observation(), expected=None, expires_at=200)
    document = json.loads(owner.path.read_bytes())
    mutation(document["conversations"]["records"][0])
    raw = json.dumps(document).encode()
    owner.path.write_bytes(raw)
    with pytest.raises((ValueError, ReplayStoreInvalid)):
        journal.events(observation().key, after=None, limit=16, expires_at=200)
    assert owner.path.read_bytes() == raw


@pytest.mark.parametrize("invalid", [None, [], False, 0, ""])
def test_missing_journal_object_is_never_first_provisioning_for_any_owner_operation(
    owner, invalid
):
    journal = ConversationStore(owner)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    journal.observe(observation(), expected=None, expires_at=200)
    document = json.loads(owner.path.read_bytes())
    document["conversations"] = invalid
    raw = json.dumps(document).encode()
    owner.path.write_bytes(raw)
    for operation in (
        lambda: journal.capabilities(expires_at=200),
        lambda: journal.observe(observation(), expected=None, expires_at=200),
        lambda: journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=200),
        lambda: owner.execute(
            ConsumerScope("consumer-a", "account-a", "example/project"),
            "pending",
            {},
            expires_at=200,
        ),
    ):
        with pytest.raises(ReplayStoreInvalid):
            operation()
        assert owner.path.read_bytes() == raw
