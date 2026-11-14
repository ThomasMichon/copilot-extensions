"""Pure refusal must not enter mutation preflight or synthesize owner absence."""

from __future__ import annotations

import json
import argparse
import sqlite3
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import config as cfg


@pytest.fixture
def planning_record(tmp_path, monkeypatch):
    monkeypatch.setattr(relocation, "_native_supported", lambda: True)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "fixture")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
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
        ({"owner_ref": "original/project/creator"}, "proven local creator"),
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


def _move_arguments(*arguments):
    from agent_worktrees import worktree_ops_cli

    parser = argparse.ArgumentParser()
    worktree_ops_cli.add_parsers(parser.add_subparsers())
    return parser.parse_args(["move", *arguments])


def test_live_cli_discovery_does_not_load_target_state(monkeypatch, capsys):
    from agent_worktrees import worktree_ops_cli

    monkeypatch.setattr(relocation, "_native_supported", lambda: True)
    monkeypatch.setattr(cfg, "load_config", lambda: pytest.fail("discovery loaded target state"))
    assert worktree_ops_cli.cmd_move(_move_arguments("--capabilities")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["live_admission"] == {
        "schema": "agent-worktrees.checkout-live-admission", "version": 1,
        "check_live": True, "activity_refresh": True, "apply_rechecks": True,
    }


def test_live_cli_dispatch_never_selects_copy_or_pure_planning(planning_record, monkeypatch, capsys):
    from agent_worktrees import checkout_relocation_plan, worktree_ops_cli

    fixture = planning_record
    seen = []
    monkeypatch.setattr(cfg, "load_config", lambda: fixture.config)

    def admitted(*arguments):
        seen.append(arguments)
        return {"apply": False, "phase": "planned", "live_admission": {"apply_rechecks": True}}

    monkeypatch.setattr(checkout_relocation_plan, "check_live", admitted)
    for owner, name in ((relocation, "move"), (relocation, "plan"), (relocation.state, "write")):
        monkeypatch.setattr(owner, name, lambda *_: pytest.fail("live CLI selected copying or journal writes"))
    assert worktree_ops_cli.cmd_move(_move_arguments(
        fixture.identity, "--to", str(fixture.destination), "--check-live", "--json",
    )) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["live_admission"]["apply_rechecks"] and not result["apply"]
    assert seen == [(fixture.identity, str(fixture.destination), fixture.config)]


@pytest.mark.parametrize("conflict", ["--apply", "--abort", "--capabilities"])
def test_live_cli_rejects_conflicting_effect_or_discovery_options(conflict, monkeypatch, capsys):
    from agent_worktrees import worktree_ops_cli

    monkeypatch.setattr(cfg, "load_config", lambda: pytest.fail("conflicting command read target state"))
    if conflict == "--capabilities":
        assert worktree_ops_cli.cmd_move(_move_arguments("--check-live", conflict)) == 1
        assert "accepts no target or effects" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as refusal:
            _move_arguments("--check-live", conflict)
        assert refusal.value.code == 2


def _ordinary_owned(fixture):
    fixture.data["owner_ref"] = "fixture/sample/creator"
    fixture.ledger.write_text(json.dumps(fixture.data), encoding="utf-8")
    parent = fixture.ledger.with_name("creator.yaml")
    parent.write_text(json.dumps({
        "worktree_id": "creator", "repo": "sample", "machine": "fixture",
        "platform": "windows", "worktree_path": str(parent.parent / "creator"),
        "branch": "worktree/creator", "status": "active", "sessions": [],
        "resources": [{
            "kind": "worktree", "ref": "fixture/sample/" + fixture.identity,
            "state": "at-rest", "created_at": "2020-01-01T00:00:00",
        }],
    }), encoding="utf-8")
    return parent


def test_generic_owned_ordinary_record_is_not_a_missing_receiving_receipt(planning_record):
    fixture = planning_record
    parent = _ordinary_owned(fixture)
    before = (fixture.ledger.read_bytes(), parent.read_bytes())
    with pytest.raises(ValueError, match="qualified passive native owner evidence"):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    record, proof, _guard = relocation._receiving_admission(
        fixture.ledger, fixture.config,
    )
    assert proof is None and record.owner_ref == "fixture/sample/creator"
    assert before == (fixture.ledger.read_bytes(), parent.read_bytes())


def test_ordinary_owner_still_requires_the_actual_receiving_ledger(planning_record, monkeypatch):
    fixture = planning_record
    _ordinary_owned(fixture)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    record, _, _ = relocation._receiving_admission(fixture.ledger, fixture.config)
    monkeypatch.setattr(relocation.execution_spaces, "require_current_execution_space", lambda *_: None)

    with pytest.raises(relocation.execution_spaces.ExecutionSpaceError, match="receiving ledger"):
        relocation.execution_spaces.require_record_mutation(record, fixture.config, recover_receiving=False)


def test_generic_creator_reference_is_exact_before_ledger_lookup(planning_record):
    fixture = planning_record
    fixture.data["owner_ref"] = "fixture/sample/../unrelated"
    fixture.ledger.write_text(json.dumps(fixture.data), encoding="utf-8")
    with pytest.raises(ValueError, match="exact qualified worktree reference"):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)


def test_native_owner_recheck_also_revalidates_generic_creator_responsibility(planning_record, monkeypatch):
    fixture = planning_record
    parent = _ordinary_owned(fixture)
    record, _, _ = relocation._receiving_admission(fixture.ledger, fixture.config)

    def native_owner(candidate, *, strict):
        assert candidate.yaml_path == fixture.ledger and strict
        return SimpleNamespace(active=False, probes_ok=True)

    def cwd_owner(path):
        assert path == record.worktree_path
        return None

    monkeypatch.setattr(relocation.sessions, "verify_worktree_active", native_owner)
    monkeypatch.setattr(relocation.procs, "checkout_retirement_blocker", cwd_owner)
    relocation._stopped(record, Path(record.worktree_path))
    data = json.loads(parent.read_text())
    data["resources"][0]["state"] = "active"
    parent.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="responsibility is missing, unsettled"):
        relocation._stopped(record, Path(record.worktree_path))


@pytest.mark.parametrize("journal", ["receiving", "busy", "malformed", "unsupported"])
def test_known_or_unreadable_original_receiving_evidence_still_refuses_purely(planning_record, journal):
    fixture = planning_record
    _ordinary_owned(fixture)
    path = fixture.ledger.parent.parent / "workspace-creation.sqlite3"
    if journal == "malformed":
        path.write_bytes(b"not an original SQLite journal")
        reason = "quiescent rollback journal"
    else:
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE creations(key_digest TEXT,version INTEGER,facts TEXT,status TEXT,worktree TEXT)")
        connection.execute("INSERT INTO creations VALUES(?,?,?,?,?)", (
            "original-key", 999 if journal == "unsupported" else 1,
            json.dumps({} if journal == "unsupported" else {"receiving_allocation": {"origin": fixture.data["owner_ref"]}}),
            "terminal", json.dumps({"worktree": {"id": fixture.identity, "creation_nonce": fixture.data["creation_nonce"]}}),
        ))
        connection.commit()
        connection.close()
        reason = "cannot recover missing"
        if journal == "unsupported":
            reason = "unsupported version"
        if journal == "busy":
            Path(str(path) + "-wal").write_bytes(b"original owner evidence")
            reason = "busy, redirected or unsupported"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=reason):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    if journal == "receiving":
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        assert connection.execute("PRAGMA index_list(creations)").fetchall() == []
        connection.close()


@pytest.mark.parametrize("condition", ["unsettled", "opaque-producer", "oversized", "malformed", "missing-field", "opaque-peer"])
def test_unsettled_or_native_producer_is_not_reclassified_as_generic_ordinary(planning_record, condition):
    fixture = planning_record
    parent = _ordinary_owned(fixture)
    if condition == "malformed":
        parent.write_text("resources: [")
        with pytest.raises(ValueError, match="creator ledger is malformed"):
            relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
        return
    if condition == "oversized":
        parent.write_bytes(b" " * (64 * 1024 + 1))
        with pytest.raises(ValueError, match="oversized"):
            relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
        return
    data = json.loads(parent.read_text())
    if condition == "missing-field":
        del data["branch"]
        parent.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="creator ledger is malformed"):
            relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
        return
    if condition == "unsettled":
        data["resources"][0]["state"] = "active"
    elif condition == "opaque-peer":
        data["resources"].append({"kind": None, "ref": data["resources"][0]["ref"], "state": "active"})
    else:
        data["resources"][0]["producer"] = {"namespace": "unknown", "version": 99}
    parent.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="responsibility is missing, unsettled"):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)


@pytest.mark.parametrize("identity,session", [("creator", "original-session"), ("@anchor", None), ("@anchor", "original-session")])
def test_generic_creator_forms_preserve_original_reference(planning_record, identity, session):
    fixture = planning_record
    parent = _ordinary_owned(fixture)
    data = json.loads(parent.read_text())
    data["worktree_id"] = identity
    parent.unlink()
    parent.with_name(identity + ".yaml").write_text(json.dumps(data))
    fixture.data["owner_ref"] = f"fixture/sample/{identity}" + (f"#{session}" if session else "")
    fixture.ledger.write_text(json.dumps(fixture.data), encoding="utf-8")
    record, proof, _ = relocation._receiving_admission(fixture.ledger, fixture.config)
    assert proof is None and record.owner_ref == fixture.data["owner_ref"]


@pytest.mark.skipif(os.name != "nt", reason="qualified Windows SQLite byte-range locking")
def test_original_exclusive_transaction_without_sidecars_stays_busy(planning_record):
    fixture = planning_record
    _ordinary_owned(fixture)
    path = fixture.ledger.parent.parent / "workspace-creation.sqlite3"
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE creations(version INTEGER,facts TEXT,worktree TEXT)")
    writer.commit()
    before = path.read_bytes()
    writer.execute("BEGIN EXCLUSIVE")
    try:
        assert not any(Path(str(path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm"))
        with pytest.raises(ValueError, match="classification is busy"):
            relocation._receiving_admission(fixture.ledger, fixture.config)
        with pytest.raises(ValueError, match="qualified passive native owner evidence"):
            relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
        assert path.read_bytes() == before
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.parametrize("kind", ["empty", "reserved", "oversized"])
def test_bounded_original_handoff_owner_keeps_reservations_and_unknowns(planning_record, monkeypatch, kind):
    from agent_worktrees import claim_handoffs

    fixture = planning_record
    _ordinary_owned(fixture)
    path = fixture.ledger.parent.parent / "claim-handoffs.yaml"
    monkeypatch.setattr(claim_handoffs, "registry_path", lambda: path)
    bundle = claim_handoffs.ClaimBundle(
        "original-bundle", "offered", "fixture/sample/creator", "fixture/sample/recipient",
        ({"kind": "worktree", "ref": "fixture/sample/" + fixture.identity},),
        "2020-01-01T00:00:00", "2020-01-01T00:00:00",
    )
    path.write_text(json.dumps({"version": 1, "bundles": [bundle.to_dict()] if kind == "reserved" else []}))
    reason = "handoff-reserved" if kind == "reserved" else "qualified passive native owner evidence"
    if kind == "oversized":
        path.write_bytes(b" " * (1024 * 1024 + 1))
        reason = "read budget"
    before = path.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match=reason):
        relocation.plan(fixture.identity, str(fixture.destination), fixture.config)
    assert path.read_bytes() == before


def test_literal_workspace_lookup_does_not_enumerate_unrelated_session_artifacts(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    expected = []
    for name in ("first", "second"):
        directory = root / name
        directory.mkdir()
        workspace = directory / "workspace.yaml"
        workspace.write_text("cwd: example")
        expected.append(workspace)
        for index in range(8):
            (directory / f"unrelated-{index}.json").write_text("not reference metadata")
    monkeypatch.setattr(relocation.transfer, "MAX_ENTRIES", 4)
    assert set(relocation._strict_glob(root, "*/workspace.yaml")) == set(expected)


def test_literal_lookup_keeps_root_namespace_budget(tmp_path, monkeypatch):
    for name in ("first", "second", "third"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "workspace.yaml").write_text("cwd: example")
    monkeypatch.setattr(relocation.transfer, "MAX_ENTRIES", 2)
    with pytest.raises(ValueError, match="inventory exceeds its budget"):
        list(relocation._strict_glob(tmp_path, "*/workspace.yaml"))


def test_missing_literal_leaf_does_not_hide_a_disappearing_reference_directory(tmp_path, monkeypatch):
    directory = tmp_path / "session"
    directory.mkdir()
    original = Path.lstat

    def disappear(path, *args, **kwargs):
        if path == directory / "workspace.yaml":
            directory.rename(tmp_path / "retained-session")
            raise FileNotFoundError(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappear)
    with pytest.raises(FileNotFoundError):
        list(relocation._strict_glob(tmp_path, "*/workspace.yaml"))
