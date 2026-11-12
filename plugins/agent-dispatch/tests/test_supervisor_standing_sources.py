"""Isolated supervisor-to-store reconciliation; no agent or live coordinator."""

from __future__ import annotations

import time
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_dispatch import supervisor_standing_sources as reconciliation
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_observations import RegistrarSourceObservation
from agent_dispatch.registrar_standing import standing_source_entries
from tests import test_pool_publication_custody as custody
from tests.test_pool_source_collection import _declaration, _registration
from tests.test_registrar_standing import _document

owner = custody.owner
REVISION = "a" * 40
NEXT = "b" * 40
NAMESPACE = "repo:example"


def _standing(**fields):
    return replace(load_declaration(_document(**fields)), owner=NAMESPACE)


def _source(root, *declarations, revision=REVISION):
    observation = RegistrarSourceObservation(
        "example", NAMESPACE, "repo", (), root, "complete",
        declaration_names=tuple(item.name for item in declarations),
        revision_state="ordered" if revision else "unordered", revision=revision,
    )
    return DeclarationRead(tuple(declarations), True, source_observations=(observation,))


def _entry(declaration):
    entry, = standing_source_entries([declaration])
    return {"title": entry.title, "declaration": dict(entry.declaration), "enabled": entry.enabled}


@pytest.fixture
def standing_owner(owner):
    daemon, queue, _ = owner
    calls, writes = [], []

    def repositories(namespace, *, deadline):
        calls.append(("inventory", namespace, deadline))
        return queue.standing_source_repositories(namespace)

    def observation(repo, namespace, *, deadline):
        calls.append(("observation", repo, namespace, deadline))
        return queue.standing_source_observation(repo, namespace)

    def reconcile(repo, namespace, entries, *, revision, expected_observation, deadline):
        calls.append(("reconcile", repo, namespace, deadline))
        result = queue.reconcile_standing_source_set(
            repo, namespace, entries, revision=revision, expected_observation=expected_observation,
        )
        writes.append(result)
        return result

    daemon.client.standing_source_repositories = repositories
    daemon.client.standing_source_observation = observation
    daemon.client.reconcile_standing_source_set = reconcile
    return daemon, queue, calls, writes


def test_fresh_mixed_source_enrolls_idempotently_without_a_standing_process(standing_owner, tmp_path):
    daemon, queue, calls, writes = standing_owner
    source = _source(tmp_path, _declaration(), _standing())
    daemon.declared_source = lambda: source
    assert set(daemon._desired()) == {"declared:repo:example:worker"}
    task_id, = writes[0]["enrolled"]
    before = queue.get(task_id)
    assert before.standing_charter["lifecycle"] == "active"
    assert queue.standing_source_observation("example/harness", NAMESPACE) == REVISION
    assert len({call[-1] for call in calls}) == 1
    daemon._desired()
    assert writes[-1]["unchanged"] == [task_id]
    assert queue.get(task_id) == before


def test_empty_source_retires_retained_lane_but_never_a_foreign_namespace(
    standing_owner, tmp_path, monkeypatch,
):
    daemon, queue, _, writes = standing_owner
    declaration = _standing(repos="https://example.test/owner/repo.git")
    source = _source(tmp_path, declaration)
    daemon.declared_source = lambda: source
    daemon._desired()
    task_id, = writes[-1]["enrolled"]
    foreign = replace(declaration, owner="repo:foreign")
    foreign_id, = queue.reconcile_standing_source_set(
        declaration.repos, foreign.owner, [_entry(foreign)], revision=REVISION,
        expected_observation=None,
    )["enrolled"]
    ancestry = []

    def follows(root, previous, current, *, deadline):
        ancestry.append((root, previous, current))
        return True

    monkeypatch.setattr(reconciliation, "source_revision_follows", follows)
    source = _source(tmp_path, revision=NEXT)
    daemon._desired()
    assert ancestry == [(tmp_path, REVISION, NEXT)]
    assert writes[-1]["retired"] == [{"id": task_id, "reason": "removed"}]
    assert queue.get(foreign_id).standing_charter["lifecycle"] == "active"
    assert queue.standing_source_repositories(NAMESPACE) == ["example.test/owner/repo"]
    source = _source(tmp_path, declaration, revision=NEXT)
    daemon._desired()
    assert writes[-1]["deferred"] == [{"id": task_id, "reason": "reenrollment-required"}]


def test_aliases_share_one_complete_repository_set(standing_owner, tmp_path):
    daemon, queue, calls, writes = standing_owner
    first = _standing(repos="https://example.test/owner/repo.git")
    second = _standing(
        name="secondary", repos="git@example.test:owner/repo.git", spec={"lane_key": "secondary"},
    )
    daemon.declared_source = lambda: _source(tmp_path, first, second)
    daemon._desired()
    assert len(writes) == 1 and len(writes[0]["enrolled"]) == 2
    assert [call[1] for call in calls if call[0] == "reconcile"] == ["example.test/owner/repo"]
    assert queue.standing_source_repositories(NAMESPACE) == ["example.test/owner/repo"]


@pytest.mark.parametrize("failure", [
    "incomplete", "plain", "no-observation", "missing", "error", "no-root", "unavailable",
    "missing-name", "extra-name", "foreign-owner", "conflicting-revision", "conflicting-root",
])
def test_unqualified_sources_preserve_finite_workers_without_standing_io(
    standing_owner, tmp_path, failure,
):
    daemon, _, calls, writes = standing_owner
    source = _source(tmp_path, _declaration(), _standing())
    observation, = source.source_observations
    if failure == "incomplete":
        source = replace(source, complete=False)
    elif failure == "plain":
        source = list(source)
    elif failure == "no-observation":
        source = replace(source, source_observations=())
    elif failure == "foreign-owner":
        source = replace(source, declarations=(_declaration(), replace(_standing(), owner="repo:other")))
    else:
        overrides = {
            "missing": {"status": "missing"}, "error": {"status": "error"},
            "no-root": {"root": None}, "unavailable": {"revision_state": "unavailable"},
            "missing-name": {"declaration_names": ("worker",)},
            "extra-name": {"declaration_names": (*observation.declaration_names, "absent")},
            "conflicting-revision": {"revision": NEXT},
            "conflicting-root": {"root": tmp_path / "other"},
        }
        changed = replace(observation, **overrides[failure])
        source = replace(source, source_observations=(
            (observation, changed) if failure.startswith("conflicting-") else (changed,)
        ))
    daemon.declared_source = lambda: source
    assert set(daemon._desired()) == {"declared:repo:example:worker"}
    assert calls == writes == []


def test_metadata_capture_never_reconciles_standing_sources(standing_owner, tmp_path):
    daemon, queue, calls, writes = standing_owner
    daemon.declared_source = lambda: _source(tmp_path, _declaration(), _standing())
    assert daemon.capture_pool_publication_inputs().coherent
    assert calls == writes == []
    assert queue.standing_source_repositories(NAMESPACE) == []


def test_cached_and_delayed_discovery_cannot_reconcile(standing_owner, tmp_path):
    daemon, _, calls, writes = standing_owner
    source = _source(tmp_path, _declaration(), _standing())
    daemon.declared_source = lambda: source
    daemon._last_declared = [_registration()]
    future = Future()
    daemon._discovery_futures["declarations"] = future
    daemon._probe_inflight.add(future)
    daemon._reconcile_deadline = time.monotonic()
    assert daemon._declared()
    future.set_result(([_registration()], True, "", source))
    daemon._reconcile_deadline = time.monotonic() + 1
    assert daemon._declared()
    assert calls == writes == []
    daemon._reconcile_deadline = None
    assert daemon._declared()
    assert writes[0]["enrolled"]


@pytest.mark.parametrize("fault", ["no-lock", "lost-lock", "new-client", "expired"])
def test_custody_and_deadline_are_rechecked_before_writes(standing_owner, tmp_path, fault):
    daemon, queue, calls, writes = standing_owner
    daemon.declared_source = lambda: _source(tmp_path, _declaration(), _standing())
    original = daemon.client
    if fault == "no-lock":
        daemon.release_singleton()
    elif fault == "expired":
        daemon._reconcile_deadline = time.monotonic()
    else:
        read = original.standing_source_observation

        def interrupted(*args, **kwargs):
            result = read(*args, **kwargs)
            if fault == "lost-lock":
                daemon.release_singleton()
            else:
                daemon.client = SimpleNamespace()
            return result

        original.standing_source_observation = interrupted
    try:
        if fault == "expired":
            daemon._reconcile_standing_sources(daemon.declared_source())
        else:
            assert daemon._declared()
        assert writes == []
        assert not any(call[0] == "reconcile" for call in calls)
        assert queue.standing_source_repositories(NAMESPACE) == []
    finally:
        daemon.client = original
        daemon._reconcile_deadline = None


def test_unordered_source_can_enroll_but_cannot_update_or_retire(standing_owner, tmp_path):
    daemon, queue, _, writes = standing_owner
    source = _source(tmp_path, _standing(), revision=None)
    daemon.declared_source = lambda: source
    daemon._desired()
    task_id, = writes[-1]["enrolled"]
    before = queue.get(task_id)
    source = _source(tmp_path, _standing(title="Changed"), revision=None)
    daemon._desired()
    assert writes[-1]["deferred"] == [{"id": task_id, "reason": "unordered-source-change"}]
    source = _source(tmp_path, revision=None)
    daemon._desired()
    assert not writes[-1]["retired"]
    assert queue.get(task_id) == before


def test_reverse_revision_and_observation_race_leave_existing_state(
    standing_owner, tmp_path, monkeypatch,
):
    daemon, queue, _, writes = standing_owner
    source = _source(tmp_path, _standing())
    daemon.declared_source = lambda: source
    daemon._desired()
    task_id, = writes[-1]["enrolled"]
    before = queue.get(task_id)
    source = _source(tmp_path, revision=NEXT)
    monkeypatch.setattr(reconciliation, "source_revision_follows", lambda *args, **kwargs: False)
    daemon._desired()
    assert len(writes) == 1
    monkeypatch.setattr(reconciliation, "source_revision_follows", lambda *args, **kwargs: True)
    read = daemon.client.standing_source_observation

    def race(repo, namespace, **kwargs):
        previous = read(repo, namespace, **kwargs)
        queue.reconcile_standing_source_set(
            repo, namespace, [_entry(_standing())],
            revision="c" * 40, expected_observation=previous,
        )
        return previous

    daemon.client.standing_source_observation = race
    daemon._desired()
    assert len(writes) == 1
    assert queue.get(task_id) == before
    assert queue.standing_source_observation("example/harness", NAMESPACE) == "c" * 40


def test_source_failure_is_sanitized_and_does_not_block_finite_workers(
    standing_owner, tmp_path, caplog,
):
    daemon, _, _, writes = standing_owner
    daemon.declared_source = lambda: _source(tmp_path, _declaration(), _standing())

    def unavailable(*args, **kwargs):
        raise RuntimeError("private-source-details")

    daemon.client.standing_source_repositories = unavailable
    assert set(daemon._desired()) == {"declared:repo:example:worker"}
    assert writes == []
    assert "error_type=RuntimeError" in caplog.text
    assert "private-source-details" not in caplog.text
