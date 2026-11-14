"""Explicit legacy recovery retains ordinary finalization and ownership gates."""

from __future__ import annotations

from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import claimant, context_cli, execution_spaces, finalize, finalize_cli
from agent_worktrees import git_ops, retired_owner_finalization as recovery, tracking
from agent_worktrees import status_monitor_runtime, tracking_write
from agent_worktrees import output, sessions
from agent_worktrees.tracking_claims import canonicalize_ref
from pr_test_helpers import _git
from test_local_worktree_authority import local_worktree  # noqa: F401


pytestmark = pytest.mark.usefixtures("git_only_mux")


@pytest.fixture
def local_git_checkout(tmp_path: Path) -> tuple[Path, Path]:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    _git("init", "--template=", "-b", "master", cwd=anchor)
    _git("-c", "user.name=Example", "-c", "user.email=example@example.com",
         "commit", "--allow-empty", "-m", "fixture", cwd=anchor)
    checkout = tmp_path / "worktrees" / "old-host-worktree"
    _git("worktree", "add", "-b", "worktree/example", str(checkout), cwd=anchor)
    return anchor, checkout


@pytest.fixture
def retired(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    fixture = request.getfixturevalue("local_worktree")
    fixture.data.pop("creation_nonce")
    fixture.data["resources"] = []
    fixture.data["owner_ref"] = "old-host/example/retired-parent"
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    fixture.parent = fixture.ledger.parent / "retired-parent.yaml"
    fixture.parent_data = {
        **fixture.data, "worktree_id": "retired-parent", "status": "archived",
        "owner_ref": None, "branch": "worktree/retired-parent",
        "worktree_path": str(fixture.checkout.parent / "retired-parent"),
        "resources": [{
            "kind": "worktree", "ref": f"old-host/example/{fixture.checkout.name}",
            "state": "abandoned", "created_at": "2026-01-01T00:00:00",
        }],
    }
    fixture.parent.write_text(yaml.safe_dump(fixture.parent_data), encoding="utf-8")
    fixture.config = fixture.load()
    fixture.claimant_death_probe = claimant._confirmed_record_death
    monkeypatch.setattr(claimant, "_confirmed_record_death", lambda record, config: False)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    monkeypatch.setattr(sessions, "mux_status_many", lambda *a, **k: {})
    monkeypatch.setattr(finalize.procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    return fixture


def _run(fixture: SimpleNamespace, **changes) -> int:
    options = dict(
        worktree_id=fixture.checkout.name, worktree_id_flag=None, config=None, json=True,
        dry_run=False, abandon=False, handoff_to=None, force_open_pr=False,
        explain_landing=False, recover_retired_owner=fixture.data["owner_ref"],
    )
    options.update(changes)
    return finalize_cli.cmd_finalize(Namespace(**options))


def test_explicit_recovery_removes_landed_legacy_child_on_actual_branch(retired: SimpleNamespace, capsys) -> None:
    _git("switch", "-c", "reviewed-topic", cwd=retired.checkout)
    parent = retired.parent.read_bytes()
    assert _run(retired, recover_retired_owner=None) == 1
    assert _run(retired) == 0
    assert not retired.checkout.exists()
    assert str(retired.checkout) not in _git("worktree", "list", "--porcelain", cwd=retired.anchor)
    assert not git_ops.ref_exists("refs/heads/reviewed-topic", cwd=retired.anchor)
    assert retired.parent.read_bytes() == parent
    child = tracking.load_record(retired.ledger)
    assert child.owner_ref == retired.data["owner_ref"]
    assert not child.receiving_allocation_present
    assert "parent-obligation settlement is unconfirmed" not in capsys.readouterr().err


@pytest.mark.parametrize("source", ["dirty", "unlanded"])
def test_recovery_preserves_unfinished_source(retired: SimpleNamespace, source: str) -> None:
    pending = retired.checkout / "unfinished.txt"
    pending.write_text("keep\n", encoding="utf-8")
    if source == "unlanded":
        _git("add", "unfinished.txt", cwd=retired.checkout)
        _git("-c", "user.name=Example", "-c", "user.email=example@example.com",
             "commit", "-m", "unfinished", cwd=retired.checkout)
    assert _run(retired) == 1
    assert pending.read_text(encoding="utf-8") == "keep\n"


@pytest.mark.parametrize("fault", [
    "active", "present", "missing-claim", "held-claim", "reserved-claim", "duplicate-claim", "producer-claim",
    "malformed-parent",
])
def test_recovery_refuses_unproven_original_custody_without_writes(
    retired: SimpleNamespace, fault: str,
) -> None:
    parent = retired.parent_data
    if fault == "active":
        parent["status"] = "active"
    elif fault == "present":
        Path(parent["worktree_path"]).mkdir()
    elif fault == "missing-claim":
        parent["resources"] = []
    elif fault == "held-claim":
        parent["resources"][0]["state"] = "active"
    elif fault == "reserved-claim":
        parent["resources"][0]["handoff_bundle"] = "other-bundle"
    elif fault == "duplicate-claim":
        parent["resources"].append(dict(parent["resources"][0]))
    elif fault == "producer-claim":
        parent["resources"][0]["producer"] = {"namespace": "unknown"}
    retired.parent.write_text(yaml.safe_dump(parent), encoding="utf-8")
    if fault == "malformed-parent":
        retired.parent.write_text("resources: [\n", encoding="utf-8")
    before = retired.ledger.read_bytes(), retired.parent.read_bytes()
    assert _run(retired) == 1
    assert (retired.ledger.read_bytes(), retired.parent.read_bytes()) == before
    assert retired.checkout.exists()


@pytest.mark.parametrize("alive", [True, None])
def test_recovery_keeps_live_and_unknown_parent(
    retired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, alive: bool | None,
) -> None:
    monkeypatch.setattr(claimant, "_confirmed_record_death", lambda record, config: alive)
    before = retired.ledger.read_bytes(), retired.parent.read_bytes()
    assert _run(retired) == 1
    assert (retired.ledger.read_bytes(), retired.parent.read_bytes()) == before


@pytest.mark.parametrize("subject", ["parent", "child"])
def test_recovery_is_scoped_and_rechecks_incarnation(retired: SimpleNamespace, subject: str) -> None:
    record = tracking.load_record(retired.ledger)
    with recovery.scope(record.worktree_id, retired.config, record.owner_ref):
        execution_spaces.require_record_mutation(record, retired.config)
        path, data = (
            (retired.parent, retired.parent_data) if subject == "parent"
            else (retired.ledger, retired.data)
        )
        data["creation_nonce"] = "replacement"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        record = tracking.load_record(retired.ledger)
        with pytest.raises(execution_spaces.ExecutionSpaceError):
            execution_spaces.require_record_mutation(record, retired.config)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="original receiving allocation"):
        execution_spaces.require_record_mutation(record, retired.config)


def test_recovery_dry_run_and_override_rejection_do_not_write(retired: SimpleNamespace) -> None:
    before = retired.ledger.read_bytes(), retired.parent.read_bytes()
    assert _run(retired, dry_run=True) == 0
    assert _run(retired, force_open_pr=True) == 2
    assert (retired.ledger.read_bytes(), retired.parent.read_bytes()) == before


def test_removal_excludes_parent_claim_writers(retired: SimpleNamespace) -> None:
    def try_writer() -> bool:
        with tracking._RecordLock(retired.parent, blocking=False, require_sidecar=True) as lock:
            return lock.acquired

    record = tracking.load_record(retired.ledger)
    with recovery.scope(record.worktree_id, retired.config, record.owner_ref):
        with recovery.removal_guard(retired.ledger), ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(try_writer).result(timeout=2) is False
    assert try_writer() is True


def test_selected_runtime_advertises_its_recovery_parser(retired: SimpleNamespace, capsys) -> None:
    from agent_worktrees import __main__ as cli

    assert context_cli.cmd_get(Namespace(key="finalize-recovery")) == 0
    declared = json.loads(capsys.readouterr().out)
    args = cli.build_parser().parse_args([
        "finalize", retired.checkout.name, declared["retired_owner"]["option"],
        retired.data["owner_ref"], "--dry-run",
    ])
    assert finalize_cli.cmd_finalize(args) == 0


def test_recovery_rechecks_absence_when_ordinary_custody_becomes_valid(
    retired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import retained_checkout

    record = tracking.load_record(retired.ledger)
    with recovery.scope(record.worktree_id, retired.config, record.owner_ref):
        Path(retired.parent_data["worktree_path"]).mkdir()
        monkeypatch.setattr(retained_checkout, "resolve_owner", lambda *a, **k: retired.parent)
        with pytest.raises(execution_spaces.ExecutionSpaceError):
            execution_spaces.require_record_mutation(record, retired.config)


@pytest.mark.parametrize("after_admission", [False, True])
@pytest.mark.parametrize("metadata", [{"producer": {"namespace": "unknown"}}, {"future": "opaque"}])
@pytest.mark.parametrize("canonical", [False, True])
def test_recovery_rejects_mixed_duplicate_claims(
    retired: SimpleNamespace, after_admission: bool, metadata: dict, canonical: bool,
) -> None:
    from contextlib import nullcontext

    record = tracking.load_record(retired.ledger)
    scope = recovery.scope(record.worktree_id, retired.config, record.owner_ref)
    with scope if after_admission else nullcontext():
        ref = retired.parent_data["resources"][0]["ref"]
        retired.parent_data["resources"].append({
            **retired.parent_data["resources"][0], **metadata, "state": "active",
            "ref": canonicalize_ref("worktree", ref) if canonical else ref,
        })
        retired.parent.write_text(yaml.safe_dump(retired.parent_data), encoding="utf-8")
        if after_admission:
            with pytest.raises(execution_spaces.ExecutionSpaceError):
                execution_spaces.require_record_mutation(record, retired.config)
        else:
            assert _run(retired) == 1
        assert retired.checkout.exists()


@pytest.mark.parametrize("session_claim", [False, True])
def test_recovery_keeps_session_settlement_inside_admitted_process(
    retired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, session_claim: bool,
) -> None:
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "recovery-session")
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    if session_claim:
        record = tracking.load_record(retired.ledger)
        retired.data["resources"].append({
            "kind": "session", "state": "active",
            "ref": tracking.format_claim_ref(
                record.execution_machine, record.repo, record.worktree_id,
                session="recovery-session",
            ),
        })
        retired.ledger.write_text(yaml.safe_dump(retired.data), encoding="utf-8")

    def daemon(*args, **kwargs):
        pytest.fail("process-local recovery admission must not be sent to a resident daemon")

    monkeypatch.setattr(tracking_write, "dispatch", daemon)
    assert _run(retired) == 0
    assert not retired.checkout.exists()
    if session_claim:
        assert tracking.load_record(retired.ledger).resources[0].state == "at-rest"


def test_recovery_uses_the_qualified_retired_parent_for_inactivity(
    retired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(claimant, "_confirmed_record_death", retired.claimant_death_probe)
    assert claimant.local_claimant_alive(retired.data["owner_ref"]) is None
    assert _run(retired) == 0


@pytest.mark.parametrize("probe", ["live", "unknown"])
def test_recovery_checks_actual_parent_binding_probe(
    retired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, probe: str,
) -> None:
    from agent_worktrees import reclaim

    retired.parent_data["sessions"] = [{"session_id": "parent-session", "name": "parent"}]
    retired.parent.write_text(yaml.safe_dump(retired.parent_data), encoding="utf-8")
    seen = []

    def bindings(*, worktree_id, session_ids, strict=False):
        seen.append((worktree_id, session_ids, strict))
        if probe == "unknown":
            raise OSError("native binding observation unavailable")
        return [{"session_id": "parent-session", "homing": "bare"}]

    monkeypatch.setattr(reclaim, "resolve_bound_copilots", bindings)
    monkeypatch.setattr(sessions, "mux_status_many", lambda *a, **k: {})
    before = retired.ledger.read_bytes(), retired.parent.read_bytes()
    assert _run(retired) == 1
    assert seen == [("retired-parent", ["parent-session"], True)]
    assert (retired.ledger.read_bytes(), retired.parent.read_bytes()) == before
    assert retired.checkout.exists()


@pytest.mark.parametrize("subject", ["child", "parent"])
@pytest.mark.parametrize("damage", ["yaml", "list", "branch", "worktree_id"])
def test_recovery_malformed_record_is_a_json_refusal(
    retired: SimpleNamespace, subject: str, damage: str,
) -> None:
    path, data = (
        (retired.ledger, retired.data) if subject == "child"
        else (retired.parent, retired.parent_data)
    )
    if damage in {"branch", "worktree_id"}:
        del data[damage]
        text = yaml.safe_dump(data)
    else:
        text = "resources: [\n" if damage == "yaml" else "- incomplete\n"
    path.write_text(text, encoding="utf-8")
    before = retired.ledger.read_bytes(), retired.parent.read_bytes()
    with output.capture_json_output() as captured:
        assert _run(retired) == 1
    response = json.loads(captured.getvalue())
    assert response["error"]
    assert (retired.ledger.read_bytes(), retired.parent.read_bytes()) == before
    assert retired.checkout.exists()


def test_recovery_retries_a_freeze_after_original_custody_is_restored(
    retired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire = finalize.FinalizeLock.acquire
    changed = False

    def invalidate_after_freeze(lock):
        nonlocal changed
        result = acquire(lock)
        if not changed:
            changed = True
            retired.parent_data["status"] = "active"
            retired.parent.write_text(yaml.safe_dump(retired.parent_data), encoding="utf-8")
        return result

    monkeypatch.setattr(finalize.FinalizeLock, "acquire", invalidate_after_freeze)
    head = _git("rev-parse", "HEAD", cwd=retired.checkout)
    assert _run(retired) == 1
    assert tracking.load_record(retired.ledger).status == "finalizing"
    assert _git("rev-parse", "HEAD", cwd=retired.checkout) == head
    retired.parent_data["status"] = "archived"
    retired.parent.write_text(yaml.safe_dump(retired.parent_data), encoding="utf-8")
    assert _run(retired) == 0
    assert not retired.checkout.exists()
    assert tracking.load_record(retired.ledger).status == "finalized"
