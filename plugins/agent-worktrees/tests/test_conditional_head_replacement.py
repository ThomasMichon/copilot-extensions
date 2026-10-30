"""Explicit dead-head promotion must never change normal registration."""

from dataclasses import asdict, replace
import argparse
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import (
    config as cfg,
    config_authority,
    native_lifecycle_source,
    native_source_context,
    sessions,
    conditional_head_replacement_cli as replacement_cli,
    tracking,
)
from agent_worktrees import conditional_head_replacement as replacement


_START_IDENTITY = native_lifecycle_source.start_identity
_CANDIDATE_CWD = replacement._candidate_cwd
OLD = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
EPOCH = {"platform": "linux", "environment_id": "1" * 32, "boot_id": "boot"}
SOURCE = native_source_context.identity(NEW, 123, "456", EPOCH)
CONTRACT = "agent-worktrees/conditional-head-replacement"


def replacement_args(*args):
    parser = argparse.ArgumentParser()
    replacement_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["replace-dead-head", *args])


def request_json(request):
    return json.dumps({"contract": CONTRACT, "version": 1, **asdict(request)})


def test_full_parser_keeps_replacement_separate_from_binding():
    from agent_worktrees.__main__ import build_parser

    parser = build_parser()
    args = parser.parse_args(["replace-dead-head", "apply", "--permit", "--stdin"])
    assert args.replacement_action == "apply" and args.permit and args.stdin
    ordinary = parser.parse_args(["bind-session", "--worktree-id", "target", "--session-id", NEW])
    assert ordinary.session_id == NEW and not hasattr(ordinary, "permit")
    with pytest.raises(SystemExit) as refused:
        parser.parse_args(["bind-session", "--permit"])
    assert refused.value.code == 2


@pytest.fixture
def target(tmp_tracking_dir, monkeypatch, monkeypatch_config):
    root = tmp_tracking_dir / "trees"
    checkout = root / "target"
    checkout.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_tracking_dir), machine="test", platform=cfg.detect_platform(),
        repo_name="test-repo", repos={"test-repo": cfg.RepoConfig(
            anchor=str(tmp_tracking_dir), worktree_root=str(root),
        )},
    )
    monkeypatch.setattr(config_authority, "load_project_authority_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config)
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _id: tmp_tracking_dir)
    path = tmp_tracking_dir / "target.yaml"
    record = tracking.WorktreeRecord(
        worktree_id="target", branch="worktree/target", worktree_path=str(checkout),
        repo="test-repo", machine="test", platform=config.platform,
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        creation_nonce="creation-one",
        sessions=[tracking.SessionEntry(OLD, "2026-01-01T00:00:00")],
    )
    tracking._append_head_transition(record, OLD, reason="register")
    tracking.save_record(record, path)
    # Use the normal registration transaction to establish a non-head session.
    from agent_worktrees.tracking_session_registration_write import apply_session_register

    assert apply_session_register({
        "worktree_id": "target", "yaml_path": str(path), "session_id": NEW,
        "source": "bind", "source_identity": SOURCE, "pid": 123,
    })["ok"]
    assert tracking.load_record(path).resolved_head_session == OLD
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    monkeypatch.setattr(replacement, "_candidate_cwd", lambda _id: checkout)
    monkeypatch.setattr(native_lifecycle_source, "start_identity", lambda *_a: SOURCE)
    monkeypatch.setattr(replacement, "session_liveness", lambda sid: "live" if sid == NEW else "dead")
    request = replacement.HeadReplacement(
        worktree_id="target", worktree_path=str(checkout), creation_nonce="creation-one",
        expected_head=OLD, expected_head_revision=1,
        expected_lifecycle_revision=tracking.load_record(path).lifecycle_revision,
        replacement_session=NEW, replacement_source=SOURCE, native_source={"test": True},
    )
    return SimpleNamespace(path=path, checkout=checkout, request=request)


def test_promotes_exact_registered_replacement_preserving_history_and_claims(target):
    before = tracking.load_record(target.path)
    result = replacement.replace_dead_head(target.request)
    after = tracking.load_record(target.path)
    assert result.previous_head == OLD
    assert result.head_session == NEW
    assert result.head_revision > before.lifecycle_revision
    assert after.resolved_head_session == NEW
    assert after.head_transitions[:-1] == before.head_transitions
    assert after.head_transitions[-1].reason == "authorized-dead-head-replacement"
    assert after.resources == before.resources
    assert after.handoffs == before.handoffs
    for old, new in zip(before.sessions, after.sessions):
        assert asdict(old) | {"relation_revision": new.relation_revision} == asdict(new)


@pytest.mark.parametrize("field,value", [
    ("expected_head", OTHER), ("expected_head_revision", 0),
    ("expected_head_revision", True), ("creation_nonce", "wrong"),
    ("expected_lifecycle_revision", 1),
    ("replacement_source", "native-source-v1:" + "b" * 64),
    ("replacement_session", "other:namespace"), ("replacement_session", OLD),
    ("worktree_id", "../target"), ("worktree_path", "relative"),
])
def test_request_mismatch_has_no_write(target, field, value):
    before = target.path.read_bytes()
    with pytest.raises((ValueError, OSError)):
        replacement.replace_dead_head(replace(target.request, **{field: value}))
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("state", ["live", "unknown"])
def test_predecessor_must_be_provably_dead(target, monkeypatch, state):
    monkeypatch.setattr(replacement, "session_liveness", lambda _id: state)
    before = target.path.read_bytes()
    with pytest.raises(ValueError, match="dead"):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("mutation", [
    "pending", "finalizing", "completed", "reaped", "opaque", "holder",
    "activation-ended", "activation-ambiguous", "source-saturated",
    "head-cache", "history-capacity", "already-head",
])
def test_record_refusal_has_no_write(target, mutation):
    record = tracking.load_record(target.path)
    if mutation == "pending":
        tracking.open_handoff(record, OLD, "pending-token", save=False)
    elif mutation in {"finalizing", "completed"}:
        record.status = mutation
    elif mutation == "reaped":
        record.reaped_at = "2026-01-02T00:00:00"
    elif mutation == "opaque":
        record.execution_leg_opaque = True
        record.execution_leg_raw = {"version": 999}
    elif mutation == "holder":
        record.sessions.append(tracking.SessionEntry(OTHER, "2026-01-01T00:00:00"))
    elif mutation == "activation-ended":
        record.session_entry(NEW).activations[-1].ended_at = "2026-01-02T00:00:00"
    elif mutation == "activation-ambiguous":
        record.session_entry(NEW).activations[-1].source_ambiguous = True
    elif mutation == "source-saturated":
        record.session_entry(NEW).source_history_saturated = True
    elif mutation == "head-cache":
        record.head_session = NEW
    elif mutation == "history-capacity":
        record.head_transitions *= tracking._MAX_HEAD_TRANSITIONS
    elif mutation == "already-head":
        record.head_transitions.insert(0, tracking.HeadTransition(
            1, NEW, "old-head", "2025-01-01T00:00:00",
        ))
    tracking.save_record(record, target.path)
    if mutation == "holder":
        # A third session with unavailable native state is a competing holder.
        from unittest.mock import patch
        with patch.object(
            replacement, "session_liveness",
            side_effect=lambda sid: "unknown" if sid == OTHER else "dead",
        ):
            before = target.path.read_bytes()
            with pytest.raises(ValueError):
                replacement.replace_dead_head(target.request)
    else:
        before = target.path.read_bytes()
        with pytest.raises(ValueError):
            replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("boundary", ["mux", "unknown-mux", "native-source", "native-cwd"])
def test_missing_native_proof_has_no_write(target, monkeypatch, boundary):
    if boundary == "mux":
        monkeypatch.setattr(
            sessions, "_list_mux_sessions",
            lambda: {sessions.mux_session_name(target.request.worktree_id): 0},
        )
    elif boundary == "unknown-mux":
        monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: None)
    elif boundary == "native-source":
        monkeypatch.setattr(native_lifecycle_source, "start_identity", lambda *_a: None)
    else:
        monkeypatch.setattr(replacement, "_candidate_cwd", lambda _id: target.checkout.parent)
    before = target.path.read_bytes()
    with pytest.raises(ValueError):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_replay_has_no_write(target):
    replacement.replace_dead_head(target.request)
    before = target.path.read_bytes()
    with pytest.raises(ValueError):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_cli_applies_exact_request_and_refuses_replay(target, monkeypatch, capfd):
    args = replacement_args("apply", "--permit", "--stdin")
    text = request_json(target.request)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert replacement_cli.cmd_replace_dead_head(args) == 0
    result = json.loads(capfd.readouterr().out)
    assert result == {
        "contract": CONTRACT, "version": 1, "replaced": True,
        "previous_head": OLD, "head_session": NEW,
        "head_revision": tracking.load_record(target.path).head_revision,
    }
    before = target.path.read_bytes()
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert replacement_cli.cmd_replace_dead_head(args) == 3
    assert "error" in json.loads(capfd.readouterr().out)
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("mutation", [
    "missing", "unknown", "version", "bool-version", "contract", "bool-revision",
    "null-path", "null-source", "duplicate", "too-large", "array", "malformed",
])
def test_cli_invalid_contract_never_calls_owner(target, monkeypatch, capfd, mutation):
    def forbidden(*_a, **_k):
        pytest.fail("Invalid request reached the replacement owner")

    data = json.loads(request_json(target.request))
    if mutation == "missing":
        del data["expected_head_revision"]
    elif mutation == "unknown":
        data["force"] = True
    elif mutation == "version":
        data["version"] = 2
    elif mutation == "bool-version":
        data["version"] = True
    elif mutation == "contract":
        data["contract"] = "different"
    elif mutation == "bool-revision":
        data["expected_head_revision"] = True
    elif mutation == "null-path":
        data["worktree_path"] = None
    elif mutation == "null-source":
        data["native_source"] = None
    text = json.dumps(data)
    if mutation == "duplicate":
        text = text[:-1] + ', "version": 1}'
    elif mutation == "too-large":
        text = " " * 65537 + text
    elif mutation == "array":
        text = "[]"
    elif mutation == "malformed":
        text = "{"
    monkeypatch.setattr(replacement, "replace_dead_head", forbidden)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    before = target.path.read_bytes()
    assert replacement_cli.cmd_replace_dead_head(
        replacement_args("apply", "--permit", "--stdin"),
    ) == 3
    assert "error" in json.loads(capfd.readouterr().out)
    assert target.path.read_bytes() == before


def test_cli_without_permit_refuses_before_read_or_owner(monkeypatch, capfd):
    def forbidden(*_a, **_k):
        pytest.fail("Unpermitted request read input or reached the owner")

    monkeypatch.setattr("sys.stdin", SimpleNamespace(read=forbidden))
    monkeypatch.setattr(replacement, "replace_dead_head", forbidden)
    assert replacement_cli.cmd_replace_dead_head(
        replacement_args("apply", "--stdin"),
    ) == 2
    assert "--permit" in json.loads(capfd.readouterr().out)["error"]


def test_cli_persistence_error_is_unconfirmed_not_success(target, monkeypatch, capfd):
    def fail(*_a, **_k):
        raise OSError("record persistence unavailable")

    monkeypatch.setattr(tracking, "save_record", fail)
    monkeypatch.setattr("sys.stdin", io.StringIO(request_json(target.request)))
    before = target.path.read_bytes()
    assert replacement_cli.cmd_replace_dead_head(
        replacement_args("apply", "--permit", "--stdin"),
    ) == 3
    result = json.loads(capfd.readouterr().out)
    assert "unconfirmed" in result["error"] and "replaced" not in result
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("argv,code", [
    (["capabilities"], 0),
    (["capabilities", "--permit"], 2),
    (["apply", "--stdin"], 2),
    (["apply", "--permit", "--stdin", "--force"], 2),
])
def test_replacement_real_entrypoint_contract(tmp_path, argv, code):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "replace-dead-head", *argv],
        cwd=tmp_path, capture_output=True, text=True, input="{}",
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == code, result.stderr + result.stdout
    if code == 0:
        assert json.loads(result.stdout) == {
            "version": 1, "contract": CONTRACT, "versions": [1],
            "candidate_observation": 1, "preflight": 1,
        }
    elif argv == ["apply", "--stdin"]:
        assert "--permit" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("field,value", [
    ("head_transitions", [{"revision": "unreadable"}]),
    ("handoffs", [{"future-handoff": True}]),
    ("sessions", [{"future-session": True}]),
])
def test_opaque_history_has_no_write(target, field, value):
    import yaml

    raw = yaml.safe_load(target.path.read_text())
    raw[field] = value
    target.path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = target.path.read_bytes()
    with pytest.raises(ValueError):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_rechecks_after_concurrent_writer_at_record_lock(target, monkeypatch):
    """A separate thread writes after discovery, before the required record lock."""
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor

    original = tracking._RecordLock
    changed = []

    @contextmanager
    def racing_lock(path, *args, **kwargs):
        if Path(path) == target.path and not changed:
            def writer():
                with original(target.path, require_sidecar=True):
                    record = tracking.load_record(target.path)
                    tracking._append_head_transition(record, NEW, reason="concurrent")
                    tracking.save_record(record, target.path)
                return target.path.read_bytes()
            # Prevent the competing writer's nested save from reentering this hook.
            changed.append(None)
            with ThreadPoolExecutor(max_workers=1) as pool:
                changed[0] = pool.submit(writer).result(timeout=5)
        with original(path, *args, **kwargs) as lock:
            yield lock

    monkeypatch.setattr(tracking, "_RecordLock", racing_lock)
    with pytest.raises(ValueError):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == changed[0]


def test_rechecks_after_separate_process_writes_head(target, monkeypatch):
    """A native child commits while this caller is between discovery and locking."""
    import os
    import subprocess
    import sys
    from contextlib import contextmanager

    original = tracking._RecordLock
    changed = []
    child = """
import sys
from pathlib import Path
from agent_worktrees import tracking
path = Path(sys.argv[1])
with tracking._RecordLock(path, require_sidecar=True):
    record = tracking.load_record(path)
    tracking._append_head_transition(record, sys.argv[2], reason="concurrent-process")
    tracking.save_record(record, path)
"""

    @contextmanager
    def racing_lock(path, *args, **kwargs):
        if Path(path) == target.path and not changed:
            result = subprocess.run(
                [sys.executable, "-c", child, str(target.path), NEW],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert result.returncode == 0, result.stderr
            changed.append(target.path.read_bytes())
        with original(path, *args, **kwargs) as lock:
            yield lock

    monkeypatch.setattr(tracking, "_RecordLock", racing_lock)
    with pytest.raises(ValueError):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == changed[0]


@pytest.mark.parametrize("proof", ["valid", "no-hold", "epoch-changed", "wrong-uuid"])
def test_native_source_is_corroborated_not_accepted_as_caller_text(target, monkeypatch, proof):
    from agent_worktrees import native_source_context

    epoch = EPOCH
    monkeypatch.setattr(native_source_context, "epoch", lambda: epoch)
    source = native_source_context.identity(NEW, 123, "456", epoch)
    witness = {
        "version": 1, "qualified": True, **epoch,
        "ancestors": [{"pid": 123, "start_time": "456"}],
    }
    if proof == "epoch-changed":
        witness["boot_id"] = "old-boot"
    elif proof == "wrong-uuid":
        source = native_source_context.identity(OTHER, 123, "456", epoch)
    if proof == "wrong-uuid":
        # Construct a falsely attributed original registration, without asking
        # the writer to rewrite an existing activation's immutable source.
        target.path.write_text(
            target.path.read_text(encoding="utf-8").replace(SOURCE, source), encoding="utf-8",
        )
    monkeypatch.setattr(native_lifecycle_source, "start_identity", _START_IDENTITY)
    holds = []

    def owns_hold(session_id, pid, birth):
        holds.append((session_id, pid, birth))
        return proof != "no-hold" and (session_id, pid, birth) == (NEW, 123, "456")

    monkeypatch.setattr(native_lifecycle_source, "session_process_owns_hold", owns_hold)
    request = replace(target.request, replacement_source=source, native_source=witness)
    before = target.path.read_bytes()
    if proof == "valid":
        assert replacement.replace_dead_head(request).head_session == NEW
    else:
        with pytest.raises(ValueError):
            replacement.replace_dead_head(request)
        assert target.path.read_bytes() == before
    if proof != "epoch-changed":
        assert holds == [(NEW, 123, "456")]


@pytest.mark.parametrize("started", [False, True])
def test_native_workspace_metadata_is_required(target, monkeypatch, tmp_path, started):
    """Read the native owner's actual workspace metadata, not supplied cwd text."""
    import yaml

    state = tmp_path / "native-state"
    native = state / NEW
    native.mkdir(parents=True)
    if started:
        (native / "events.jsonl").write_text("", encoding="utf-8")
    (native / "workspace.yaml").write_text(
        yaml.safe_dump({"cwd": str(target.checkout.parent)}), encoding="utf-8",
    )
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
    monkeypatch.setattr(replacement, "_candidate_cwd", _CANDIDATE_CWD)
    before = target.path.read_bytes()
    with pytest.raises(ValueError, match="workspace"):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before
    (native / "workspace.yaml").write_text(
        yaml.safe_dump({"cwd": str(target.checkout)}), encoding="utf-8",
    )
    assert (sessions.session_cwd(NEW) is not None) == started
    assert replacement.replace_dead_head(target.request).head_session == NEW


@pytest.mark.parametrize("invalid", [
    "missing", "malformed", "array", "null-cwd", "relative", "absent-directory", "detached",
])
def test_unstarted_candidate_requires_valid_workspace_metadata(target, monkeypatch, tmp_path, invalid):
    state = tmp_path / "native-state"
    native = state / NEW
    native.mkdir(parents=True)
    data = {"cwd": str(target.checkout)}
    if invalid == "null-cwd":
        data["cwd"] = None
    elif invalid == "relative":
        data["cwd"] = "."
    elif invalid == "absent-directory":
        data["cwd"] = str(tmp_path / "absent")
    if invalid != "missing":
        text = "[" if invalid == "malformed" else json.dumps([] if invalid == "array" else data)
        (native / "workspace.yaml").write_text(text, encoding="utf-8")
    if invalid == "detached":
        (native / ".detached").touch()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
    monkeypatch.setattr(replacement, "_candidate_cwd", _CANDIDATE_CWD)
    before = target.path.read_bytes()
    with pytest.raises((OSError, ValueError)):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("guard", ["lifecycle", "admission", "record"])
def test_lock_contention_has_no_write(target, monkeypatch, guard):
    from contextlib import contextmanager

    @contextmanager
    def busy(*_args, **_kwargs):
        raise TimeoutError("competing owner")
        yield

    if guard == "lifecycle":
        monkeypatch.setattr(replacement.worktree_lifecycle, "guard", busy)
    elif guard == "admission":
        monkeypatch.setattr(replacement.launch_admission, "guard", busy)
    else:
        original = tracking._RecordLock

        def conditional(path, *args, **kwargs):
            return busy() if Path(path) == target.path else original(path, *args, **kwargs)

        monkeypatch.setattr(tracking, "_RecordLock", conditional)
    before = target.path.read_bytes()
    with pytest.raises(TimeoutError):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_wrong_absolute_checkout_has_no_write(target):
    before = target.path.read_bytes()
    with pytest.raises(ValueError, match="path"):
        replacement.replace_dead_head(replace(target.request, worktree_path=str(target.checkout.parent)))
    assert target.path.read_bytes() == before


def test_aba_head_revision_is_not_reusable(target):
    record = tracking.load_record(target.path)
    tracking._append_head_transition(record, NEW, reason="competing-head")
    tracking._append_head_transition(record, OLD, reason="return-head")
    tracking.save_record(record, target.path)
    before = target.path.read_bytes()
    with pytest.raises(ValueError, match="revision"):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_stale_ordinary_save_cannot_undo_successful_transition(target):
    stale = tracking.load_record(target.path)
    result = replacement.replace_dead_head(target.request)
    tracking.save_record(stale, target.path)
    after = tracking.load_record(target.path)
    assert after.resolved_head_session == NEW
    assert after.head_revision == result.head_revision


def test_persistence_error_is_not_reported_as_success(target, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("record persistence unavailable")

    before = target.path.read_bytes()
    monkeypatch.setattr(tracking, "save_record", fail)
    with pytest.raises(OSError, match="persistence unavailable"):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_native_proof_exception_is_not_a_fallback(target, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("native evidence unavailable")

    before = target.path.read_bytes()
    monkeypatch.setattr(native_lifecycle_source, "start_identity", fail)
    with pytest.raises(OSError, match="native evidence"):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before


def test_fresh_execution_authority_is_required(target, monkeypatch):
    original = config_authority.registration_record_config
    calls = []

    def recheck(record):
        calls.append(record.worktree_id)
        if len(calls) == 2:
            raise ValueError("execution authority changed")
        return original(record)

    monkeypatch.setattr(config_authority, "registration_record_config", recheck)
    before = target.path.read_bytes()
    with pytest.raises(ValueError, match="authority changed"):
        replacement.replace_dead_head(target.request)
    assert target.path.read_bytes() == before
    assert calls == ["target", "target"]


@pytest.mark.parametrize("proof", ["valid", "reused-pid", "wrong-epoch", "no-hold"])
def test_candidate_observation_corroborates_original_source(target, monkeypatch, proof):
    monkeypatch.setattr(native_lifecycle_source, "start_identity", _START_IDENTITY)
    epoch = {**EPOCH, **({"boot_id": "different"} if proof == "wrong-epoch" else {})}
    monkeypatch.setattr(native_source_context, "epoch", lambda: epoch)
    monkeypatch.setattr(native_source_context, "process", lambda pid: {
        "pid": pid, "start_time": "789" if proof == "reused-pid" else "456", "parent_pid": 1,
    })
    monkeypatch.setattr(
        native_lifecycle_source, "session_process_owns_hold",
        lambda session, pid, birth: proof != "no-hold" and session == NEW and pid == 123,
    )
    def never_capture():
        raise AssertionError("the observer must never use its own ancestry")
    monkeypatch.setattr(native_source_context, "capture", never_capture)
    before = target.path.read_bytes()
    if proof == "valid":
        observed = replacement.observe_candidate("target", NEW, 123)
        assert observed["replacement_source"] == SOURCE
        assert observed["expected_head"] == OLD
        assert observed["expected_lifecycle_revision"] == target.request.expected_lifecycle_revision
        assert observed["native_source"]["ancestors"] == [{"pid": 123, "start_time": "456"}]
        assert observed["replacement_session"] == NEW
    else:
        with pytest.raises(ValueError, match="original registered native source"):
            replacement.observe_candidate("target", NEW, 123)
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["ended", "ambiguous", "missing", "changed-owner", "wrong-workspace"])
def test_candidate_observation_refuses_changed_registration(target, monkeypatch, mutation):
    monkeypatch.setattr(native_source_context, "epoch", lambda: EPOCH)
    monkeypatch.setattr(native_source_context, "process", lambda pid: {
        "pid": pid, "start_time": "456", "parent_pid": 1,
    })
    record = tracking.load_record(target.path)
    entry = record.session_entry(NEW)
    if mutation == "ended":
        entry.activations[-1].ended_at = "2026-01-02T00:00:00"
    elif mutation == "ambiguous":
        entry.activations[-1].source_ambiguous = True
    elif mutation == "missing":
        entry.activations[-1].source_identity = None
    elif mutation == "wrong-workspace":
        monkeypatch.setattr(replacement, "_candidate_cwd", lambda _id: target.checkout.parent)
    tracking.save_record(record, target.path)
    before = target.path.read_bytes()
    if mutation == "changed-owner":
        original = tracking.load_record
        calls = []
        def changed(path):
            value = original(path)
            calls.append(path)
            if len(calls) == 2:
                value.lifecycle_revision += 1
            return value
        monkeypatch.setattr(tracking, "load_record", changed)
    with pytest.raises(ValueError):
        replacement.observe_candidate("target", NEW, 123)
    assert target.path.read_bytes() == before


def test_candidate_observation_command_never_promotes(target, monkeypatch, capfd):
    monkeypatch.setattr(native_source_context, "epoch", lambda: EPOCH)
    monkeypatch.setattr(native_source_context, "process", lambda pid: {
        "pid": pid, "start_time": "456", "parent_pid": 1,
    })
    before = target.path.read_bytes()
    assert replacement_cli.cmd_replace_dead_head(replacement_args(
        "observe", "--worktree-id", "target", "--session-id", NEW, "--pid", "123",
    )) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["candidate"]["expected_head"] == OLD
    assert result["candidate"]["replacement_session"] == NEW
    assert target.path.read_bytes() == before


def preflight_target(target):
    return replacement.ReplacementTarget(**{
        key: value for key, value in asdict(target.request).items()
        if not key.startswith("replacement_") and key != "native_source"
    })


@pytest.mark.parametrize("live", [OLD, NEW, None])
def test_preflight_refuses_any_registered_live_holder(target, monkeypatch, live):
    monkeypatch.setattr(replacement, "session_liveness", lambda sid: "live" if sid == live else "dead")
    before = target.path.read_bytes()
    request = preflight_target(target)
    if live is None:
        assert replacement.check_dead_head(request) == asdict(request)
    else:
        with pytest.raises(ValueError):
            replacement.check_dead_head(request)
    assert target.path.read_bytes() == before


def test_preflight_command_checks_exact_target_without_permission_or_write(target, monkeypatch, capfd):
    monkeypatch.setattr(replacement, "session_liveness", lambda _sid: "dead")
    request = preflight_target(target)
    monkeypatch.setattr(replacement_cli.sys, "stdin", io.StringIO(request_json(request)))
    before = target.path.read_bytes()
    assert replacement_cli.cmd_replace_dead_head(replacement_args("check", "--stdin")) == 0
    assert json.loads(capfd.readouterr().out)["target"] == asdict(request)
    assert target.path.read_bytes() == before
