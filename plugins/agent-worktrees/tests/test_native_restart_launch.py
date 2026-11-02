"""Restart Resume must cross the real embody fence without replacing history."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import handoff_cli, launch_admission, native_restart_check
from agent_worktrees import native_source_context, record_cache, sessions, tracking


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/WSL restart owner contract")
NATIVE = "00000000-0000-4000-8000-000000000001"
TARGET = {"platform": "linux", "environment_id": "b" * 32, "boot_id": "boot-new"}
SOURCE = {**TARGET, "boot_id": "boot-old"}


@pytest.fixture
def subject(tmp_path, sample_record, monkeypatch):
    worktree = tmp_path / "checkout"
    worktree.mkdir()
    record = replace(
        sample_record, worktree_path=str(worktree), platform="linux",
        creation_nonce="0123456789abcdef", head_revision=5, lifecycle_revision=8,
        head_session=NATIVE, sessions=[tracking.SessionEntry(
            NATIVE, "start", activations=[tracking.SessionActivation(
                2, "start", "start",
                source_identity=native_source_context.identity(NATIVE, 42, "123", SOURCE),
            )],
        )],
    )
    path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    request = {
        "request_id": str(uuid4()), "source_epoch": SOURCE, "native_incarnation": "42:123",
        "expected": {
            "project": record.repo, "worktree_id": record.worktree_id,
            "worktree_path": str(worktree), "native_session_id": NATIVE,
            "worktree_incarnation": record.creation_nonce, "head_revision": 5,
            "owner_revision": 8, "activation_ordinal": 2,
            "source_identity": record.sessions[0].activations[0].source_identity,
            "target_epoch": TARGET,
        },
    }
    repo = SimpleNamespace(
        worktree_root=str(tmp_path), launch={}, launch_recovery={},
        setup_hook={}, env_script={}, copilot_path={}, anchor=str(worktree),
    )
    config = SimpleNamespace(default_repo=repo, profile_assignment=None)
    monkeypatch.setattr(handoff_cli.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(handoff_cli.cfg, "load_config", lambda: config)
    monkeypatch.setattr(handoff_cli.worktree_identity, "_resolve_worktree_id", lambda x: x)
    monkeypatch.setattr(core, "_repo_for_record", lambda *_: repo)
    monkeypatch.setattr(core, "_preflight_launch", lambda *_: core.LaunchPreflight())
    monkeypatch.setattr(core, "_build_launch_cmd", lambda *_, **kw: ["copilot"])
    monkeypatch.setattr(core, "_build_env", lambda *_, **kw: {})
    monkeypatch.setattr(core, "_repo_session_env", lambda *_: {})
    monkeypatch.setattr(core, "_launch_profile_selection", lambda *_, **kw: pytest.fail("profile allocation"))
    monkeypatch.setattr(native_source_context, "epoch", lambda: dict(TARGET))
    monkeypatch.setattr(sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    monkeypatch.setattr(native_restart_check, "session_liveness", lambda _: "dead")
    monkeypatch.setattr(sessions, "mux_seed_pane", lambda *_, **kw: pytest.fail("post-fence seed typing"))
    launches = []

    def spawn(wt, cwd, argv, env):
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            with launch_admission.guard(path):
                pass
        launches.append((wt, cwd, argv, env))
        return {"ok": True, "session": sessions.mux_session_name(wt), "new_pane": "%1"}

    monkeypatch.setattr(sessions, "mux_new_session", spawn)
    return SimpleNamespace(path=path, record=record, request=request, launches=launches, config=config)


def invoke(subject, capfd, *extra):
    args = core.build_parser().parse_args([
        "embody", "--worktree-id", subject.record.worktree_id,
        "--restart-request", json.dumps(subject.request), "--json", *extra,
    ])
    rc = handoff_cli.cmd_embody(args)
    return rc, json.loads(capfd.readouterr().out)


def test_exact_original_resume_has_durable_reservation_and_full_status_first_seed(subject, capfd, monkeypatch):
    from agent_worktrees import native_restart_receipt

    original = sessions.mux_new_session

    def spawn(*args):
        receipt = native_restart_receipt.read(subject.path)
        assert receipt["state"] == "launch-uncertain"
        assert receipt["request"] == subject.request
        assert receipt["seed_acknowledged"] is False
        return original(*args)

    monkeypatch.setattr(sessions, "mux_new_session", spawn)
    rc, result = invoke(subject, capfd)
    assert rc == 0
    assert result["state"] == "mux-created"
    assert result["restored"] is False
    assert result["seed_acknowledged"] is False
    assert result["automatic_eligible"] is False
    argv = subject.launches[0][2]
    assert argv[:2] == ["copilot", "--resume=" + NATIVE]
    assert argv[2] == "--interactive"
    assert "STATUS FIRST" in argv[3]
    assert "restart" in argv[3].lower()
    assert "not renewed permission" in argv[3]
    assert "claims" in argv[3] and "holds" in argv[3]
    assert "-i" not in argv
    assert native_restart_receipt.read(subject.path)["state"] == "mux-created"

def test_restart_late_join_never_claims_its_own_mux_creation(subject, capfd, monkeypatch):
    from agent_worktrees import native_restart_receipt

    monkeypatch.setattr(sessions, "mux_new_session", lambda *a: {
        "ok": True, "created": False, "session": "existing", "new_pane": None,
    })
    rc, result = invoke(subject, capfd)
    assert rc == 4 and result["state"] == "launch-uncertain"
    assert native_restart_receipt.read(subject.path)["state"] == "launch-uncertain"
    assert subject.launches == []


def test_canonical_original_self_claim_survives_exact_fenced_resume(subject, capfd):
    claim = tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            subject.record.machine, subject.record.repo, subject.record.worktree_id,
            session=NATIVE,
        ),
    )
    tracking.save_record(replace(subject.record, resources=[claim]), subject.path)
    record_cache.clear()
    before = subject.path.read_bytes()
    rc, result = invoke(subject, capfd)
    assert rc == 0, result
    assert result["state"] == "mux-created"
    assert result["restored"] is False and result["automatic_eligible"] is False
    assert subject.launches[0][2][1] == "--resume=" + NATIVE
    assert subject.path.read_bytes() == before
    assert invoke(subject, capfd)[0] == 0
    assert len(subject.launches) == 1


def test_native_start_transaction_claim_is_retained_by_restart_resume(monkeypatch_config, subject, capfd):
    from agent_worktrees.tracking_session_registration_write import apply_session_register

    tracking.save_record(
        replace(subject.record, head_session=None, head_transitions=[], sessions=[]),
        subject.path,
    )
    result = apply_session_register({
        "worktree_id": subject.record.worktree_id, "yaml_path": str(subject.path),
        "session_id": NATIVE, "source_identity": subject.request["expected"]["source_identity"],
        "source": "hook", "started_at": "2026-01-01T00:00:00+00:00",
    })
    assert result["ok"] is True, result
    record_cache.clear()
    record = tracking.load_record(subject.path)
    assert len(record.resources) == 1 and record.resources[0].kind == "session"
    subject.request["expected"].update(
        owner_revision=record.lifecycle_revision, head_revision=record.head_revision,
        activation_ordinal=record.sessions[0].activations[0].ordinal,
    )
    before = subject.path.read_bytes()
    rc, result = invoke(subject, capfd)
    assert rc == 0 and result["state"] == "mux-created", result
    assert subject.path.read_bytes() == before
    assert subject.launches[0][2][1] == "--resume=" + NATIVE


@pytest.mark.parametrize("damage", [
    "other-session", "other-machine", "other-project", "other-worktree",
    "released", "at-rest", "producer", "opaque", "duplicate", "task-mixed",
    "inbound", "dispatch", "released-ledger", "action-time-replacement",
])
def test_self_claim_admission_never_relaxes_other_owner_policy(subject, capfd, monkeypatch, damage):
    claim = tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            subject.record.machine, subject.record.repo, subject.record.worktree_id,
            session=NATIVE,
        ),
    )
    changes = {"resources": [claim]}
    if damage.startswith("other-"):
        fields = dict(machine=subject.record.machine, project=subject.record.repo,
                      worktree_id=subject.record.worktree_id, session=NATIVE)
        field = {"other-session": "session", "other-machine": "machine",
                 "other-project": "project", "other-worktree": "worktree_id"}[damage]
        fields[field] = "00000000-0000-4000-8000-000000000002" if field == "session" else "other"
        claim.ref = tracking.format_claim_ref(**fields)
    elif damage in {"released", "at-rest"}:
        claim.state = damage
    elif damage == "opaque":
        claim.metadata_opaque = True
    elif damage == "duplicate":
        changes["resources"].append(replace(claim))
    elif damage == "task-mixed":
        changes["resources"].append(tracking.ResourceClaim(kind="task", ref="dispatch-local-task:t"))
    elif damage == "inbound":
        changes["owner_ref"] = "other/project/worktree"
    elif damage == "dispatch":
        changes["dispatch_attempt_raw_present"] = True
        changes["dispatch_attempt_opaque"] = True
    elif damage == "released-ledger":
        changes["last_finalize_released"] = [claim]
    elif damage == "action-time-replacement":
        def preflight(*_):
            tracking.save_record(
                replace(subject.record, resources=[replace(claim, state="released")]),
                subject.path,
            )
            return core.LaunchPreflight()
        monkeypatch.setattr(core, "_preflight_launch", preflight)
    tracking.save_record(replace(subject.record, **changes), subject.path)
    if damage == "producer":
        import yaml

        raw = yaml.safe_load(subject.path.read_text())
        raw["resources"][0]["producer"] = None
        subject.path.write_text(yaml.safe_dump(raw))
    record_cache.clear()
    if damage == "dispatch":
        persisted = tracking.load_record(subject.path)
        assert persisted.dispatch_attempt_raw_present is True
        assert persisted.dispatch_attempt_opaque is True
    rc, result = invoke(subject, capfd)
    assert rc != 0 and "action-time-claim-policy-unqualified" in result["error"]
    assert subject.launches == []
    from agent_worktrees import native_restart_receipt
    assert native_restart_receipt.read(subject.path) is None


@pytest.mark.parametrize("change,reason", [
    ({"head_revision": 6}, "head-changed"),
    ({"head_session": "00000000-0000-4000-8000-000000000002", "sessions": [
        tracking.SessionEntry("00000000-0000-4000-8000-000000000002", "new"),
    ]}, "head-changed"),
    ({"paused": True}, "paused"),
    ({"bound_agent": "worker"}, "profile-policy-unqualified"),
    ({"resources": [tracking.ResourceClaim(kind="task", ref="dispatch-local-task:task-1")]},
     "action-time-claim-policy-unqualified"),
])
def test_current_policy_refuses_without_reserving_or_launching(subject, capfd, change, reason):
    tracking.save_record(replace(subject.record, **change), subject.path)
    rc, result = invoke(subject, capfd)
    assert rc != 0 and reason in result["error"]
    assert subject.launches == []
    from agent_worktrees import native_restart_receipt
    assert native_restart_receipt.read(subject.path) is None


@pytest.mark.parametrize("mutation,reason", [
    ("same-boot", "same-boot"),
    ("foreign", "foreign-environment"),
    ("wrong-source", "source-identity"),
    ("unknown-mux", "mux-state-unknown"),
    ("existing-mux", "existing-mux"),
    ("staged-seed", "pending-seed"),
])
def test_source_epoch_and_pending_intent_are_not_inferred(subject, capfd, monkeypatch, mutation, reason):
    from agent_worktrees import launch_seed_state

    if mutation == "same-boot":
        subject.request["source_epoch"] = TARGET
    elif mutation == "foreign":
        subject.request["source_epoch"] = {**SOURCE, "environment_id": "c" * 32}
    elif mutation == "wrong-source":
        subject.request["native_incarnation"] = "43:123"
    elif mutation == "unknown-mux":
        monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: None)
    elif mutation == "existing-mux":
        monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {
            sessions.mux_session_name(subject.record.worktree_id): 0,
        })
    else:
        monkeypatch.setattr(launch_seed_state, "peek", lambda *_, **kw: object())
    rc, result = invoke(subject, capfd)
    assert rc != 0 and reason in result["error"]
    assert subject.launches == []


def test_timeout_after_native_call_never_becomes_permission_to_retry(subject, capfd, monkeypatch):
    calls = []

    def timeout(*_):
        calls.append(True)
        return {"ok": False, "error": "mux invocation timed out"}

    monkeypatch.setattr(sessions, "mux_new_session", timeout)
    rc, result = invoke(subject, capfd)
    assert rc != 0 and result["state"] == "launch-uncertain"
    assert result["seed_acknowledged"] is False
    rc, repeated = invoke(subject, capfd)
    assert rc != 0 and repeated["state"] == "launch-uncertain"
    assert calls == [True]


def test_receipt_is_idempotent_even_after_native_start_advanced_owner(subject, capfd):
    assert invoke(subject, capfd)[0] == 0
    tracking.save_record(replace(subject.record, lifecycle_revision=9), subject.path)
    rc, result = invoke(subject, capfd)
    assert rc == 0 and result["state"] == "mux-created"
    assert result["restored"] is False
    assert len(subject.launches) == 1


def test_changed_request_cannot_overwrite_unreconciled_receipt(subject, capfd):
    assert invoke(subject, capfd)[0] == 0
    subject.request["request_id"] = str(uuid4())
    rc, result = invoke(subject, capfd)
    assert rc != 0 and "unreconciled-restart" in result["error"]
    assert len(subject.launches) == 1


def test_dry_run_is_read_only_and_never_launch_authority(subject, capfd):
    rc, result = invoke(subject, capfd, "--dry-run")
    assert rc == 0 and result["launch_authorized"] is False
    from agent_worktrees import native_restart_receipt
    assert native_restart_receipt.read(subject.path) is None
    assert subject.launches == []


@pytest.mark.parametrize("launched", [False, True])
def test_receipt_inspection_never_calls_launch_config_or_acquires_admission(subject, capfd, monkeypatch, launched):
    if launched:
        assert invoke(subject, capfd)[0] == 0
    before = {path: path.read_bytes() for path in subject.path.parent.rglob("*") if path.is_file()}
    monkeypatch.setattr(handoff_cli.cfg, "load_config", lambda: pytest.fail("read-only provisioning"))
    rc, result = invoke(subject, capfd, "--restart-receipt")
    assert rc == 0 and result["state"] == ("mux-created" if launched else "not-reserved")
    assert result["seed_acknowledged"] is False
    assert before == {path: path.read_bytes() for path in subject.path.parent.rglob("*") if path.is_file()}


@pytest.mark.parametrize("flag", ["--seed=other", "--fresh", "--resume-head",
                                 "--bridge-acquisition=fresh", "--ensure-mux", "--recovery",
                                 "--copilot-arg=--resume=other"])
def test_restart_cannot_mix_other_launch_intents(subject, capfd, flag):
    try:
        rc, _ = invoke(subject, capfd, flag)
    except SystemExit:
        return
    assert rc != 0
    assert subject.launches == []


def test_owner_change_during_preflight_is_refused_at_action_time(subject, capfd, monkeypatch):
    def preflight(*_):
        tracking.save_record(replace(subject.record, paused=True), subject.path)
        return core.LaunchPreflight()

    monkeypatch.setattr(core, "_preflight_launch", preflight)
    rc, result = invoke(subject, capfd)
    assert rc != 0 and "paused" in result["error"]
    assert subject.launches == []


@pytest.mark.parametrize("phase", ["reservation", "outcome"])
def test_denied_receipt_write_does_not_lose_uncertainty(subject, capfd, monkeypatch, phase):
    from agent_worktrees import native_restart_receipt

    real_write = native_restart_receipt.write

    def write(*args, reserve):
        if reserve == (phase == "reservation"):
            raise OSError("simulated denied durable write")
        return real_write(*args, reserve=reserve)

    monkeypatch.setattr(native_restart_receipt, "write", write)
    assert invoke(subject, capfd)[0] != 0
    assert len(subject.launches) == (phase == "outcome")
    if phase == "outcome":
        monkeypatch.setattr(native_restart_receipt, "write", real_write)
        rc, result = invoke(subject, capfd)
        assert rc != 0 and result["state"] == "launch-uncertain"
        assert len(subject.launches) == 1


@pytest.mark.parametrize("damage", ["corrupt", "oversized", "symlink", "exposed", "duplicate"])
def test_bad_receipt_is_not_reset_or_retried(subject, capfd, damage):
    from agent_worktrees import native_restart_receipt

    assert invoke(subject, capfd)[0] == 0
    path = native_restart_receipt._path(subject.path)
    if damage == "symlink":
        target = path.with_name("other.json")
        path.rename(target)
        path.symlink_to(target)
    elif damage == "exposed":
        path.chmod(0o644)
    elif damage == "duplicate":
        path.write_text(path.read_text().replace('"state":', '"state": "launch-uncertain", "state":'))
    else:
        path.write_text("{" if damage == "corrupt" else "x" * 32769)
    before = path.read_bytes()
    assert invoke(subject, capfd)[0] != 0
    assert path.read_bytes() == before
    assert len(subject.launches) == 1


_CHILD = r"""
import json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
from agent_worktrees import __main__ as core, handoff_cli as cli, native_restart_check
from agent_worktrees import native_source_context, sessions, tracking
request, raw_path, mode = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3]
parser = core.build_parser()
path = Path(raw_path)
repo = SimpleNamespace(worktree_root=str(path.parent), launch={}, setup_hook={},
    env_script={}, copilot_path={}, anchor=request["expected"]["worktree_path"])
config = SimpleNamespace(default_repo=repo, profile_assignment=None)
cli.cfg.tracking_dir = lambda: path.parent
cli.cfg.load_config = lambda: config
core._repo_for_record = lambda *_: repo
core._preflight_launch = lambda *_: core.LaunchPreflight()
core._build_launch_cmd = lambda *_, **kw: ["copilot"]
core._build_env = lambda *_, **kw: {}
core._repo_session_env = lambda *_: {}
native_source_context.epoch = lambda: request["expected"]["target_epoch"]
native_restart_check.session_liveness = lambda _: "dead"
sessions.session_has_conversation_data = lambda _: True
sessions._list_mux_sessions = lambda: {}
def spawn(*args):
    with open(path.with_suffix(".launches"), "a") as stream:
        stream.write("launch\n")
        stream.flush()
        os.fsync(stream.fileno())
    if mode == "crash":
        os._exit(42)
    time.sleep(0.15)
    return {"ok": True}
sessions.mux_new_session = spawn
if mode == "reservation-crash":
    from agent_worktrees import native_restart_receipt
    real_write = native_restart_receipt.write
    def crash_after_reservation(*args, **kwargs):
        real_write(*args, **kwargs)
        if kwargs["reserve"]:
            os._exit(41)
    native_restart_receipt.write = crash_after_reservation
if mode.startswith("ack"):
    from agent_worktrees import native_hosting_evidence as hosting, native_restart_receipt
    cli.cfg.detect_platform = lambda: "linux"
    hosting.session_liveness = lambda _: "live"
    hosting.session_process_owns_hold = lambda *a: True
    sessions.mux_binding_for_session = lambda *a, **k: {
        "session_name": sessions.mux_session_name(request["expected"]["worktree_id"]),
        "pane_id": "%1", "pane_start_time": "455", "session_incarnation": "$1:100",
        "copilot_pid": 99, "copilot_start_time": "456"}
    native_source_context.capture = lambda: {
        "version": 1, "qualified": True, **request["expected"]["target_epoch"],
        "ancestors": [{"pid": 99, "start_time": "456"}]}
    if mode == "ack-crash":
        real_write = native_restart_receipt.write
        def crash(*args, **kwargs):
            real_write(*args, **kwargs)
            os._exit(42)
        native_restart_receipt.write = crash
    intent = ["--restart-ack", request["request_id"], "--status-reconciled"]
else:
    intent = ["--restart-request", json.dumps(request)]
args = parser.parse_args(["embody", "--worktree-id",
    request["expected"]["worktree_id"], *intent, "--json"])
sys.exit(cli.cmd_embody(args))
"""


@pytest.mark.parametrize("self_claim", [False, True])
def test_two_actual_cli_processes_share_one_durable_launch(subject, self_claim):
    if self_claim:
        subject.record.resources = [tracking.ResourceClaim(
            kind="session", ref=tracking.format_claim_ref(
                subject.record.machine, subject.record.repo, subject.record.worktree_id,
                session=NATIVE,
            ),
        )]
        tracking.save_record(subject.record, subject.path)
    argv = [sys.executable, "-c", _CHILD, json.dumps(subject.request), str(subject.path), "normal"]
    first = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        results = [(process, process.communicate(timeout=20)) for process in (first, second)]
        assert any(process.returncode == 0 for process, _ in results), results
        assert subject.path.with_suffix(".launches").read_text() == "launch\n"
        for process, (stdout, _) in results:
            if process.returncode == 0:
                assert json.loads(stdout)["state"] == "mux-created"
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("self_claim", [False, True])
def test_actual_owner_crash_after_native_call_is_never_retried(subject, self_claim):
    if self_claim:
        subject.record.resources = [tracking.ResourceClaim(
            kind="session", ref=tracking.format_claim_ref(
                subject.record.machine, subject.record.repo, subject.record.worktree_id,
                session=NATIVE,
            ),
        )]
        tracking.save_record(subject.record, subject.path)
    argv = [sys.executable, "-c", _CHILD, json.dumps(subject.request), str(subject.path)]
    crashed = subprocess.run([*argv, "crash"], capture_output=True, text=True, timeout=20)
    assert crashed.returncode == 42, crashed.stderr
    repeated = subprocess.run([*argv, "normal"], capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 4, repeated.stderr
    assert json.loads(repeated.stdout)["state"] == "launch-uncertain"
    assert subject.path.with_suffix(".launches").read_text() == "launch\n"


@pytest.fixture
def resumed(subject, capfd, monkeypatch):
    from agent_worktrees import native_hosting_evidence
    from agent_worktrees.tracking_session_registry import _start_session_activation

    assert invoke(subject, capfd)[0] == 0
    record = tracking.load_record(subject.path)
    source = native_source_context.identity(NATIVE, 99, "456", TARGET)
    assert _start_session_activation(
        record.sessions[0], event_at="resumed", recorded_at="resumed",
        source="hook", source_identity=source,
    )
    record.lifecycle_revision += 1
    record.resources = [tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=NATIVE,
        ),
    )]
    tracking.save_record(record, subject.path)
    monkeypatch.setattr(handoff_cli.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(native_hosting_evidence, "session_liveness", lambda _: "live")
    monkeypatch.setattr(native_hosting_evidence, "session_process_owns_hold", lambda *a: True)
    monkeypatch.setattr(sessions, "mux_binding_for_session", lambda *a, **k: {
        "session_name": sessions.mux_session_name(record.worktree_id),
        "pane_id": "%1", "pane_start_time": "455", "session_incarnation": "$1:100",
        "copilot_pid": 99, "copilot_start_time": "456",
    })
    monkeypatch.setattr(native_source_context, "capture", lambda: {
        "version": 1, "qualified": True, **TARGET,
        "ancestors": [{"pid": 99, "start_time": "456"}],
    })
    return subject


def acknowledge(subject, capfd, *extra):
    args = core.build_parser().parse_args([
        "embody", "--worktree-id", subject.record.worktree_id,
        "--restart-ack", subject.request["request_id"], "--status-reconciled", "--json", *extra,
    ])
    rc = handoff_cli.cmd_embody(args)
    return rc, json.loads(capfd.readouterr().out)


def test_acknowledgement_is_explicit_durable_and_bound_to_resumed_process(resumed, capfd):
    from agent_worktrees import native_restart_receipt

    assert native_restart_receipt.read(resumed.path)["seed_acknowledged"] is False
    rc, result = acknowledge(resumed, capfd)
    assert rc == 0 and result["state"] == "seed-acknowledged"
    assert result["version"] == 2 and result["seed_acknowledged"] is True
    assert result["restored"] is False and result["automatic_eligible"] is False
    ack = result["acknowledgement"]
    assert ack["native_session_id"] == NATIVE and ack["native_incarnation"] == "99:456"
    assert ack["session_incarnation"] == "$1:100"
    assert ack["target_epoch"] == TARGET and ack["activation_ordinal"] == 3
    assert ack["status_reconciled"] is True
    assert native_restart_receipt.read(resumed.path)["acknowledgement"] == ack
    before = native_restart_receipt._path(resumed.path).read_bytes()
    assert acknowledge(resumed, capfd)[0] == 0
    assert native_restart_receipt._path(resumed.path).read_bytes() == before
    assert invoke(resumed, capfd, "--restart-receipt")[1]["seed_acknowledged"] is True
    assert len(resumed.launches) == 1


@pytest.mark.parametrize("damage", [
    "wrong-ancestry", "wrong-boot", "wrong-head", "head-revision", "wrong-source",
    "old-source", "pruned-source", "ended", "paused", "task", "owner-ref",
    "pending-seed", "opaque-claim", "wrong-worktree-incarnation", "no-hold", "intentional-end",
])
def test_ack_refuses_unproven_or_changed_native_owner(resumed, capfd, monkeypatch, damage):
    from agent_worktrees import native_hosting_evidence, native_restart_receipt

    before = native_restart_receipt._path(resumed.path).read_bytes()
    record = tracking.load_record(resumed.path)
    if damage == "wrong-ancestry":
        monkeypatch.setattr(native_source_context, "capture", lambda: {
            "version": 1, "qualified": True, **TARGET,
            "ancestors": [{"pid": 100, "start_time": "456"}],
        })
    elif damage == "wrong-boot":
        monkeypatch.setattr(native_source_context, "epoch", lambda: SOURCE)
    elif damage == "wrong-head":
        other = str(uuid4())
        record.sessions.append(tracking.SessionEntry(other, "new"))
        tracking._append_head_transition(record, other, reason="bind", at="new")
    elif damage == "head-revision":
        record.head_revision += 1
    elif damage == "wrong-source":
        record.sessions[0].activations[-1].source_identity = "native-source-v1:" + "a" * 64
    elif damage == "old-source":
        record.sessions[0].activations[0].source_identity = "native-source-v1:" + "a" * 64
    elif damage == "pruned-source":
        record.sessions[0].pruned_sources = [resumed.request["expected"]["source_identity"]]
    elif damage == "ended":
        record.sessions[0].activations[-1].ended_at = "ended"
    elif damage == "intentional-end":
        record.sessions[0].activations[0].end_source = "hook"
        record.sessions[0].activations[0].end_reason = "intentional-exit"
    elif damage == "paused":
        record.paused = True
    elif damage == "task":
        record.resources.append(tracking.ResourceClaim(kind="task", ref="dispatch-local-task:one"))
    elif damage == "owner-ref":
        record.owner_ref = "unknown"
    elif damage == "pending-seed":
        from agent_worktrees import launch_seed_state

        monkeypatch.setattr(launch_seed_state, "peek", lambda *a, **k: object())
    elif damage == "opaque-claim":
        record.resources[0].metadata_opaque = True
    elif damage == "wrong-worktree-incarnation":
        record.creation_nonce = "fedcba9876543210"
    else:
        monkeypatch.setattr(native_hosting_evidence, "session_process_owns_hold", lambda *a: False)
    tracking.save_record(record, resumed.path)
    rc, result = acknowledge(resumed, capfd)
    assert rc != 0 and result["error"]
    assert native_restart_receipt._path(resumed.path).read_bytes() == before
    assert len(resumed.launches) == 1


def test_uncertain_launch_can_only_be_reconciled_by_actual_native_ack(resumed, capfd):
    from agent_worktrees import native_restart_receipt

    path = native_restart_receipt._path(resumed.path)
    path.write_text(path.read_text().replace('"mux-created"', '"launch-uncertain"'))
    assert acknowledge(resumed, capfd)[0] == 0
    assert native_restart_receipt.read(resumed.path)["state"] == "seed-acknowledged"


def test_ack_write_failure_preserves_unacknowledged_outcome(resumed, capfd, monkeypatch):
    from agent_worktrees import native_restart_receipt

    def denied(*args, **kwargs):
        raise OSError("denied acknowledgement write")

    monkeypatch.setattr(native_restart_receipt, "write", denied)
    assert acknowledge(resumed, capfd)[0] != 0
    assert native_restart_receipt.read(resumed.path)["seed_acknowledged"] is False


def test_ack_requires_status_reconciliation_and_cannot_be_dry_run(resumed, capfd):
    args = core.build_parser().parse_args([
        "embody", "--worktree-id", resumed.record.worktree_id,
        "--restart-ack", resumed.request["request_id"], "--json",
    ])
    assert handoff_cli.cmd_embody(args) != 0
    capfd.readouterr()
    assert acknowledge(resumed, capfd, "--dry-run")[0] != 0


@pytest.fixture
def later_boot(resumed, capfd, monkeypatch):
    rc, receipt = acknowledge(resumed, capfd)
    assert rc == 0
    previous = resumed.request
    ack = receipt["acknowledgement"]
    target = {**TARGET, "boot_id": "boot-third"}
    resumed.request = {
        "request_id": str(uuid4()), "source_epoch": TARGET,
        "native_incarnation": ack["native_incarnation"],
        "expected": {
            **previous["expected"], "target_epoch": target,
            **{key: ack[key] for key in (
                "source_identity", "owner_revision", "activation_ordinal",
            )},
        },
    }
    monkeypatch.setattr(native_source_context, "epoch", lambda: dict(target))
    return resumed, previous, {key: value for key, value in receipt.items() if key != "ok"}


def test_later_boot_retains_acknowledged_history_without_reusing_launch(later_boot, capfd):
    resumed, previous, acknowledged = later_boot
    current_request = resumed.request
    rc, result = invoke(resumed, capfd)
    assert rc == 0, result
    assert result["request"] == current_request and result["state"] == "mux-created"
    assert len(resumed.launches) == 2
    assert invoke(resumed, capfd)[0] == 0
    assert len(resumed.launches) == 2
    resumed.request = previous
    rc, historical = invoke(resumed, capfd, "--restart-receipt")
    assert rc == 0 and historical == acknowledged
    assert historical["restored"] is False and historical["automatic_eligible"] is False
    assert len(resumed.launches) == 2


@pytest.mark.parametrize("state", ["launch-uncertain", "mux-created"])
def test_later_boot_never_supersedes_an_unacknowledged_launch(later_boot, capfd, state):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    unresolved = {
        key: value for key, value in acknowledged.items() if key != "acknowledgement"
    }
    unresolved.update(version=1, state=state, seed_acknowledged=False)
    path = native_restart_receipt._path(resumed.path)
    path.write_text(json.dumps(unresolved))
    before = path.read_bytes()
    rc, result = invoke(resumed, capfd)
    assert rc != 0 and "unreconciled-restart" in result["error"]
    assert path.read_bytes() == before
    assert len(resumed.launches) == 1


@pytest.mark.parametrize("change", [
    "head", "source", "source-boot", "activation", "owner", "original", "cycled-boot",
])
def test_later_boot_requires_continuity_with_the_acknowledged_execution(later_boot, capfd, monkeypatch, change):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    expected = resumed.request["expected"]
    if change == "head":
        expected["head_revision"] += 1
    elif change == "activation":
        expected["activation_ordinal"] += 1
    elif change == "owner":
        expected["owner_revision"] -= 1
    elif change == "cycled-boot":
        expected["target_epoch"] = SOURCE
        monkeypatch.setattr(native_source_context, "epoch", lambda: dict(SOURCE))
    else:
        if change == "source":
            resumed.request["native_incarnation"] = "100:789"
        elif change == "source-boot":
            resumed.request["source_epoch"] = {**TARGET, "boot_id": "skipped-boot"}
        else:
            expected["native_session_id"] = str(uuid4())
        pid, birth = resumed.request["native_incarnation"].split(":")
        expected["source_identity"] = native_source_context.identity(
            expected["native_session_id"], int(pid), birth, resumed.request["source_epoch"],
        )
    path = native_restart_receipt._path(resumed.path)
    before = path.read_bytes()
    rc, result = invoke(resumed, capfd)
    assert rc != 0 and result["error"]
    assert path.read_bytes() == before
    assert len(resumed.launches) == 1


def test_later_boot_native_ack_preserves_both_original_execution_outcomes(later_boot, capfd, monkeypatch):
    from agent_worktrees import native_restart_receipt
    from agent_worktrees.tracking_session_registry import _start_session_activation

    resumed, previous, acknowledged = later_boot
    assert invoke(resumed, capfd)[0] == 0
    target = resumed.request["expected"]["target_epoch"]
    source = native_source_context.identity(NATIVE, 100, "789", target)
    record = tracking.load_record(resumed.path)
    assert _start_session_activation(
        record.sessions[0], event_at="third", recorded_at="third",
        source="hook", source_identity=source,
    )
    record.lifecycle_revision += 1
    tracking.save_record(record, resumed.path)
    monkeypatch.setattr(sessions, "mux_binding_for_session", lambda *a, **k: {
        "session_name": sessions.mux_session_name(record.worktree_id),
        "pane_id": "%2", "pane_start_time": "788", "session_incarnation": "$2:200",
        "copilot_pid": 100, "copilot_start_time": "789",
    })
    monkeypatch.setattr(native_source_context, "capture", lambda: {
        "version": 1, "qualified": True, **target,
        "ancestors": [{"pid": 100, "start_time": "789"}],
    })
    rc, result = acknowledge(resumed, capfd)
    assert rc == 0 and result["state"] == "seed-acknowledged", result
    assert result["acknowledgement"]["source_identity"] == source
    assert native_restart_receipt.find(resumed.path, previous) == acknowledged
    before = native_restart_receipt._path(resumed.path).read_bytes()
    assert acknowledge(resumed, capfd)[0] == 0
    assert native_restart_receipt._path(resumed.path).read_bytes() == before
    assert len(resumed.launches) == 2


def _next_receipts(previous, ordinal):
    ack = previous["acknowledgement"]
    source_epoch = previous["request"]["expected"]["target_epoch"]
    target = {**source_epoch, "boot_id": f"history-{ordinal}"}
    request = {
        "request_id": str(uuid4()), "source_epoch": source_epoch,
        "native_incarnation": ack["native_incarnation"],
        "expected": {
            **previous["request"]["expected"], "target_epoch": target,
            **{key: ack[key] for key in (
                "source_identity", "owner_revision", "activation_ordinal",
            )},
        },
    }
    reserved = {
        "schema": "native-restart-launch", "version": 1, "request": request,
        "state": "launch-uncertain", "seed_acknowledged": False,
        "restored": False, "automatic_eligible": False,
    }
    acknowledged = {
        **reserved, "version": 2, "state": "seed-acknowledged", "seed_acknowledged": True,
        "acknowledgement": {
            **ack, "target_epoch": target,
            "source_identity": native_source_context.identity(NATIVE, 100 + ordinal, "456", target),
            "native_incarnation": f"{100 + ordinal}:456",
            "owner_revision": ack["owner_revision"] + 1,
            "activation_ordinal": ack["activation_ordinal"] + 1,
        },
    }
    return reserved, acknowledged


def test_history_count_capacity_preserves_every_acknowledged_request(later_boot):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    expected = [acknowledged]
    for ordinal in range(2, 17):
        reserved, acknowledged = _next_receipts(acknowledged, ordinal)
        native_restart_receipt.write(resumed.path, reserved, reserve=True)
        native_restart_receipt.write(resumed.path, acknowledged, reserve=False)
        expected.append(acknowledged)
    path = native_restart_receipt._path(resumed.path)
    before = path.read_bytes()
    assert len(before) <= 32768
    assert native_restart_receipt._MAX_RECEIPTS == 16
    reserved, _ = _next_receipts(acknowledged, 17)
    with pytest.raises(ValueError, match="capacity"):
        native_restart_receipt.write(resumed.path, reserved, reserve=True)
    assert path.read_bytes() == before
    for receipt in expected:
        assert native_restart_receipt.find(resumed.path, receipt["request"]) == receipt


def test_history_aggregate_byte_capacity_refuses_without_evicting(later_boot):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    acknowledged = deepcopy(acknowledged)
    acknowledged["request"]["expected"]["project"] = "p" * 4096
    path = native_restart_receipt._path(resumed.path)
    path.write_text(json.dumps(acknowledged))
    receipts = [acknowledged]
    for ordinal in range(2, 17):
        reserved, acknowledged = _next_receipts(acknowledged, ordinal)
        maximum = deepcopy(acknowledged)
        maximum["acknowledgement"].update(
            native_incarnation="9999999999:" + "9" * 32, session_incarnation="x" * 128,
            source_identity=native_source_context.identity(
                NATIVE, 9999999999, "9" * 32, reserved["request"]["expected"]["target_epoch"],
            ),
            owner_revision=tracking.MAX_PERSISTED_COUNTER,
            activation_ordinal=tracking.MAX_PERSISTED_COUNTER,
        )
        native_restart_receipt.validate_acknowledgement(
            reserved["request"], maximum["acknowledgement"],
        )
        candidate = {
            "schema": "native-restart-launch-history", "version": 1,
            "receipts": [*receipts, maximum],
        }
        size = len((json.dumps(candidate, sort_keys=True) + "\n").encode("utf-8"))
        if size > 32768:
            before = path.read_bytes()
            assert len(before) <= 32768
            with pytest.raises(ValueError, match="exceeds its budget"):
                native_restart_receipt.write(resumed.path, reserved, reserve=True)
            assert path.read_bytes() == before
            return
        native_restart_receipt.write(resumed.path, reserved, reserve=True)
        native_restart_receipt.write(resumed.path, acknowledged, reserve=False)
        receipts.append(acknowledged)
    pytest.fail("The aggregate byte limit was not exercised before the count limit")


def test_history_reservation_requires_room_for_acknowledgement(later_boot):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    receipts = [deepcopy(acknowledged)]
    receipts[0]["request"]["expected"]["project"] = "p"
    for ordinal in range(2, 7):
        _, acknowledged = _next_receipts(receipts[-1], ordinal)
        receipts.append(acknowledged)
    reserved, _ = _next_receipts(receipts[-1], 7)
    candidate = {
        "schema": "native-restart-launch-history", "version": 1,
        "receipts": [*receipts, reserved],
    }
    overhead = len((json.dumps(candidate, sort_keys=True) + "\n").encode("utf-8"))
    padding = 1 + (32768 - overhead) // 7
    assert 1 <= padding <= 4096
    for receipt in candidate["receipts"]:
        receipt["request"]["expected"]["project"] = "p" * padding
    assert len((json.dumps(candidate, sort_keys=True) + "\n").encode("utf-8")) <= 32768
    path = native_restart_receipt._path(resumed.path)
    path.write_text(json.dumps({**candidate, "receipts": receipts}, sort_keys=True) + "\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="acknowledgement.*budget"):
        native_restart_receipt.write(resumed.path, reserved, reserve=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["owner_revision", "activation_ordinal"])
def test_reservation_refuses_exhausted_acknowledgement_counters(subject, capfd, field):
    from agent_worktrees import native_restart_receipt

    record = tracking.load_record(subject.path)
    subject.request["expected"][field] = tracking.MAX_PERSISTED_COUNTER
    if field == "owner_revision":
        record.lifecycle_revision = tracking.MAX_PERSISTED_COUNTER
        tracking.save_record(record, subject.path)
    else:
        import yaml

        raw = yaml.safe_load(subject.path.read_text())
        raw["sessions"][0]["activations"][0]["ordinal"] = tracking.MAX_PERSISTED_COUNTER
        subject.path.write_text(yaml.safe_dump(raw))
    record_cache.clear()
    rc, result = invoke(subject, capfd)
    assert rc != 0 and "acknowledgement counter capacity" in result["error"]
    assert subject.launches == []
    assert native_restart_receipt.read(subject.path) is None


@pytest.mark.parametrize("mode", ["normal", "crash", "reservation-crash"])
def test_actual_owner_processes_preserve_history_and_new_attempt_uncertainty(later_boot, mode):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    argv = [sys.executable, "-c", _CHILD, json.dumps(resumed.request), str(resumed.path)]
    if mode == "normal":
        children = [
            subprocess.Popen([*argv, mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        try:
            results = [(child, child.communicate(timeout=20)) for child in children]
            assert any(child.returncode == 0 for child, _ in results), results
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
    else:
        crashed = subprocess.run([*argv, mode], capture_output=True, text=True, timeout=20)
        assert crashed.returncode == (42 if mode == "crash" else 41), crashed.stderr
        repeated = subprocess.run([*argv, "normal"], capture_output=True, text=True, timeout=20)
        assert repeated.returncode == 4, repeated.stderr
        assert json.loads(repeated.stdout)["state"] == "launch-uncertain"
    launches = resumed.path.with_suffix(".launches")
    if mode == "reservation-crash":
        assert not launches.exists()
    else:
        assert launches.read_text() == "launch\n"
    assert native_restart_receipt.find(resumed.path, previous) == acknowledged


@pytest.mark.parametrize("damage", ["duplicate", "reordered", "broken-link", "unknown-version"])
def test_corrupt_history_is_never_partially_read_or_replaced(later_boot, capfd, damage):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    assert invoke(resumed, capfd)[0] == 0
    path = native_restart_receipt._path(resumed.path)
    value = json.loads(path.read_text())
    if damage == "duplicate":
        value["receipts"][1] = deepcopy(value["receipts"][0])
    elif damage == "reordered":
        value["receipts"].reverse()
    elif damage == "broken-link":
        value["receipts"][1]["request"]["expected"]["owner_revision"] = 0
    else:
        value["version"] = True
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    resumed.request = previous
    assert invoke(resumed, capfd, "--restart-receipt")[0] != 0
    assert path.read_bytes() == before
    assert len(resumed.launches) == 2


@pytest.mark.parametrize("phase", ["reservation", "outcome"])
def test_history_publication_failure_retains_prior_ack_and_new_uncertainty(later_boot, capfd, monkeypatch, phase):
    from agent_worktrees import native_restart_receipt

    resumed, previous, acknowledged = later_boot
    original = os.replace
    calls = []

    def replace_receipt(*args):
        calls.append(True)
        if len(calls) == (1 if phase == "reservation" else 2):
            raise OSError("simulated failed history publication")
        return original(*args)

    monkeypatch.setattr(native_restart_receipt.os, "replace", replace_receipt)
    assert invoke(resumed, capfd)[0] != 0
    assert native_restart_receipt.find(resumed.path, previous) == acknowledged
    if phase == "reservation":
        assert native_restart_receipt.find(resumed.path, resumed.request) is None
        assert len(resumed.launches) == 1
    else:
        assert invoke(resumed, capfd)[1]["state"] == "launch-uncertain"
        assert len(resumed.launches) == 2


@pytest.mark.parametrize("incarnation", ["$1:100\n", "$1:100;extra"])
def test_invalid_mux_incarnation_cannot_be_loaded_as_acknowledged(resumed, capfd, incarnation):
    from agent_worktrees import native_restart_receipt

    assert acknowledge(resumed, capfd)[0] == 0
    path = native_restart_receipt._path(resumed.path)
    receipt = json.loads(path.read_text())
    receipt["acknowledgement"]["session_incarnation"] = incarnation
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()
    rc, result = invoke(resumed, capfd, "--restart-receipt")
    assert rc != 0 and "Invalid restart seed acknowledgement" in result["error"]
    assert path.read_bytes() == before and len(resumed.launches) == 1


def test_actual_ack_crash_after_commit_is_idempotent_without_native_relaunch(resumed):
    argv = [sys.executable, "-c", _CHILD, json.dumps(resumed.request), str(resumed.path)]
    crashed = subprocess.run([*argv, "ack-crash"], capture_output=True, text=True, timeout=20)
    assert crashed.returncode == 42, crashed.stderr
    repeated = subprocess.run([*argv, "ack"], capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["seed_acknowledged"] is True
    assert len(resumed.launches) == 1
    assert not resumed.path.with_suffix(".launches").exists()


def test_owner_changed_during_hosting_probe_cannot_ack(resumed, capfd, monkeypatch):
    from agent_worktrees import native_hosting_evidence, native_restart_receipt

    original = native_hosting_evidence.observe

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        record = tracking.load_record(resumed.path)
        record.paused = True
        tracking.save_record(record, resumed.path)
        return result

    monkeypatch.setattr(native_hosting_evidence, "observe", changed)
    assert acknowledge(resumed, capfd)[0] != 0
    assert native_restart_receipt.read(resumed.path)["seed_acknowledged"] is False


@pytest.mark.parametrize("flag", ["--restart-request=", "--restart-ack="])
def test_empty_restart_intent_never_falls_through_to_ordinary_launch(subject, capfd, flag):
    args = core.build_parser().parse_args([
        "embody", "--worktree-id", subject.record.worktree_id, flag, "--json",
    ])
    assert handoff_cli.cmd_embody(args) != 0
    assert json.loads(capfd.readouterr().out)["error"]
    assert subject.launches == []
