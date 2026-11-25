"""Cold legacy Git/ledger consumers; native liveness is explicitly fixture-supplied."""

import json
import os
import shutil
import sqlite3
import socket
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest
import yaml

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as state, tracking
from agent_worktrees import config as cfg, creation_requests, git_ops
from agent_worktrees import checkout_transfer as transfer, procs, sessions
from agent_procutil.owned_process import run_caller_owned
import test_checkout_relocation as checkout_fixture

checkout = checkout_fixture.checkout


@pytest.fixture
def legacy(checkout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    fixture = checkout
    retained = yaml.safe_load(fixture.record.yaml_path.read_bytes())
    retained.pop("creation_nonce")
    fixture.record.yaml_path.write_text(yaml.safe_dump(retained), encoding="utf-8")
    fixture.record = tracking.load_record(fixture.record.yaml_path)
    original_probe = sessions.verify_worktree_active
    original_blocker = procs.checkout_retirement_blocker

    def scoped_probe(record: tracking.WorktreeRecord, **kwargs: Any) -> Any:
        if record.yaml_path == fixture.record.yaml_path:
            return SimpleNamespace(probes_ok=True, active=False, mux_live=False)
        return original_probe(record, **kwargs)

    def scoped_blocker(path: str) -> str | None:
        if Path(path).parent in {fixture.source.parent, fixture.destination.parent}:
            return None
        return original_blocker(path)

    monkeypatch.setattr(sessions, "verify_worktree_active", scoped_probe)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", scoped_blocker)
    return fixture


@pytest.mark.timeout(120)
def test_complete_legacy_component_preserves_absence_and_destination_mutation(legacy: SimpleNamespace) -> None:
    fixture = legacy
    before = fixture.record.yaml_path.read_bytes()
    discovered = run_caller_owned(
        [sys.executable, "-m", "agent_worktrees", "--project", "sample", "get", "checkout-relocation"],
        cwd=fixture.home,
        env=dict(
            os.environ, AGENT_HOME=str(fixture.home), HOME=str(fixture.home),
            USERPROFILE=str(fixture.home), COPILOT_HOME=str(fixture.copilot),
        ),
        timeout=30, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert discovered.returncode == 0, (discovered.stdout, discovered.stderr)
    capabilities = json.loads(discovered.stdout)
    assert capabilities["legacy_identity"]["supported"]
    report = relocation._action_plan(
        fixture.record.worktree_id, str(fixture.destination), fixture.config,
    )
    assert report["schema"] == capabilities["legacy_identity"]["schema"] == state.LEGACY_SCHEMA
    assert fixture.record.yaml_path.read_bytes() == before
    result = relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "complete"
    assert not fixture.source.exists()
    assert (fixture.destination / "ignored.bin").read_bytes() == bytes(range(256))
    assert (fixture.destination / "tracked.txt").read_bytes() == b"original\0content\n"
    current = tracking.load_record(fixture.record.yaml_path)
    assert current.worktree_path == str(fixture.destination)
    assert not current.creation_nonce
    assert "creation_nonce" not in yaml.safe_load(current.yaml_path.read_bytes())
    current.summary = "supported destination mutation"
    tracking.save_record(current)
    assert tracking.load_record(current.yaml_path).summary == "supported destination mutation"
    assert relocation.move(
        fixture.record.worktree_id, str(fixture.destination), fixture.config,
    )["phase"] == "complete"


@pytest.mark.parametrize("nonce", [None, "", 0, False])
def test_present_invalid_metadata_is_not_an_unversioned_subject(
    legacy: SimpleNamespace, nonce: object,
) -> None:
    ledger = legacy.record.yaml_path
    data = yaml.safe_load(ledger.read_bytes())
    data["creation_nonce"] = nonce
    ledger.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="completed ordinary sessionless"):
        relocation._action_plan(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert ledger.read_bytes() == before and not state.journal_path(ledger).exists()


@pytest.mark.parametrize("kind", ["receiving", "ordinary", "unsupported", "future-schema", "malformed", "busy"])
def test_absent_nonce_does_not_hide_original_creation_evidence(legacy: SimpleNamespace, kind: str) -> None:
    ledger = legacy.record.yaml_path
    database = ledger.parent.parent / "workspace-creation.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE creations(version INTEGER,facts TEXT,worktree TEXT)")
    connection.execute("INSERT INTO creations VALUES(?,?,?)", (
        999 if kind in {"unsupported", "future-schema"} else creation_requests.SCHEMA_VERSION,
        "malformed" if kind == "malformed" else json.dumps(
            {"receiving_allocation": {}} if kind == "receiving" else {},
        ),
        json.dumps(
            {"renamed_child": legacy.record.worktree_id} if kind == "future-schema"
            else {"worktree": {"id": legacy.record.worktree_id}},
        ),
    ))
    connection.commit()
    connection.close()
    if kind == "busy":
        Path(str(database) + "-wal").write_bytes(b"owner evidence")
    before = database.read_bytes(), ledger.read_bytes()
    with pytest.raises(ValueError, match="creation/receiving evidence|classification"):
        relocation.plan(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert (database.read_bytes(), ledger.read_bytes()) == before
    assert not state.journal_path(ledger).exists()
    assert legacy.source.exists() and not legacy.destination.exists()


def _interrupt_copy(fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    with monkeypatch.context() as injection:
        def interrupted(*args: object) -> None:
            raise OSError("fixture copy interruption")
        injection.setattr(transfer, "copy", interrupted)
        with pytest.raises(OSError, match="fixture copy interruption"):
            relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    retained = state.read(fixture.record.yaml_path)
    assert retained is not None
    return retained


@pytest.mark.timeout(120)
@pytest.mark.parametrize("kind", ["ledger", "administration", "checkout", "head"])
def test_reused_or_changed_incarnation_cannot_resume_original_move(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    retained = _interrupt_copy(legacy, monkeypatch)
    ledger = legacy.record.yaml_path
    if kind == "ledger":
        replacement = ledger.with_suffix(".replacement")
        replacement.write_bytes(ledger.read_bytes())
        os.replace(replacement, ledger)
    elif kind in {"administration", "checkout"}:
        source = (
            Path(retained["legacy_identity"]["administrative"]["path"])
            if kind == "administration" else legacy.source
        )
        backup = source.with_name(source.name + ".original")
        source.rename(backup)
        shutil.copytree(backup, source)
    else:
        head = git_ops.git("rev-parse", "HEAD", cwd=legacy.anchor).stdout.strip()
        tree = git_ops.git("rev-parse", "HEAD^{tree}", cwd=legacy.anchor).stdout.strip()
        other = git_ops.git(
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", head, "-m", "different incarnation",
            cwd=legacy.anchor,
        ).stdout.strip()
        git_ops.git("update-ref", f"refs/heads/{legacy.record.branch}", other, cwd=legacy.anchor)
    journal_before = state.journal_path(ledger).read_bytes()
    with pytest.raises(ValueError, match="incarnation changed|HEAD changed"):
        relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert state.journal_path(ledger).read_bytes() == journal_before
    assert legacy.source.exists() and not legacy.destination.exists()
    current = state.read(ledger)
    assert current is not None and current["stage"] == retained["stage"]


@pytest.mark.timeout(120)
@pytest.mark.parametrize("phase", [
    "copied", "published", "git-repaired", "before-record-save", "rebound", "retiring", "complete",
])
def test_legacy_recovery_keeps_original_operation_across_each_durable_boundary(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    original_write = state.write
    with monkeypatch.context() as injection:
        def interrupted(ledger: Path, data: dict[str, Any]) -> None:
            if data["phase"] == phase:
                raise OSError("fixture checkpoint interruption")
            return original_write(ledger, data)
        injection.setattr(state, "write", interrupted)
        if phase == "before-record-save":
            original_save = tracking.save_record

            def interrupted_record(record: tracking.WorktreeRecord, *args: Any, **kwargs: Any) -> None:
                if record.yaml_path == legacy.record.yaml_path and record.worktree_path == str(legacy.destination):
                    raise OSError("fixture checkpoint interruption")
                return original_save(record, *args, **kwargs)

            injection.setattr(tracking, "save_record", interrupted_record)
        with pytest.raises(OSError, match="fixture checkpoint interruption"):
            relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    retained = state.read(legacy.record.yaml_path)
    assert retained is not None
    assert retained["phase"] != phase
    assert retained["identity"]["creation_nonce"] == ""
    result = relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert result["phase"] == "complete"
    assert result["stage"] == retained["stage"]
    assert not legacy.source.exists()
    assert "creation_nonce" not in yaml.safe_load(legacy.record.yaml_path.read_bytes())


@pytest.mark.timeout(120)
def test_legacy_recovery_uses_physical_namespace_after_reporter_and_default_root_change(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = legacy
    sibling = fixture.source.parent / "unrelated-active"
    git_ops.create_worktree(str(fixture.anchor), str(sibling), "worktree/unrelated-active", "HEAD")
    other = tracking.create_new_record(
        sibling.name, "worktree/unrelated-active", str(sibling), "sample",
        fixture.record.machine, fixture.record.platform, fixture.record.yaml_path.parent,
    )
    untouched = other.yaml_path.read_bytes(), (sibling / ".git").read_bytes()
    retained = _interrupt_copy(fixture, monkeypatch)
    (fixture.anchor / "machines.yaml").write_text(json.dumps({"machines": {
        "new-reporter": {
            "hostname": socket.gethostname(),
            "execution_platform": cast(Callable[[], str], getattr(cfg, "detect_platform"))(),
        },
    }}), encoding="utf-8")
    future = fixture.home / "future-default"
    future.mkdir()
    repo = replace(fixture.config.default_repo, worktree_root=str(future))
    fixture.config = replace(fixture.config, machine="new-reporter", repos={"sample": repo})
    configuration = cfg.project_dir("sample") / "config.yaml"
    data = yaml.safe_load(configuration.read_bytes())
    data["machine"] = "new-reporter"
    data["repos"]["sample"]["worktree_root"] = str(future)
    configuration.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "complete" and result["stage"] == retained["stage"]
    assert result["legacy_identity"]["namespace"] == retained["legacy_identity"]["namespace"]
    assert (other.yaml_path.read_bytes(), (sibling / ".git").read_bytes()) == untouched
    assert sibling.is_dir() and not fixture.source.exists()


@pytest.mark.timeout(120)
def test_legacy_abort_keeps_original_birth_absence(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = _interrupt_copy(legacy, monkeypatch)
    result = relocation.abort(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert result["phase"] == "cancelled" and result["stage"] == retained["stage"]
    assert legacy.source.exists() and not legacy.destination.exists()
    assert "creation_nonce" not in yaml.safe_load(legacy.record.yaml_path.read_bytes())


@pytest.mark.timeout(120)
@pytest.mark.parametrize("terminal", ["complete", "cancelled"])
@pytest.mark.parametrize("operation", ["add", "bind"])
def test_terminal_legacy_relocation_preserves_task_claim_consumers(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    terminal: str, operation: str,
) -> None:
    import argparse
    from agent_worktrees import claims_binding, handoff_cancel_cli, output
    from agent_worktrees import tracking_claim_write, tracking_claims

    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "sample")
    record = tracking.load_record(legacy.record.yaml_path)
    if operation == "bind":
        record.resources = [tracking_claims.ResourceClaim(
            kind="task", ref="fixture-task", state="released",
        )]
        tracking.save_record(record)
    if terminal == "complete":
        relocation.move(record.worktree_id, str(legacy.destination), legacy.config)
    else:
        _interrupt_copy(legacy, monkeypatch)
        relocation.abort(record.worktree_id, str(legacy.destination), legacy.config)
    args = {
        "worktree_id": record.worktree_id, "yaml_path": str(record.yaml_path),
        "kind": "task", "ref": "fixture-task",
    }
    result = (
        tracking_claim_write.apply_claim_add(args) if operation == "add"
        else tracking_claim_write.apply_claim_bind(args)
    )
    assert result.get("ok"), result
    binding = result["claim_binding"]
    assert binding["creation_nonce"].startswith("legacy:")
    monkeypatch.syspath_prepend(str(
        Path(__file__).resolve().parents[4] / "plugins" / "agent-dispatch" / "src",
    ))
    from agent_dispatch.handoff_claim_binding import validated_binding

    assert validated_binding(binding, {
        "id": "fixture-task", "labels": ["handoff"],
        "target_worktree": record.worktree_id, "target_machine": binding["machine"],
    }) == binding
    current = tracking.load_record(record.yaml_path)
    assert not current.creation_nonce
    assert "creation_nonce" not in yaml.safe_load(record.yaml_path.read_bytes())
    assert claims_binding.for_claim(current, current.resources[0]) == binding
    settled = tracking_claim_write.apply_claim_settle({
        **args, "disposition": "released",
        "expected_record_nonce": binding["creation_nonce"],
        "expected_claim_nonce": binding["claim_nonce"], "expected_kind": "task",
    })
    assert settled.get("ok"), settled
    assert tracking.load_record(record.yaml_path).resources[0].state == "released"
    assert "creation_nonce" not in yaml.safe_load(record.yaml_path.read_bytes())
    with output.capture_json_output() as captured:
        cancelled = handoff_cancel_cli.cmd_cancel_handoff(argparse.Namespace(
            owner_ref=f'{binding["machine"]}/{binding["project"]}/{binding["worktree_id"]}',
            expected_record_nonce=binding["creation_nonce"], worktree_id=None,
            worktree_dir=None, token="fixture-task", dry_run=True,
        ))
    assert cancelled == 0, captured.getvalue()
    before = record.yaml_path.read_bytes()
    refused = tracking_claim_write.apply_claim_settle({
        **args, "disposition": "released", "expected_record_nonce": "legacy:" + "0" * 64,
        "expected_claim_nonce": binding["claim_nonce"], "expected_kind": "task",
    })
    assert refused.get("error") == "rejected"
    assert record.yaml_path.read_bytes() == before


@pytest.mark.timeout(120)
@pytest.mark.parametrize("terminal", ["complete", "cancelled"])
def test_terminal_legacy_relocation_releases_historical_path_custody(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, terminal: str,
) -> None:
    if terminal == "complete":
        relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
        historical = legacy.source
    else:
        _interrupt_copy(legacy, monkeypatch)
        relocation.abort(legacy.record.worktree_id, str(legacy.destination), legacy.config)
        historical = legacy.destination
    historical.mkdir()
    marker = historical / "unrelated.bin"
    marker.write_bytes(b"unrelated retained content")
    replay = (
        relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
        if terminal == "complete"
        else relocation.abort(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    )
    assert replay["replayed"]
    authoritative = legacy.destination if terminal == "complete" else legacy.source
    retained = authoritative.with_name(authoritative.name + ".fixture-held")
    authoritative.rename(retained)
    authoritative.mkdir()
    try:
        with pytest.raises(ValueError, match="incarnation changed"):
            relocation._action_plan(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    finally:
        authoritative.rmdir()
        retained.rename(authoritative)
    next_parent = legacy.source.parent / "next-root"
    next_parent.mkdir()
    destination = next_parent / legacy.record.worktree_id
    result = relocation.move(legacy.record.worktree_id, str(destination), legacy.config)
    assert result["phase"] == "complete"
    assert result["source"] != str(historical)
    assert marker.read_bytes() == b"unrelated retained content"
    assert (destination / "tracked.txt").read_bytes() == b"original\0content\n"
    assert "creation_nonce" not in yaml.safe_load(legacy.record.yaml_path.read_bytes())


@pytest.mark.timeout(120)
@pytest.mark.parametrize(("phase", "action"), [
    ("copying", "retry"), ("copied", "retry"),
    ("copying", "abort"), ("copied", "abort"), ("cancelling", "abort"),
])
def test_replaced_legacy_stage_is_never_reused_or_deleted(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, phase: str, action: str,
) -> None:
    retained = _interrupt_copy(legacy, monkeypatch)
    ledger = legacy.record.yaml_path
    stage = Path(retained["stage"])
    original = stage.with_name(stage.name + ".original")
    stage.rename(original)
    stage.mkdir()
    unrelated = stage / "tracked.txt"
    unrelated.write_bytes(b"replacement directory must survive")
    if phase != "copying":
        retained["phase"] = phase
        state.write(ledger, retained)
    before = ledger.read_bytes(), state.journal_path(ledger).read_bytes()
    operation = relocation.move if action == "retry" else relocation.abort
    with pytest.raises(ValueError, match="stage incarnation changed"):
        operation(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert unrelated.read_bytes() == b"replacement directory must survive"
    assert set(stage.iterdir()) == {unrelated}
    assert (ledger.read_bytes(), state.journal_path(ledger).read_bytes()) == before
    assert legacy.source.is_dir() and not legacy.destination.exists()


@pytest.mark.timeout(120)
def test_legacy_stage_metadata_preservation_does_not_change_ownership(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = _interrupt_copy(legacy, monkeypatch)
    stage = Path(retained["stage"])
    os.utime(stage, ns=(1_000_000_000, 1_000_000_000))
    result = relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert result["phase"] == "complete" and result["stage"] == str(stage)


@pytest.mark.timeout(120)
def test_identical_legacy_record_save_before_admission_does_not_publish_a_fence(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = legacy.record.yaml_path
    original = relocation._action_plan
    before = ledger.read_bytes()

    def raced(*args: Any, **kwargs: Any) -> dict[str, Any]:
        report = original(*args, **kwargs)
        saved = run_caller_owned(
            [
                sys.executable, "-c",
                "import sys; from pathlib import Path; from agent_worktrees import tracking; "
                "path=Path(sys.argv[1]); "
                "lock=tracking._RecordLock(path,require_sidecar=True)\n"
                "with lock: tracking._atomic_write(path,path.read_bytes())",
                str(ledger),
            ],
            cwd=legacy.home,
            env=dict(
                os.environ, AGENT_HOME=str(legacy.home), HOME=str(legacy.home),
                USERPROFILE=str(legacy.home), COPILOT_HOME=str(legacy.copilot),
            ),
            timeout=30, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert saved.returncode == 0, (saved.stdout, saved.stderr)
        return report

    with monkeypatch.context() as injection:
        injection.setattr(relocation, "_action_plan", raced)
        with pytest.raises(ValueError, match="ledger incarnation changed"):
            relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert ledger.read_bytes() == before and not state.journal_path(ledger).exists()
    assert legacy.source.is_dir() and not legacy.destination.exists()
    assert not list(legacy.destination.parent.glob("*.relocating-*"))
    assert relocation.move(
        legacy.record.worktree_id, str(legacy.destination), legacy.config,
    )["phase"] == "complete"


@pytest.mark.timeout(120)
def test_completed_legacy_record_can_grow_past_the_planning_budget(legacy: SimpleNamespace) -> None:
    from agent_worktrees.checkout_relocation_plan import read_snapshot

    assert relocation.move(
        legacy.record.worktree_id, str(legacy.destination), legacy.config,
    )["phase"] == "complete"
    current = tracking.load_record(legacy.record.yaml_path)
    current.summary = "large ordinary record " * 4096
    tracking.save_record(current)
    assert current.yaml_path.stat().st_size > 64 * 1024
    state.require_idle(current.yaml_path)
    assert tracking.load_record(current.yaml_path).summary == current.summary
    current.summary = "subsequent ordinary save"
    tracking.save_record(current)
    state.require_idle(current.yaml_path)
    current.summary = "large ordinary record " * 4096
    tracking.save_record(current)
    with pytest.raises(ValueError, match="oversized"):
        read_snapshot(current.yaml_path)
    assert "creation_nonce" not in yaml.safe_load(current.yaml_path.read_bytes())


@pytest.mark.parametrize("suffix", ["-wal", "-journal", "-shm"])
@pytest.mark.parametrize("lock_snapshot", [False, True])
def test_orphan_creation_sidecars_do_not_qualify_an_absent_store(
    legacy: SimpleNamespace, suffix: str, lock_snapshot: bool,
) -> None:
    from agent_worktrees.receiving_allocation import has_retained_receiving_operation

    ledger = legacy.record.yaml_path
    database = ledger.parent.parent / "workspace-creation.sqlite3"
    sidecar = Path(str(database) + suffix)
    sidecar.write_bytes(b"unfinished original store evidence")
    before = ledger.read_bytes(), sidecar.read_bytes()
    with pytest.raises(ValueError, match="classification"):
        has_retained_receiving_operation(
            legacy.record, ledger.parent, unversioned=True, lock_snapshot=lock_snapshot,
        )
    assert (ledger.read_bytes(), sidecar.read_bytes()) == before
    assert not database.exists() and not state.journal_path(ledger).exists()


@pytest.mark.timeout(120)
@pytest.mark.parametrize("interleaving", [False, True])
def test_relocated_legacy_ancestor_keeps_withheld_root_attribution_frozen(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, interleaving: bool,
) -> None:
    from agent_worktrees import checkout_legacy_identity as identity, root_chain

    ledger = legacy.record.yaml_path
    data = yaml.safe_load(ledger.read_bytes())
    data.update(codename="harbor-lattice", codename_source="custom")
    ledger.write_text(yaml.safe_dump(data), encoding="utf-8")
    legacy.record = tracking.load_record(ledger)
    assert relocation.move(
        legacy.record.worktree_id, str(legacy.destination), legacy.config,
    )["phase"] == "complete"
    current = tracking.load_record(ledger)
    child = tracking.create_new_record(
        "attribution-child", "worktree/attribution-child", str(legacy.home / "attribution-child"),
        "sample", current.machine, current.platform, ledger.parent,
        owner_ref=f"{current.machine}/sample/{current.worktree_id}",
    )
    root_config = legacy.config
    monkeypatch.setattr(root_chain, "_load_root_config", lambda _project: root_config)
    assert root_chain.resolve_root_codename(
        child, project="sample", this_machine=current.machine,
    ) is None
    root_config = replace(root_config, repos={"sample": replace(
        root_config.default_repo, pr=replace(
            root_config.default_repo.pr, source_attribution="codename",
            source_attribution_configured=True,
        ),
    )})
    current.summary = "ordinary root mutation preserves frozen attribution"
    tracking.save_record(current)
    frozen_path = ledger.with_name("attribution-child.root-attribution.json")
    before = frozen_path.read_bytes()
    original = identity.require
    interleaved = False

    def save_between_journal_and_ledger(
        source_record: tracking.WorktreeRecord, source_ledger: Path, report: dict[str, Any],
        *args: Any, **kwargs: Any,
    ) -> None:
        nonlocal interleaved
        if source_ledger == ledger and not interleaved:
            interleaved = True
            saved = run_caller_owned(
                [
                    sys.executable, "-c",
                    "import sys; from pathlib import Path; "
                    "from agent_worktrees import config as cfg, tracking; "
                    "cfg.set_active_project('sample'); "
                    "record=tracking.load_record(Path(sys.argv[1])); "
                    "record.summary='interleaved ordinary record save'; tracking.save_record(record)",
                    str(ledger),
                ],
                cwd=legacy.home,
                env=dict(
                    os.environ, AGENT_HOME=str(legacy.home), HOME=str(legacy.home),
                    USERPROFILE=str(legacy.home), COPILOT_HOME=str(legacy.copilot),
                ),
                timeout=30, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
            )
            assert saved.returncode == 0, (saved.stdout, saved.stderr)
        original(source_record, source_ledger, report, *args, **kwargs)

    with monkeypatch.context() as injection:
        if interleaving:
            injection.setattr(identity, "require", save_between_journal_and_ledger)
        assert root_chain.resolve_root_codename(
            child, project="sample", this_machine=current.machine,
        ) is None
    assert interleaved == interleaving
    assert frozen_path.read_bytes() == before
    assert root_chain.resolve_root_codename(
        child, project="sample", this_machine=current.machine,
    ) is None
    frozen = json.loads(frozen_path.read_bytes())
    assert frozen["root_codename"] is None
    assert "creation_nonce" not in yaml.safe_load(ledger.read_bytes())


@pytest.mark.parametrize("declaration", [None, False, 2, 1], ids=["old", "invalid", "unsupported", "supported"])
def test_legacy_requires_participating_writer_capability_before_move(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, declaration: int | bool | None,
) -> None:
    from agent_worktrees import locks, status_monitor_runtime

    published = legacy.home / "published-writer.lock"
    published.write_bytes(b"incumbent publication")
    monkeypatch.setattr(status_monitor_runtime, "_monitor_lock_path", lambda: published)
    publication = state.writer_capabilities()
    publication.pop("checkout_relocation_legacy_identity_generation")
    if declaration is not None:
        publication["checkout_relocation_legacy_identity_generation"] = declaration
    monkeypatch.setattr(locks, "read_lock", lambda _path: publication)
    before = legacy.record.yaml_path.read_bytes()
    state.require_writer_generation()
    if type(declaration) is int and declaration == 1:
        assert relocation._action_plan(
            legacy.record.worktree_id, str(legacy.destination), legacy.config,
        )["phase"] == "planned"
    else:
        with pytest.raises(ValueError, match="writer does not support"):
            relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert legacy.record.yaml_path.read_bytes() == before
    assert not state.journal_path(legacy.record.yaml_path).exists()
    assert legacy.source.exists() and not legacy.destination.exists()


@pytest.mark.timeout(120)
@pytest.mark.parametrize(("old_schema", "consumer", "expected"), [
    (False, "read", "unsupported relocation journal"),
    (False, "require_idle", "unsupported relocation journal"),
    (True, "read", "accepted baseline"),
    (True, "require_idle", "relocation is unfinished"),
], ids=["prior-reader-refuses", "prior-writer-refuses", "baseline-reader", "baseline-writer-fence"])
def test_frozen_prior_reader_process_preserves_the_original_ledger(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, old_schema: bool, consumer: str, expected: str,
) -> None:
    retained = _interrupt_copy(legacy, monkeypatch)
    if old_schema:
        retained["schema"] = relocation.SCHEMA
        retained.pop("legacy_identity")
        state.write(legacy.record.yaml_path, retained)
    before = legacy.record.yaml_path.read_bytes(), state.journal_path(legacy.record.yaml_path).read_bytes()
    reader = Path(__file__).parents[1] / "fixtures" / "checkout_relocation_reader_v1.txt"
    result = run_caller_owned(
        [
            sys.executable, "-c",
            "import pathlib,sys; scope={}; "
            "exec(compile(pathlib.Path(sys.argv[1]).read_text(),sys.argv[1],'exec'),scope); "
            "scope[sys.argv[3]](pathlib.Path(sys.argv[2])); print('accepted baseline')",
            str(reader), str(legacy.record.yaml_path), consumer,
        ],
        cwd=legacy.home, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=30,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    if old_schema and consumer == "read":
        assert result.returncode == 0 and result.stdout.strip() == "accepted baseline"
    else:
        assert result.returncode != 0 and expected in result.stderr
        assert "accepted baseline" not in result.stdout
    assert (legacy.record.yaml_path.read_bytes(), state.journal_path(legacy.record.yaml_path).read_bytes()) == before


@pytest.mark.timeout(120)
def test_original_ledger_incarnation_cannot_roll_back_a_completed_location(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = legacy.record.yaml_path
    original = ledger.with_suffix(".original")
    replacement = tracking._replace_with_retry

    def retain_original(source: str, destination: str, **kwargs: Any) -> None:
        if Path(destination) == ledger and not original.exists():
            ledger.rename(original)
        replacement(source, destination, **kwargs)

    with monkeypatch.context() as injection:
        injection.setattr(tracking, "_replace_with_retry", retain_original)
        result = relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert result["phase"] == "complete" and original.exists()
    current = ledger.with_suffix(".current")
    ledger.rename(current)
    original.rename(ledger)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="location rolled back"):
        state.require_idle(ledger)
    assert ledger.read_bytes() == before and legacy.destination.is_dir()


@pytest.mark.timeout(120)
def test_completed_location_preserves_native_finality_record_saves(legacy: SimpleNamespace) -> None:
    result = relocation.move(legacy.record.worktree_id, str(legacy.destination), legacy.config)
    assert result["phase"] == "complete"
    current = tracking.load_record(legacy.record.yaml_path)
    current.status = "finalizing"
    tracking.save_record(current)
    (legacy.destination / "ignored.bin").unlink()
    assert git_ops.remove_worktree(str(legacy.anchor), str(legacy.destination))
    current.status = "finalized"
    tracking.save_record(current)
    final = tracking.load_record(current.yaml_path)
    assert final.status == "finalized" and not final.creation_nonce
    assert "creation_nonce" not in yaml.safe_load(current.yaml_path.read_bytes())
    state.require_idle(current.yaml_path)
