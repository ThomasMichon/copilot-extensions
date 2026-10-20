"""Restart Resume must cross the real embody fence without replacing history."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import handoff_cli, launch_admission, native_restart_check
from agent_worktrees import native_source_context, sessions, tracking


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


def test_two_actual_cli_processes_share_one_durable_launch(subject):
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


def test_actual_owner_crash_after_native_call_is_never_retried(subject):
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
