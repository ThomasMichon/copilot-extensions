"""Typed retry staging uses the existing daemon and the Copilot handoff fence."""

from __future__ import annotations

from types import SimpleNamespace
import argparse
import dataclasses
import json
import threading
from pathlib import Path

import pytest

from agent_worktrees import launch_seed_exec, launch_seed_state as state, tracking, tracking_write
from agent_worktrees import config as cfg


@pytest.fixture(autouse=True)
def _cold_probe_boundary(monkeypatch):
    from agent_worktrees.sessions import LiveVerdict

    monkeypatch.setattr(
        launch_seed_exec.sessions, "verify_worktree_active", lambda *a: LiveVerdict(),
    )


def _record(tmp_path):
    project_dir = cfg.project_dir("demo")
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {"anchor": str(tmp_path), "default_branch": "dev"}},
    }), encoding="utf-8")
    return tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "checkout"), "demo", "test",
        "windows", tmp_path,
    ).yaml_path


def _settled_pair_receipt(path):
    from agent_worktrees import terminal_pair_progress

    sibling = tracking.load_record(path)
    sibling.kind, sibling.status = "bridge", "complete"
    sibling.origin, sibling.owner = "delegate", "dispatcher"
    sibling.worktree_path = str(path.parent / sibling.worktree_id)
    sibling.pair_id, sibling.pair_role, sibling.pair_kind = "fixture-pair", "knowledge", "worktree"
    sibling.pair_ref = tracking.format_claim_ref("test", "demo", "wt-primary")
    primary = dataclasses.replace(
        sibling, worktree_id="wt-primary", branch="worktree/wt-primary",
        worktree_path=str(path.parent / "wt-primary"), pair_role="harness",
        pair_ref=tracking.format_claim_ref("test", "demo", sibling.worktree_id),
    )
    primary_path = path.parent / "wt-primary.yaml"
    tracking.save_record(primary, primary_path)
    tracking.save_record(sibling, path)
    terminal_pair_progress.publish(primary, sibling, path, primary_path=primary_path)
    for record, record_path in ((primary, primary_path), (sibling, path)):
        for name in ("pair_id", "pair_role", "pair_ref", "pair_kind"):
            setattr(record, name, None)
        tracking.save_record(record, record_path)
    return primary_path


@pytest.mark.parametrize(("name", "old_version"), [
    (name, version)
    for name in ("stage", "take", "restore", "finish", "remove")
    for version in range(1, 5 if name in {"stage", "take", "remove"} else 4)
])
def test_seed_client_never_dials_pre_admission_daemon(tmp_path, monkeypatch, name, old_version):
    from agent_worktrees import locks, status_monitor_runtime

    path = _record(tmp_path)
    seed = state.stage(path, kind="new", text="initial intent")
    receipt = state.take(path, seed_id=seed.seed_id)
    verb = f"launch_seed_{name}"
    old = {
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture-token",
        "tracking_write_verbs": [verb],
        "tracking_write_verb_versions": {verb: old_version},
    }
    assert tracking_write.endpoint_from_rendezvous(old, verb=verb, min_version=old_version)
    monkeypatch.setattr(locks, "read_lock", lambda *args: old)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *args, **kwargs: pytest.fail("launch-seed mutation dialed an authority-unaware daemon"),
    )
    operations = {
        "stage": lambda: state.stage(path, kind="resume", text="new intent"),
        "take": lambda: state.take(path, seed_id=seed.seed_id),
        "restore": lambda: state.restore(path, receipt),
        "finish": lambda: state.finish(path, receipt),
        "remove": lambda: state.remove(path, remove_record=True),
    }
    if name == "remove":
        before = path.read_bytes(), state.state_path(path).read_bytes()
        with pytest.raises(ValueError, match="verb version 5"):
            operations[name]()
        assert (path.read_bytes(), state.state_path(path).read_bytes()) == before
    else:
        assert operations[name]() is not None


def test_remove_rejects_real_v3_daemon_without_direct_unlink_fallback(tmp_path, monkeypatch):
    from agent_worktrees import locks, status_monitor_runtime

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="preserve on mixed-version refusal")
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    before = {file: file.read_bytes() for file in (path, state.state_path(path), pair_receipt)}
    called = []
    server = tracking_write.start_server(lambda *args: called.append(args) or {"record_removed": True})
    server.start()
    old = tracking_write.rendezvous_fields(server)
    old["tracking_write_verb_versions"] = {**old["tracking_write_verb_versions"], "launch_seed_remove": 3}
    monkeypatch.setattr(locks, "read_lock", lambda *a: old)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(tracking_write, "run_direct", lambda *a, **k: pytest.fail("direct unlink fallback forbidden"))
    try:
        with pytest.raises(ValueError, match="verb version 5"):
            state.remove(path, remove_record=True)
        assert called == []
        assert {file: file.read_bytes() for file in before} == before
    finally:
        server.close()


def test_receipt_teardown_retains_launch_admission_at_real_daemon(tmp_path, monkeypatch):
    from agent_worktrees import launch_admission, locks, status_monitor_runtime

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="preserve while launch admission is held")
    receipt = path.with_suffix(".terminal-pair.json")
    receipt.write_text('{"version":1}', encoding="utf-8")
    files = (path, state.state_path(path), receipt)
    before = {file: file.read_bytes() for file in files}
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    monkeypatch.setattr(locks, "read_lock", lambda *a: endpoint)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(tracking_write, "run_direct", lambda *a, **k: pytest.fail("daemon required"))
    try:
        assert endpoint["tracking_write_verb_versions"]["launch_seed_remove"] == 5
        with launch_admission.guard(path):
            with pytest.raises(ValueError, match="launch admission is busy"):
                state.remove(path, remove_record=True)
        assert {file: file.read_bytes() for file in files} == before
        assert state.remove(path, remove_record=True)["record_removed"]
        assert all(not file.exists() for file in files)
    finally:
        server.close()


@pytest.mark.parametrize("system", ["Linux", "Windows", "Darwin"])
@pytest.mark.parametrize("owner", [
    "dead", "reused", "live", "unknown", "missing", "malformed", "invalid-pid", "missing-birth",
    "whitespace-birth", "garbage-birth", "noncanonical-birth", "nonascii-birth",
    "wrong-platform-birth", "invalid-microseconds", "malformed-current",
])
def test_old_removal_rendezvous_requires_proven_stale_owner(tmp_path, monkeypatch, owner, system):
    from agent_worktrees import locks, status_monitor_runtime

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="retain exact receipt authority")
    receipt = path.with_suffix(".terminal-pair.json")
    receipt.write_text('{"version":1}', encoding="utf-8")
    files = (path, state.state_path(path), receipt)
    before = {file: file.read_bytes() for file in files}
    recorded_birth = "111:1" if system == "Darwin" else "111"
    current_birth = "222:2" if system == "Darwin" else "222"
    old = {
        "pid": 123, "start_time": recorded_birth,
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture-token",
        "tracking_write_verbs": ["launch_seed_remove"],
        "tracking_write_verb_versions": {"launch_seed_remove": 4},
    }
    if owner == "missing":
        old.pop("pid")
    if owner == "malformed":
        old["start_time"] = {"invalid": "identity"}
    if owner == "invalid-pid":
        old["pid"] = True
    if owner == "missing-birth":
        old["start_time"] = None
    malformed_births = {
        "whitespace-birth": " ",
        "garbage-birth": "not-a-birth-token",
        "noncanonical-birth": "0111:1" if system == "Darwin" else "0111",
        "nonascii-birth": "\u0661\u0661\u0661",
        "wrong-platform-birth": "111" if system == "Darwin" else "111:1",
        "invalid-microseconds": "111:1000000",
    }
    if owner in malformed_births:
        old["start_time"] = malformed_births[owner]
    monkeypatch.setattr(state, "platform", SimpleNamespace(system=lambda: system))
    monkeypatch.setattr(locks, "read_lock", lambda *a: old)
    monkeypatch.setattr(locks, "pid_alive", lambda pid: owner != "dead")
    monkeypatch.setattr(
        locks, "process_start_time",
        lambda pid: (
            None if owner == "unknown" else
            "not-a-birth-token" if owner == "malformed-current" else
            recorded_birth if owner == "live" else current_birth
        ),
    )
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *a, **k: pytest.fail("an incompatible daemon must never be dialed"),
    )
    if owner in {"dead", "reused"}:
        assert state.remove(path, remove_record=True)["record_removed"]
        assert all(not file.exists() for file in files)
    else:
        with pytest.raises(ValueError, match="verb version 5"):
            state.remove(path, remove_record=True)
        assert {file: file.read_bytes() for file in files} == before


@pytest.mark.parametrize("recorded_birth", [" ", "not-a-birth-token"])
def test_old_removal_rendezvous_preserves_real_live_process(tmp_path, monkeypatch, recorded_birth):
    import os
    from agent_worktrees import locks, status_monitor_runtime

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="preserve live owner's intent")
    receipt = path.with_suffix(".terminal-pair.json")
    receipt.write_text('{"version":1}', encoding="utf-8")
    files = (path, state.state_path(path), receipt)
    before = {file: file.read_bytes() for file in files}
    old = {
        "pid": os.getpid(), "start_time": recorded_birth,
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture-token",
        "tracking_write_verbs": ["launch_seed_remove"],
        "tracking_write_verb_versions": {"launch_seed_remove": 4},
    }
    assert locks.pid_alive(old["pid"])
    assert locks.process_start_time(old["pid"]) is not None
    monkeypatch.setattr(locks, "read_lock", lambda *a: old)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda *a, **k: pytest.fail("malformed identity must not authorize direct removal"),
    )
    with pytest.raises(ValueError, match="verb version 5"):
        state.remove(path, remove_record=True)
    assert {file: file.read_bytes() for file in files} == before


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_real_daemon_retains_failed_attempt_and_finishes_exactly_one_handoff(
    tmp_path, monkeypatch, kind,
):
    path = _record(tmp_path)
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    def dispatch(verb, args):
        return tracking_write.dispatch(
            verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None, boot_wait_s=0,
        )
    monkeypatch.setattr(state, "_dispatch", dispatch)
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must use the real daemon")),
    )
    try:
        seed = state.stage(path, kind=kind, text="quotes ' \"\nsecond line")
        assert state.state_path(path) == tmp_path / "wt-a" / "launch-seed.json"
        assert state.peek(path) == seed
        first = state.take(path, seed_id=seed.seed_id)
        assert state.peek(path).text == seed.text
        assert state.peek(path).handoff_id
        assert state.take(path, seed_id=seed.seed_id)["seed"] is None
        assert state.restore(path, first)["restored"]
        assert state.peek(path).handoff_id is None
        second = state.take(path, seed_id=seed.seed_id)
        assert state.finish(path, second)["finished"]
        assert state.peek(path) is None
        assert state.finish(path, second)["finished"]
    finally:
        server.close()


def test_real_daemon_reports_native_transport_refusal_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import locks, status_monitor_runtime

    path = _record(tmp_path)
    record = tracking.load_record(path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp", state="unknown", binding_revision=1, blob={},
    )
    tracking.save_record(record, path)
    before = path.read_bytes()
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    monkeypatch.setattr(locks, "read_lock", lambda *_args: endpoint)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda *a, **kw: pytest.fail("a sent refusal must not fall back or retry"),
    )
    try:
        with pytest.raises(state.NativeSeedTransportError, match="native execution leg"):
            state.stage(path, kind="resume", text="accepted draft")
        assert path.read_bytes() == before
        assert not state.state_path(path).exists()
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_seed_is_discarded_after_backend_start_not_before(tmp_path, monkeypatch, kind):
    path = _record(tmp_path)
    seed = state.stage(path, kind=kind, text="next turn")
    argv = []
    real_popen = launch_seed_exec.subprocess.Popen
    def spawn(command, **kwargs):
        if command[0] != "copilot":
            return real_popen(command, **kwargs)
        pending = state.peek(path)
        assert pending.text == seed.text and pending.handoff_id
        argv.append(command)
        return SimpleNamespace(wait=lambda: 0)
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 0
    assert argv == [["copilot", "--interactive", seed.text]]
    assert state.peek(path) is None


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_backend_start_failure_releases_retry_without_losing_text(tmp_path, monkeypatch, kind):
    path = _record(tmp_path)
    seed = state.stage(path, kind=kind, text="next turn")
    def fail(*args, **kwargs):
        raise FileNotFoundError("backend disappeared")
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", fail)
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert state.peek(path).text == seed.text
    assert state.peek(path).seed_id == seed.seed_id
    assert state.peek(path).handoff_id is None


def test_stale_handoff_never_erases_or_restores_over_newer_intent(tmp_path):
    path = _record(tmp_path)
    old = state.stage(path, kind="new", text="first task")
    receipt = state.take(path, seed_id=old.seed_id)
    newer = state.stage(path, kind="resume", text="newer request")
    assert state.finish(path, receipt)["reason"] == "superseded"
    assert state.restore(path, receipt)["reason"] == "superseded"
    assert state.peek(path) == newer
    newer_receipt = state.take(path, seed_id=newer.seed_id)
    state.finish(path, newer_receipt)
    assert not state.restore(path, receipt)["restored"]
    assert state.peek(path) is None


def test_new_fallback_cannot_take_a_resume_seed(tmp_path):
    path = _record(tmp_path)
    resume = state.stage(path, kind="resume", text="resume request")
    assert state.claim_creation(path)["seed"] is None
    assert state.peek(path) == resume


def test_legacy_new_seed_migrates_without_two_owners(tmp_path):
    path = _record(tmp_path)
    record = tracking.load_record(path)
    record.pending_seed = "legacy creation prompt"
    record.pending_seed_revision += 1
    tracking.save_record(record, path)
    seed = state.pending(path)
    assert seed.kind == "new" and seed.text == "legacy creation prompt"
    assert tracking.load_record(path).pending_seed is None
    assert state.pending(path) == seed


def test_uncertain_cleanup_retains_handoff_without_resubmission(tmp_path, monkeypatch, capsys):
    path = _record(tmp_path)
    seed = state.stage(path, kind="resume", text="next turn")
    calls = []
    real_popen = launch_seed_exec.subprocess.Popen

    def spawn(argv, **kwargs):
        if argv[0] != "copilot":
            return real_popen(argv, **kwargs)
        calls.append(argv)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        state, "finish",
        lambda *a, **k: (_ for _ in ()).throw(tracking_write.AmbiguousWriteOutcome("late ack")),
    )
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert state.peek(path).text == seed.text and state.peek(path).handoff_id
    assert "cleanup is unconfirmed" in capsys.readouterr().err
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert len(calls) == 1


def test_saved_completion_tombstone_cannot_resurrect_prompt(tmp_path, monkeypatch):
    from agent_worktrees.resume_seed import seed_for_attempt

    path = _record(tmp_path)
    seed = state.stage(path, kind="new", text="must not repeat")
    receipt = state.take(path, seed_id=seed.seed_id)
    target = state.state_path(path)
    real_unlink = Path.unlink
    def fail_seed_unlink(self, *args, **kwargs):
        if self == target:
            raise OSError("interrupted after tombstone save")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_seed_unlink)
    with pytest.raises(OSError, match="tombstone"):
        state.finish(path, receipt)
    assert "must not repeat" not in target.read_text(encoding="utf-8")
    assert state.peek(path) is None
    assert state.pending(path) is None
    record = tracking.load_record(path)
    assert state.creation_text(path, record) is None
    assert seed_for_attempt(path, record, argparse.Namespace()) is None
    assert state.stage(path) is None
    assert state.take(path, seed_id=seed.seed_id)["seed"] is None
    assert not state.restore(path, receipt)["restored"]


def test_legacy_revision_tombstone_suppresses_extant_old_json(tmp_path):
    path = _record(tmp_path)
    seed = state.stage(path, kind="resume", text="already handed off")
    record = tracking.load_record(path)
    record.pending_seed_revision = seed.revision + 1
    tracking.save_record(record, path)
    state.state_path(path).write_text(
        json.dumps({"version": 1, **dataclasses.asdict(seed)}), encoding="utf-8",
    )
    assert state.peek(path) is None
    assert state.pending(path) is None
    assert state.stage(path) is None


def test_successful_managed_reap_removes_only_owned_seed_state(tmp_path, monkeypatch):
    from agent_worktrees import reap_cli

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="private pending prompt")
    pair_receipt = path.with_suffix(".terminal-pair.json")
    _settled_pair_receipt(path)
    other_receipt = tmp_path / "other.terminal-pair.json"
    other_receipt.write_text("retain other record evidence", encoding="utf-8")
    other = state.state_path(path).parent / "unrelated-state.txt"
    other.write_text("keep me", encoding="utf-8")
    record = tracking.load_record(path)
    record.branch = ""
    record.worktree_path = ""
    monkeypatch.setattr(reap_cli.sessions, "has_mux_session", lambda *a: False)
    monkeypatch.setattr(
        reap_cli.sessions, "scan_sessions_fast",
        lambda *a: SimpleNamespace(active_sessions=set()),
    )
    monkeypatch.setattr(reap_cli.disposition_history, "remove", lambda *a: None)
    monkeypatch.setattr(reap_cli.handoff_trace, "remove_trace", lambda *a: None)
    removed, warnings = reap_cli._remove_managed_worktree(
        record, SimpleNamespace(anchor=str(tmp_path)), tmp_path,
    )
    assert removed and not warnings
    assert not path.exists() and not state.state_path(path).exists()
    assert not pair_receipt.exists()
    assert other_receipt.read_text(encoding="utf-8") == "retain other record evidence"
    assert other.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize("fault", [
    "pair_id", "pair_role", "pair_ref", "pair_kind", "identity", "owner",
    "missing-primary", "receipt", "missing-path",
])
def test_terminal_pair_gc_preserves_unknown_primary_proof(tmp_path, fault):
    from agent_worktrees import terminal_pair_progress
    import yaml

    path = _record(tmp_path)
    primary_path = _settled_pair_receipt(path)
    receipt_path = path.with_suffix(".terminal-pair.json")
    assert terminal_pair_progress.gc_preservation_reason(path) is None
    if fault.startswith("pair_"):
        document = yaml.safe_load(primary_path.read_text(encoding="utf-8"))
        document[fault] = False
        primary_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    elif fault in {"identity", "owner"}:
        primary = tracking.load_record(primary_path)
        if fault == "identity":
            primary.branch = "worktree/another-allocation"
        else:
            primary.owner = "another-owner"
        tracking.save_record(primary, primary_path)
    elif fault == "missing-primary":
        primary_path.unlink()
    elif fault == "receipt":
        receipt_path.write_text("{}", encoding="utf-8")
    else:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.pop("primary_record_path")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = path.read_bytes(), receipt_path.read_bytes()
    assert terminal_pair_progress.gc_preservation_reason(path).startswith("terminal-pair")
    assert (path.read_bytes(), receipt_path.read_bytes()) == before


def test_terminal_pair_gc_primary_waits_until_sibling_receipt_is_collected(tmp_path):
    from agent_worktrees import terminal_pair_progress

    path = _record(tmp_path)
    primary_path = _settled_pair_receipt(path)
    assert terminal_pair_progress.gc_preservation_reason(primary_path) == "terminal-pair-sibling-gc-pending"
    assert terminal_pair_progress.gc_preservation_reason(path) is None
    state.remove(path, remove_record=True)
    assert terminal_pair_progress.gc_preservation_reason(primary_path) is None
    state.remove(primary_path, remove_record=True)
    assert not primary_path.exists() and not primary_path.with_suffix(".terminal-pair.json").exists()


def test_pair_gc_primary_preserved_during_sibling_record_rollback(tmp_path, monkeypatch):
    from agent_worktrees import reap_cli, terminal_pair_progress

    path = _record(tmp_path)
    primary_path = _settled_pair_receipt(path)
    receipt_path = path.with_suffix(".terminal-pair.json")
    before = path.read_bytes(), receipt_path.read_bytes(), primary_path.read_bytes()
    original_unlink = Path.unlink

    def fail_sibling_yaml(file, *args, **kwargs):
        if file == path:
            assert not receipt_path.exists()
            primary = tracking.load_record(primary_path)
            primary.branch = primary.worktree_path = ""
            removed, reasons = reap_cli._remove_managed_worktree(
                primary, SimpleNamespace(anchor=str(tmp_path)), tmp_path,
            )
            assert not removed and any("terminal-pair" in reason for reason in reasons)
            raise OSError("injected sibling YAML rollback")
        return original_unlink(file, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_sibling_yaml)
    with pytest.raises(OSError, match="sibling YAML rollback"):
        state.remove(path, remove_record=True)
    assert (path.read_bytes(), receipt_path.read_bytes(), primary_path.read_bytes()) == before
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert terminal_pair_progress.gc_preservation_reason(path) is None
    state.remove(path, remove_record=True)
    assert terminal_pair_progress.gc_preservation_reason(primary_path) is None


@pytest.mark.parametrize("fault", ["pair-id", "role", "sibling-ref", "primary-ref"])
def test_pair_gc_rejects_well_typed_nonreciprocal_proof(tmp_path, fault):
    from agent_worktrees import terminal_pair_progress

    path = _record(tmp_path)
    _settled_pair_receipt(path)
    receipt_path = path.with_suffix(".terminal-pair.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if fault == "pair-id":
        receipt["sibling_pair"]["pair_id"] = "another-pair"
    elif fault == "role":
        receipt["sibling_pair"]["pair_role"] = receipt["primary"]["pair"]["pair_role"]
    elif fault == "sibling-ref":
        receipt["sibling_pair"]["pair_ref"] = tracking.format_claim_ref("test", "demo", "another-primary")
    else:
        receipt["primary"]["pair"]["pair_ref"] = tracking.format_claim_ref("test", "demo", "another-sibling")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = path.read_bytes(), receipt_path.read_bytes()
    assert terminal_pair_progress.gc_preservation_reason(path) is not None
    assert (path.read_bytes(), receipt_path.read_bytes()) == before


def test_terminal_pair_sidecar_removal_preserves_receipt_when_record_retained(tmp_path):
    path = _record(tmp_path)
    state.stage(path, kind="resume", text="seed only")
    record = tracking.load_record(path)
    record.pair_id, record.pair_role, record.pair_kind = "fixture-pair", "harness", "worktree"
    record.pair_ref = tracking.format_claim_ref("test", "demo", "wt-sibling")
    tracking.save_record(record, path)
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    before = path.read_bytes(), pair_receipt.read_bytes()
    result = state.remove(path)
    assert result["record_removed"] is False
    assert (path.read_bytes(), pair_receipt.read_bytes()) == before


def test_terminal_pair_sidecar_removal_preserves_unknown_owner(tmp_path):
    path = _record(tmp_path)
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    path.unlink()
    before = pair_receipt.read_bytes()
    with pytest.raises(ValueError, match="without its worktree record"):
        state.remove(path, remove_record=True)
    assert pair_receipt.read_bytes() == before


@pytest.mark.parametrize("failure", ["seed", "record", "receipt"])
@pytest.mark.parametrize("invalid_utf8", [False, True])
def test_terminal_pair_sidecar_removal_failure_retains_and_retries(
    tmp_path, monkeypatch, failure, invalid_utf8,
):
    path = _record(tmp_path)
    state.stage(path, kind="resume", text="retain on failed removal")
    target = state.state_path(path)
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    if invalid_utf8:
        target.write_bytes(b"\xff\x00seed\r\n")
        pair_receipt.write_bytes(b"\xfe\x00receipt\r\n")
    before = {file: file.read_bytes() for file in (path, target, pair_receipt)}
    failed_path = {"record": path, "receipt": pair_receipt, "seed": target}[failure]
    unlink = Path.unlink

    def fail(file, *args, **kwargs):
        if file == failed_path:
            raise OSError("injected owned-state deletion failure")
        return unlink(file, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(OSError, match="injected owned-state"):
        state.remove(path, remove_record=True)
    assert {file: file.read_bytes() for file in before} == before
    monkeypatch.setattr(Path, "unlink", unlink)
    assert state.remove(path, remove_record=True)["record_removed"] is True
    assert not any(file.exists() for file in before)
    assert state.remove(path, remove_record=True)["record_removed"] is True


@pytest.mark.parametrize("boundary", ["seed", "receipt", "record"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_terminal_pair_sidecar_removal_crash_keeps_authority_until_last(
    tmp_path, monkeypatch, boundary, position,
):
    class InterruptedRemoval(BaseException):
        pass

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="old allocation")
    seed_path = state.state_path(path)
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    before_record = path.read_bytes()
    newer = tracking.create_new_record(
        "wt-newer", "worktree/wt-newer", str(tmp_path / "newer-checkout"),
        "demo", "test", "windows", tmp_path,
    ).yaml_path
    state.stage(newer, kind="new", text="newer allocation stays owned")
    newer_receipt = newer.with_suffix(".terminal-pair.json")
    newer_receipt.write_text('{"version":1,"owner":"newer"}', encoding="utf-8")
    foreign = tmp_path / "foreign-state.txt"
    foreign.write_text("foreign evidence", encoding="utf-8")
    untouched = {file: file.read_bytes() for file in
                 (newer, state.state_path(newer), newer_receipt, foreign)}
    targets = {"seed": seed_path, "receipt": pair_receipt, "record": path}
    failed_path = targets[boundary]
    unlink = Path.unlink
    order = []

    def interrupt(file, *args, **kwargs):
        if file not in targets.values():
            return unlink(file, *args, **kwargs)
        name = next(name for name, target in targets.items() if target == file)
        order.append(name)
        if file == failed_path and position == "before":
            raise InterruptedRemoval()
        result = unlink(file, *args, **kwargs)
        if file == failed_path:
            raise InterruptedRemoval()
        return result

    monkeypatch.setattr(Path, "unlink", interrupt)
    with pytest.raises(InterruptedRemoval):
        state.remove(path, remove_record=True)
    assert order == ["seed", "receipt", "record"][:len(order)]
    if boundary == "record" and position == "after":
        assert not any(file.exists() for file in targets.values())
    else:
        assert path.read_bytes() == before_record
    assert {file: file.read_bytes() for file in untouched} == untouched
    monkeypatch.setattr(Path, "unlink", unlink)
    assert state.remove(path, remove_record=True)["record_removed"] is True
    assert not any(file.exists() for file in targets.values())
    assert {file: file.read_bytes() for file in untouched} == untouched


def test_terminal_pair_sidecar_removal_preserves_rejected_authority(tmp_path, monkeypatch):
    from agent_worktrees.execution_spaces import ExecutionSpaceError

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="retain unproven state")
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    before = {file: file.read_bytes() for file in (path, state.state_path(path), pair_receipt)}

    def reject(*args):
        raise ExecutionSpaceError("fixture authority unavailable")

    monkeypatch.setattr(state, "require_project_record_mutation", reject)
    with pytest.raises(ValueError, match="authority unavailable"):
        state.remove(path, remove_record=True)
    assert {file: file.read_bytes() for file in before} == before


def test_terminal_pair_sidecar_removal_preserves_unproven_symbolic_state(tmp_path, monkeypatch):
    path = _record(tmp_path)
    state.stage(path, kind="resume", text="retain linked state")
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    before = {file: file.read_bytes() for file in (path, state.state_path(path), pair_receipt)}
    symbolic = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda file: file == pair_receipt or symbolic(file))
    with pytest.raises(ValueError, match="symbolic link"):
        state.remove(path, remove_record=True)
    assert {file: file.read_bytes() for file in before} == before


def test_real_daemon_terminal_pair_sidecar_removal_is_complete_and_repeatable(tmp_path, monkeypatch):
    path = _record(tmp_path)
    state.stage(path, kind="resume", text="fixture seed")
    pair_receipt = path.with_suffix(".terminal-pair.json")
    pair_receipt.write_text('{"version":1}', encoding="utf-8")
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)

    def dispatch(verb, args, **kwargs):
        return tracking_write.dispatch(
            verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None,
            boot_wait_s=0, min_version=kwargs.get("min_version", 2),
        )

    monkeypatch.setattr(state, "_dispatch", dispatch)
    monkeypatch.setattr(tracking_write, "run_direct", lambda *a, **kw: pytest.fail("must use real daemon"))
    try:
        assert state.remove(path, remove_record=True)["record_removed"] is True
        assert not path.exists() and not state.state_path(path).exists() and not pair_receipt.exists()
        assert state.remove(path, remove_record=True)["record_removed"] is True
    finally:
        server.close()


@pytest.mark.parametrize("failure", ["live", "receipt"])
def test_native_reap_terminal_pair_sidecar_failure_preserves_evidence(tmp_path, monkeypatch, failure):
    from agent_worktrees import reap_cli

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="retain native retry")
    pair_receipt = path.with_suffix(".terminal-pair.json")
    _settled_pair_receipt(path)
    before = {file: file.read_bytes() for file in (path, state.state_path(path), pair_receipt)}
    record = tracking.load_record(path)
    record.branch = record.worktree_path = ""
    monkeypatch.setattr(reap_cli.sessions, "has_mux_session", lambda *a: failure == "live")
    monkeypatch.setattr(reap_cli.sessions, "scan_sessions_fast", lambda *a: SimpleNamespace(active_sessions=set()))
    monkeypatch.setattr(reap_cli.disposition_history, "remove", lambda *a: None)
    monkeypatch.setattr(reap_cli.handoff_trace, "remove_trace", lambda *a: None)
    unlink = Path.unlink

    def fail(file, *args, **kwargs):
        if file == pair_receipt:
            raise OSError("injected native receipt failure")
        return unlink(file, *args, **kwargs)

    if failure == "receipt":
        monkeypatch.setattr(Path, "unlink", fail)
    removed, warnings = reap_cli._remove_managed_worktree(
        record, SimpleNamespace(anchor=str(tmp_path)), tmp_path,
    )
    assert removed is False
    assert warnings and ("live mux" if failure == "live" else "injected native receipt") in warnings[0]
    assert {file: file.read_bytes() for file in before} == before
    if failure == "receipt":
        monkeypatch.setattr(Path, "unlink", unlink)
        assert reap_cli._remove_managed_worktree(
            record, SimpleNamespace(anchor=str(tmp_path)), tmp_path,
        ) == (True, [])
        assert not any(file.exists() for file in before)


def test_atomic_removal_prevents_concurrent_seed_recreation(tmp_path, monkeypatch):
    path = _record(tmp_path)
    state.stage(path, kind="resume", text="old private prompt")
    target = state.state_path(path)
    start_writer = threading.Event()
    writer_attempting = threading.Event()
    failures = []
    def writer():
        assert start_writer.wait(3)
        writer_attempting.set()
        try:
            state.stage(path, kind="resume", text="must not become orphaned")
        except FileNotFoundError:
            failures.append("record gone")
    real_unlink = Path.unlink
    def pause_after_seed_remove(self, *args, **kwargs):
        result = real_unlink(self, *args, **kwargs)
        if self == target:
            start_writer.set()
            assert writer_attempting.wait(3)
        return result
    monkeypatch.setattr(Path, "unlink", pause_after_seed_remove)
    thread = threading.Thread(target=writer)
    thread.start()
    removed = state.remove(path, remove_record=True)
    thread.join(5)
    assert not thread.is_alive()
    assert removed["record_removed"] is True
    assert failures == ["record gone"]
    assert not path.exists() and not target.exists()


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_gh_intermediary_does_not_acknowledge_or_consume_seed(tmp_path, monkeypatch, kind):
    path = _record(tmp_path)
    seed = state.stage(path, kind=kind, text="preserve until actual Copilot")
    monkeypatch.setattr(
        launch_seed_exec.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start gh")),
    )
    assert launch_seed_exec.launch(
        path, ["gh", "copilot"], invoke=True, seed_id=seed.seed_id,
    ) == 3
    assert state.peek(path) == seed


@pytest.mark.parametrize("case", ["mux", "bare", "unknown"])
@pytest.mark.parametrize("kind", ["new", "resume"])
def test_direct_handoff_rechecks_liveness_and_retains_seed(tmp_path, monkeypatch, kind, case):
    from agent_worktrees.sessions import LiveVerdict

    path = _record(tmp_path)
    seed = state.stage(path, kind=kind, text="do not inject live")
    verdict = (
        LiveVerdict(active=True, mux_live=True) if case == "mux"
        else LiveVerdict(active=True, bare=True, live_session_ids=["other"]) if case == "bare"
        else LiveVerdict(probes_ok=False)
    )
    monkeypatch.setattr(launch_seed_exec.sessions, "verify_worktree_active", lambda *a: verdict)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("PSMUX_PANE", raising=False)
    monkeypatch.setattr(
        launch_seed_exec.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start a live target")),
    )
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert state.peek(path) == seed


def test_newly_created_owned_mux_shell_is_not_a_live_copilot(tmp_path, monkeypatch):
    from agent_worktrees.sessions import LiveVerdict

    path = _record(tmp_path)
    seed = state.stage(path, kind="new", text="first turn")
    monkeypatch.setattr(
        launch_seed_exec.sessions, "verify_worktree_active",
        lambda *a: LiveVerdict(active=True, mux_live=True),
    )
    monkeypatch.setenv("TMUX_PANE", "%owned")
    monkeypatch.setattr(launch_seed_exec.sessions, "mux_active_pane", lambda *a: "%owned")
    monkeypatch.setattr(
        launch_seed_exec.subprocess, "Popen",
        lambda *a, **k: SimpleNamespace(wait=lambda: 0),
    )
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 0
    assert state.peek(path) is None
