"""Pure refusal must not enter mutation preflight or synthesize owner absence."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import config as cfg


@pytest.fixture
def planning_record(tmp_path, monkeypatch):
    monkeypatch.setattr(relocation, "_native_supported", lambda: True)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    records = tmp_path / "worktrees"
    records.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda _repo=None: records)
    identity = "fixture-win-20261105-100000-abcd"
    data = {
        "worktree_id": identity, "branch": f"worktree/{identity}",
        "worktree_path": str(tmp_path / identity), "repo": "sample",
        "machine": "fixture", "platform": "windows", "status": "completed",
        "creation_nonce": "original-nonce", "sessions": [],
    }
    ledger = records / f"{identity}.yaml"
    ledger.write_text(json.dumps(data), encoding="utf-8")
    return SimpleNamespace(
        data=data, ledger=ledger, identity=identity,
        config=SimpleNamespace(repo_name="sample"), destination=tmp_path / "new" / identity,
    )


def test_unknown_plan_never_enters_action_or_mutation_owners(planning_record, monkeypatch):
    fixture = planning_record

    def forbidden(*_args, **_kwargs):
        pytest.fail("pure planning entered an action/mutation owner")

    for owner, name in (
        (relocation, "_action_plan"), (relocation, "_stopped"),
        (relocation.execution_spaces, "require_record_mutation"),
        (relocation.worktree_lifecycle, "guard"), (relocation.launch_admission, "guard"),
        (relocation.state, "owner"), (relocation.state, "write"),
        (relocation.procs, "checkout_retirement_blocker"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    before = fixture.ledger.read_bytes()
    with pytest.raises(ValueError, match="qualified passive native owner evidence"):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    assert fixture.ledger.read_bytes() == before
    assert list(fixture.ledger.parent.iterdir()) == [fixture.ledger]
    assert not fixture.destination.exists()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"repo": "foreign"}, "identity/project"),
        ({"owner_ref": "original/project/creator"}, "cannot recover missing"),
        ({"receiving_allocation": {"schema": "ambiguous"}}, "receiving allocation receipt"),
        ({"status": "active"}, "ordinary sessionless"),
        ({"sessions": [{"session_id": "registered", "state": "active"}]}, "ordinary sessionless"),
    ],
)
def test_known_identity_and_owner_refusals_preserve_original_bytes(planning_record, changes, reason):
    fixture = planning_record
    fixture.ledger.write_text(json.dumps({**fixture.data, **changes}), encoding="utf-8")
    before = fixture.ledger.read_bytes()
    with pytest.raises(ValueError, match=reason):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    assert fixture.ledger.read_bytes() == before
    assert not fixture.destination.exists()


@pytest.mark.parametrize("name", ["lifecycle.lock", "launch-admission.lock"])
def test_existing_lifecycle_marker_is_not_acquired_reaped_or_cleared(planning_record, name):
    fixture = planning_record
    marker = relocation.launch_seed_state.state_path(fixture.ledger).parent / name
    marker.parent.mkdir()
    marker.write_bytes(b"original owner evidence")
    with pytest.raises(ValueError, match="idle lifecycle/launch ownership"):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    assert marker.read_bytes() == b"original owner evidence"


@pytest.mark.parametrize("name", ["lifecycle.lock", "launch-admission.lock"])
def test_unrelated_tracking_root_marker_is_not_a_worktree_owner(planning_record, name):
    fixture = planning_record
    marker = fixture.ledger.parent / name
    marker.write_bytes(b"unrelated owner evidence")
    with pytest.raises(ValueError, match="qualified passive native owner evidence"):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    assert marker.read_bytes() == b"unrelated owner evidence"


def test_move_keeps_strict_action_preflight(planning_record, monkeypatch):
    from contextlib import nullcontext

    fixture = planning_record
    fixture.config.default_repo = SimpleNamespace(worktree_root=str(fixture.ledger.parent))
    for owner, name in (
        (relocation, "FinalizeLock"), (relocation.worktree_lifecycle, "guard"),
        (relocation.launch_admission, "guard"), (relocation.state, "owner"),
    ):
        monkeypatch.setattr(owner, name, lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(relocation.state, "require_writer_generation", lambda: None)
    seen = []

    def preflight(*args):
        seen.append(args)
        raise ValueError("strict preflight sentinel")

    monkeypatch.setattr(relocation, "_action_plan", preflight)
    monkeypatch.setattr(
        relocation, "plan", lambda *_args: pytest.fail("apply called passive planning"),
    )
    with pytest.raises(ValueError, match="strict preflight sentinel"):
        relocation.move(fixture.identity, str(fixture.destination), fixture.config)
    assert seen == [(fixture.identity, str(fixture.destination), fixture.config)]


@pytest.mark.parametrize("phase", ["planned", "copying"])
def test_explicit_live_check_owns_guards_without_writing_a_move(planning_record, monkeypatch, phase, capsys):
    from contextlib import contextmanager, nullcontext
    from agent_worktrees.checkout_relocation_plan import check_live

    fixture = planning_record
    fixture.config.default_repo = SimpleNamespace(worktree_root=str(fixture.ledger.parent))
    entered = []

    def guard(name):
        @contextmanager
        def held(*_args, **_kwargs):
            if "on_stale" in _kwargs:
                _kwargs["on_stale"]("retained stale marker")
            entered.append(name)
            try:
                yield
            finally:
                entered.remove(name)
        return held

    for owner, name in (
        (relocation, "FinalizeLock"), (relocation.worktree_lifecycle, "guard"),
        (relocation.launch_admission, "guard"), (relocation.state, "owner"),
    ):
        monkeypatch.setattr(owner, name, guard(str(id(owner))))
    monkeypatch.setattr(relocation, "_receiving_admission", lambda *_: (None, None, nullcontext()))

    def preflight(*_args):
        assert len(entered) == 4
        return {"phase": phase, "apply": False}

    monkeypatch.setattr(relocation, "_action_plan", preflight)
    monkeypatch.setattr(relocation.state, "write", lambda *_: pytest.fail("live check wrote a move"))
    if phase == "planned":
        report = check_live(fixture.identity, str(fixture.destination), fixture.config)
        assert report["live_admission"]["apply_rechecks"] and not report["apply"]
    else:
        with pytest.raises(ValueError, match="unstarted move"):
            check_live(fixture.identity, str(fixture.destination), fixture.config)
    assert not entered
    captured = capsys.readouterr()
    assert not captured.out and "retained stale marker" in captured.err


@pytest.mark.parametrize("active,confirmed", [(True, True), (False, False), (False, True)])
def test_live_owner_check_preserves_strict_bound_process_and_unknown_fences(
    planning_record, monkeypatch, active, confirmed,
):
    fixture = planning_record
    seen = []

    def verify(record, *, strict):
        assert record is fixture
        seen.append(strict)
        return SimpleNamespace(active=active, probes_ok=confirmed)

    monkeypatch.setattr(relocation.sessions, "verify_worktree_active", verify)
    monkeypatch.setattr(relocation.procs, "checkout_retirement_blocker", lambda *_: None)
    if active or not confirmed:
        with pytest.raises(ValueError, match="confirmed inactive"):
            relocation._stopped(fixture, fixture.destination)
    else:
        relocation._stopped(fixture, fixture.destination)
    assert seen == [True]
