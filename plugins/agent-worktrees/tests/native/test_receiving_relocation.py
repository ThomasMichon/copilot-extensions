"""Receiving-only continuity; native host inactivity remains separately unqualified."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as location
from agent_worktrees import config as cfg, creation_requests, execution_spaces, tracking
from agent_worktrees import owned_worktree as allocation, receiving_allocation as receiving
from agent_worktrees import receiving_relocation, resource_producer as producer
from agent_worktrees import tracking_allocation_write as origin_owner
from agent_worktrees.worktree_allocation_receiver import receive
from test_checkout_relocation import checkout as checkout


def _snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.stat().st_ino, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in root.rglob("*")
    }


@pytest.fixture
def received(checkout, request, monkeypatch, record_property):
    fixture = checkout
    config = fixture.config
    expected = json.loads(os.environ.get("AGENT_WORKTREES_RELOCATION_EXPECTED_CODE", "{}"))
    for owner in (relocation, location, receiving_relocation):
        digest = hashlib.sha256(Path(owner.__file__).read_bytes()).hexdigest()
        name = Path(owner.__file__).name
        record_property(name + "_sha256", digest)
        if name in expected:
            assert digest == expected[name], "receiving consumer imported different source bytes"
    previous_project = cfg.active_project()
    cfg.set_active_project(config.repo_name)
    request.addfinalizer(lambda: cfg.set_active_project(previous_project))
    monkeypatch.setattr(
        relocation.sessions, "verify_worktree_active",
        lambda *_args, **_kwargs: pytest.fail("receiving-only fixture reached a native owner probe"),
    )
    run = subprocess.run

    def no_backend(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv and Path(argv[0]).stem.casefold() in {"psmux", "tmux"}:
            pytest.fail("receiving-only fixture invoked a native mux backend")
        return run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", no_backend)
    origin = tracking.load_record(fixture.record.yaml_path)
    origin.status = "active"
    tracking.save_record(origin)
    context = execution_spaces.current_execution_context(config)
    origin_ref = tracking.format_claim_ref(origin.execution_machine, origin.repo, origin.worktree_id)
    target = {
        "machine": context.execution_machine, "project": config.repo_name,
        "options": {"no_pair": True},
    }
    with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
        claim, _ = origin_owner._reserve(
            origin, tracking.parse_claim_ref(origin_ref),
            {"request_id": "receiving-continuity-fixture", "target": target},
        )
        with producer.transaction(origin.yaml_path):
            tracking.save_record(origin)
    envelope = {
        "schema": allocation.SCHEMA, "version": allocation.VERSION, "origin": origin_ref,
        "request_id": claim.producer["request_id"], "token": claim.producer["token"],
        "target": target, "identity": None,
    }
    created = receive("create", envelope, config)
    assert created["status"] == "allocated"
    child_id = allocation.qualified_ref(created["identity"]["ref"])[2]
    child = tracking.load_record(cfg.tracking_dir(config.repo_name) / f"{child_id}.yaml")
    with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
        origin = tracking.load_record(origin.yaml_path)
        claim = origin_owner._matches(origin, {"token": envelope["token"]})
        origin_owner._transition(claim, "bind", created)
        with producer.transaction(origin.yaml_path):
            tracking.save_record(origin)
    child.status = "finalized"
    tracking.save_record(child)
    concluded = receive("inspect", {**envelope, "identity": created["identity"]}, config)
    assert concluded["status"] == "finalized"
    with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
        origin = tracking.load_record(origin.yaml_path)
        claim = origin_owner._matches(origin, {"token": envelope["token"]})
        origin_owner._transition(claim, "close", concluded)
        with producer.transaction(origin.yaml_path):
            tracking.save_record(origin)
    return SimpleNamespace(
        config=config, record=child, source=Path(child.worktree_path),
        destination=fixture.destination.parent / child.worktree_id,
        origin=origin, envelope={**envelope, "identity": created["identity"]},
        home=fixture.home, anchor=fixture.anchor,
    )


def _receiving_only_admission(fixture, monkeypatch):
    """Admit only this new fixture's component scope, never assert host absence."""
    roots = {fixture.source.parent.resolve(), fixture.destination.parent.resolve()}
    assert all(root.is_relative_to(fixture.home.resolve()) for root in roots)

    def scoped(record, path):
        assert record.worktree_id == fixture.record.worktree_id
        assert Path(path).parent.resolve() in roots
        assert not record.sessions and not record.head_session

    monkeypatch.setattr(relocation, "_stopped", scoped)


def test_native_receiving_continuity_preserves_original_results_and_current_consumers(received, monkeypatch):
    fixture = received
    journal = fixture.record.yaml_path.parent.parent / "workspace-creation.sqlite3"
    original_journal = journal.read_bytes()
    original_receipt = copy.deepcopy(fixture.record.receiving_allocation)
    original_identity = receiving.record_identity(fixture.record)
    original_origin = fixture.origin.yaml_path.read_bytes()
    before = _snapshot(fixture.home)
    with pytest.raises(ValueError, match="qualified passive native owner evidence"):
        relocation.plan(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert _snapshot(fixture.home) == before

    # Only the receiving component is admitted. No native mux/process observer
    # or positive real-host inactivity claim is supplied by this fixture.
    _receiving_only_admission(fixture, monkeypatch)
    result = relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "complete" and not fixture.source.exists()
    current = tracking.load_record(fixture.record.yaml_path)
    assert current.worktree_path == str(fixture.destination)
    assert current.receiving_allocation == original_receipt
    assert receiving.record_identity(current) == original_identity
    assert journal.read_bytes() == original_journal
    assert fixture.origin.yaml_path.read_bytes() == original_origin
    receiving.require_record(current, fixture.config)
    execution_spaces.require_record_mutation(current, fixture.config, recover_receiving=False)
    current.title = "ordinary update at the new location"
    tracking.save_record(current)
    assert journal.read_bytes() == original_journal
    assert receive("inspect", fixture.envelope, fixture.config)["identity"] == original_identity.to_dict()
    assert receive("confirm", fixture.envelope, fixture.config)["status"] == "finalized"
    assert relocation.move(current.worktree_id, str(fixture.destination), fixture.config)["phase"] == "complete"
    with pytest.raises(ValueError, match="stale checkout location"):
        tracking.save_record(fixture.record)
    # FinalizeLock removes its marker after the move's data barrier. Settle
    # that owned metadata before measuring the next pure refusal on Windows.
    relocation.durability.directory(fixture.source.parent)
    before = _snapshot(fixture.home)
    with monkeypatch.context() as refusal:
        refusal.setattr(relocation, "FinalizeLock", lambda *_a, **_k: pytest.fail("another revision acquired leases"))
        with pytest.raises(ValueError, match="one current-location revision"):
            relocation.move(current.worktree_id, str(fixture.source), fixture.config)
    assert _snapshot(fixture.home) == before
    assert not relocation._git(fixture.destination, "status", "--porcelain")
    ledger = fixture.record.yaml_path
    journal_path = location.journal_path(ledger)
    saved_ledger, saved_revision = ledger.read_bytes(), journal_path.read_bytes()
    backup = journal.with_name("retained-original-creation.sqlite3")
    location_backup = journal_path.with_name("retained-current-location.json")
    for damage in ("missing-receipt", "unknown-proof", "missing-journal", "missing-location"):
        if damage == "missing-receipt":
            raw = yaml.safe_load(saved_ledger)
            del raw["receiving_allocation"]
            ledger.write_text(yaml.safe_dump(raw))
        elif damage == "unknown-proof":
            revision = json.loads(saved_revision)
            revision["receiving_location"]["version"] = 99
            journal_path.write_text(json.dumps(revision))
        elif damage == "missing-journal":
            journal.rename(backup)
        else:
            journal_path.rename(location_backup)
        before = _snapshot(fixture.home)
        with pytest.raises((ValueError, OSError)):
            location.require_idle(ledger)
        with pytest.raises(execution_spaces.ExecutionSpaceError):
            execution_spaces.require_record_mutation(tracking.load_record(ledger), fixture.config)
        assert _snapshot(fixture.home) == before
        if damage == "missing-location":
            with pytest.raises(TimeoutError, match="relocation blocks lifecycle"):
                with relocation.worktree_lifecycle.guard(ledger):
                    pytest.fail("unproven receiving location admitted lifecycle mutation")
            assert ledger.read_bytes() == saved_ledger
            fresh = tracking.load_record(ledger)
            fresh.title = "must not persist without location authority"
            with pytest.raises(ValueError):
                tracking.save_record(fresh)
            assert ledger.read_bytes() == saved_ledger
            with pytest.raises(ValueError):
                tracking.save_record(fixture.record)
            assert ledger.read_bytes() == saved_ledger
        ledger.write_bytes(saved_ledger)
        if damage == "missing-location":
            location_backup.rename(journal_path)
        else:
            journal_path.write_bytes(saved_revision)
        if damage == "missing-journal":
            backup.rename(journal)


@pytest.mark.parametrize("damage", ["missing-receipt", "open-origin", "wal", "changed-nonce"])
def test_receiving_refusals_do_not_enter_mutation_or_backend_owners(received, monkeypatch, damage):
    fixture = received
    if damage == "missing-receipt":
        raw = yaml.safe_load(fixture.record.yaml_path.read_text())
        del raw["receiving_allocation"]
        fixture.record.yaml_path.write_text(yaml.safe_dump(raw))
    elif damage == "open-origin":
        raw = yaml.safe_load(fixture.origin.yaml_path.read_text())
        claim = raw["resources"][0]
        claim["state"] = "active"
        claim["producer"]["phase"] = "bound"
        del claim["producer"]["closeout"]
        fixture.origin.yaml_path.write_text(yaml.safe_dump(raw))
    elif damage == "wal":
        connection = sqlite3.connect(fixture.record.yaml_path.parent.parent / "workspace-creation.sqlite3")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.close()
    else:
        raw = yaml.safe_load(fixture.record.yaml_path.read_text())
        raw["creation_nonce"] = "different-incarnation"
        fixture.record.yaml_path.write_text(yaml.safe_dump(raw))

    def forbidden(*args, **kwargs):
        pytest.fail("unqualified receiving proof entered an effectful owner")

    for owner, name in (
        (relocation, "_stopped"), (relocation, "FinalizeLock"),
        (relocation.worktree_lifecycle, "guard"), (relocation.state, "write"),
        (receiving, "recover_missing"), (creation_requests, "_connection"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    before = _snapshot(fixture.home)
    for operation in (relocation.plan, relocation.move):
        with pytest.raises((ValueError, FileNotFoundError)):
            operation(fixture.record.worktree_id, str(fixture.destination), fixture.config)
        assert _snapshot(fixture.home) == before
    assert not fixture.destination.exists()


def test_receiving_interruption_reuses_original_operation_and_closed_proof(received, monkeypatch):
    fixture = received
    _receiving_only_admission(fixture, monkeypatch)
    save = tracking.save_record

    def interrupted(record, *args, **kwargs):
        save(record, *args, **kwargs)
        if record.worktree_id == fixture.record.worktree_id and record.worktree_path == str(fixture.destination):
            raise OSError("injected post-rebind interruption")

    monkeypatch.setattr(tracking, "save_record", interrupted)
    with pytest.raises(OSError, match="post-rebind"):
        relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert location.read(fixture.record.yaml_path)["phase"] == "git-repaired"
    current = tracking.load_record(fixture.record.yaml_path)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="unfinished"):
        execution_spaces.require_record_mutation(current, fixture.config)
    monkeypatch.setattr(tracking, "save_record", save)
    # The exact closed result was retained before effects; recovery does not
    # depend on keeping the settled creator's ordinary tracking file forever.
    fixture.origin.yaml_path.unlink()
    result = relocation.move(current.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "complete"
    assert receiving_relocation.inspect(tracking.load_record(current.yaml_path), fixture.config)


def test_receiving_prepublication_cancel_preserves_original_provenance(received, monkeypatch):
    fixture = received
    _receiving_only_admission(fixture, monkeypatch)
    original = fixture.record.yaml_path.read_bytes()
    journal = fixture.record.yaml_path.parent.parent / "workspace-creation.sqlite3"
    original_journal = journal.read_bytes()

    def interrupted(*args):
        raise OSError("injected before stage transfer")

    monkeypatch.setattr(relocation.transfer, "copy", interrupted)
    with pytest.raises(OSError, match="before stage transfer"):
        relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    retained = location.read(fixture.record.yaml_path)
    stage = Path(retained["stage"])
    assert stage.is_dir() and retained["phase"] == "copying"
    saved = location.journal_path(fixture.record.yaml_path).read_bytes()
    corrupt = copy.deepcopy(retained)
    corrupt["receiving_location"]["version"] = 99
    location.write(fixture.record.yaml_path, corrupt)
    before = _snapshot(fixture.home)
    with monkeypatch.context() as refusal:
        refusal.setattr(relocation, "FinalizeLock", lambda *_a, **_k: pytest.fail("unsupported abort acquired leases"))
        with pytest.raises(ValueError, match="current-location revision"):
            relocation.abort(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert _snapshot(fixture.home) == before
    location.journal_path(fixture.record.yaml_path).write_bytes(saved)

    def scoped_stage(path):
        assert Path(path) == stage and stage.parent == fixture.destination.parent
        return None

    monkeypatch.setattr(relocation.procs, "checkout_retirement_blocker", scoped_stage)
    result = relocation.abort(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "cancelled" and not stage.exists()
    assert fixture.source.is_dir() and not fixture.destination.exists()
    assert fixture.record.yaml_path.read_bytes() == original
    assert journal.read_bytes() == original_journal
    receiving.require_record(tracking.load_record(fixture.record.yaml_path), fixture.config)


_PRIOR_READER = r'''
import hashlib,json,os,pathlib,sys
def audit(event,args):
    forbidden=event in {"subprocess.Popen","socket.connect","socket.bind","os.mkdir","os.remove","os.rename","os.rmdir"}
    if event=="open":
        mode=args[1] or ""
        flags=args[2] if isinstance(args[2],int) else 0
        forbidden=not isinstance(args[0],int) and (
            any(x in mode for x in "wax+")
            or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        )
    if forbidden:
        print("PRIOR_READER_EFFECT="+event,file=sys.stderr,flush=True)
        raise OSError("prior-reader effect canary")
sys.addaudithook(audit)
from agent_worktrees import config as cfg,execution_spaces,receiving_allocation,tracking
module=pathlib.Path(receiving_allocation.__file__).resolve()
assert module.is_relative_to(pathlib.Path(sys.prefix).resolve())
options=json.loads(sys.argv[1])
record=tracking.load_record(pathlib.Path(options["ledger"]))
config=cfg.Config(
    srcroot=options["home"],repo_name=record.repo,machine=record.machine,
    platform=cfg.detect_platform(),repos={record.repo:cfg.RepoConfig(
        anchor=options["anchor"],worktree_root=options["root"],remote="origin",default_branch="main",
    )},
)
try:
    execution_spaces.require_record_mutation(record,config,recover_receiving=False)
except execution_spaces.ExecutionSpaceError as exc:
    result={"accepted":False,"error":str(exc)}
else:
    result={"accepted":True}
print(json.dumps({
    **result,"module":str(module),"sys_prefix":sys.prefix,
    "source_sha256":hashlib.sha256(module.read_bytes()).hexdigest(),
}),flush=True)
'''


def test_actual_prior_runtime_accepts_original_but_refuses_new_location_without_effects(
    received, monkeypatch, record_property,
):
    selected = os.environ.get("AGENT_WORKTREES_RELOCATION_PRIOR_PYTHON")
    if not selected:
        pytest.skip("explicit prior physical runtime not selected; mixed-version consumer remains unqualified")
    fixture = received
    env = dict(os.environ, AGENT_HOME=str(fixture.home), PYTHONDONTWRITEBYTECODE="1")
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "PYTHONPATH", "COPILOT_PLUGIN_ROOT",
        "AGENT_RT_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR",
    ):
        env.pop(key, None)
    options = json.dumps({
        "ledger": str(fixture.record.yaml_path), "home": str(fixture.home),
        "anchor": str(fixture.anchor), "root": fixture.config.default_repo.worktree_root,
    })

    def observe():
        relocation.durability.directory(fixture.source.parent)
        before = _snapshot(fixture.home)
        result = run_caller_owned(
            [selected, "-I", "-B", "-c", _PRIOR_READER, options],
            cwd=fixture.home, env=env, timeout=30,
            max_stdout_bytes=128 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "PRIOR_READER_EFFECT=" not in result.stderr, result.stderr
        assert _snapshot(fixture.home) == before
        return json.loads(result.stdout)

    baseline = observe()
    assert baseline["accepted"] is True
    record_property("prior_receiving_reader_sha256", baseline["source_sha256"])
    _receiving_only_admission(fixture, monkeypatch)
    relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    refusal = observe()
    assert refusal["accepted"] is False
    assert "unsupported relocation journal" in refusal["error"]
