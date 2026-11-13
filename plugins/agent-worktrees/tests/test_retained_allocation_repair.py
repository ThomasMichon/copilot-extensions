"""Retained repair preserves the original allocation and execution representation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, creation_tail_repair as repair
from agent_worktrees import launch_seed_state, tracking, worktree_creation
from test_creation_tail_repair import _creation_fixture, _run


def test_paired_intent_ignores_legacy_names_but_preserves_original_subject(tmp_path, monkeypatch):
    from agent_worktrees import paired_allocation_location as paired

    creation = _creation_fixture(tmp_path, monkeypatch)
    record = creation.record
    config = creation.configs["harness"]
    paired.reserve(config, record.worktree_id, record.creation_nonce)
    path = record.yaml_path.with_suffix(".paired-allocation.json")
    data = json.loads(path.read_text())
    assert "owner" not in data
    data["owner"] = {"machine": "obsolete", "platform": "foreign", "machine_identity": None}
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    paired.reserve(config, record.worktree_id, record.creation_nonce)
    assert path.read_bytes() == before
    assert paired.original(record, creation.root) == creation.roots["knowledge"] / f"{record.worktree_id}-k"
    for field in ("creation_nonce", "repo", "anchor", "unexpected"):
        changed = {**data, field: "replacement"}
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="conflicts"):
            paired.original(record, creation.root)
        with pytest.raises(ValueError, match="conflicts"):
            paired.reserve(config, record.worktree_id, record.creation_nonce)


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("suffix", ["abcd", "a" * 32])
def test_actual_git_pair_repair_retains_tuple_and_historical_allocation(
    tmp_path, monkeypatch, capsys, platform, suffix,
):
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    creation = _creation_fixture(
        tmp_path, monkeypatch, native_git=True, tuple_identity=True, suffix=suffix,
    )
    record = tracking.load_record(creation.record.yaml_path)
    record.title = "Retained title"
    tracking.save_record(record)
    original = yaml.safe_load(record.yaml_path.read_text())
    seed_before = launch_seed_state.state_path(record.yaml_path).read_bytes()
    code, report = _run(creation, capsys, fix=True)
    assert code == 0, report
    assert report["prepared"] and report["repaired"]
    parent = tracking.load_record(record.yaml_path)
    child_path = creation.projects["knowledge"] / "worktrees" / f"{parent.worktree_id}-k.yaml"
    child = tracking.load_record(child_path)
    assert parent.machine_identity == child.machine_identity == record.machine_identity
    assert parent.pair_ref == f"custom-space/knowledge/{child.worktree_id}"
    assert child.pair_ref == f"custom-space/harness/{parent.worktree_id}"
    assert parent.pair_id == child.pair_id == f"20261013-100000-{suffix}"
    assert child.worktree_id == f"{record.worktree_id}-k"
    after = yaml.safe_load(parent.yaml_path.read_text())
    for field, value in original.items():
        if field not in {"pair_id", "pair_role", "pair_ref", "pair_kind", "updated_at"}:
            assert after[field] == value, field
    assert launch_seed_state.state_path(record.yaml_path).read_bytes() == seed_before
    monkeypatch.setattr(
        worktree_creation, "_carve_paired_knowledge", lambda *_a, **_kw: pytest.fail("second carve"),
    )
    parent_before, child_before = parent.yaml_path.read_bytes(), child_path.read_bytes()
    code, second = _run(creation, capsys, fix=True)
    assert code == 0 and second["prepared"] and not second["repaired"], second
    assert (parent.yaml_path.read_bytes(), child_path.read_bytes()) == (parent_before, child_before)
    assert launch_seed_state.state_path(record.yaml_path).read_bytes() == seed_before


def test_rename_refreshes_pair_reporters_without_changing_allocation(tmp_path, monkeypatch, capsys):
    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True)
    original_machine = creation.record.machine
    for name, config in list(creation.configs.items()):
        creation.configs[name] = replace(config, machine="preferred")
        (Path(config.default_repo.anchor) / "machines.yaml").write_text(
            f"machines:\n  preferred: {{hostname: {original_machine}}}\n",
        )
    seed_before = launch_seed_state.state_path(creation.record.yaml_path).read_bytes()
    code, report = _run(creation, capsys, fix=True)
    assert code == 0, report
    parent = tracking.load_record(creation.record.yaml_path)
    child = tracking.load_record(
        creation.projects["knowledge"] / "worktrees" / f"{parent.worktree_id}-k.yaml",
    )
    assert parent.machine == child.machine == "preferred"
    assert parent.machine_identity == child.machine_identity
    assert parent.pair_ref == f"preferred/knowledge/{child.worktree_id}"
    assert child.pair_ref == f"preferred/harness/{parent.worktree_id}"
    assert launch_seed_state.state_path(parent.yaml_path).read_bytes() == seed_before


@pytest.mark.parametrize("both_projects", [False, True])
def test_existing_pair_rename_keeps_original_sibling_finality_gate(
    tmp_path, monkeypatch, capsys, both_projects,
):
    from agent_worktrees import paired_claims

    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True)
    code, report = _run(creation, capsys, fix=True)
    assert code == 0, report
    parent = tracking.load_record(creation.record.yaml_path)
    original_ref = parent.pair_ref
    observed = cfg.socket.gethostname()
    for name in ("harness", "knowledge") if both_projects else ("harness",):
        (creation.anchors[name] / "machines.yaml").write_text(yaml.safe_dump({"machines": {
            "renamed": {"hostname": observed, "execution_platform": cfg.detect_platform()},
        }}), encoding="utf-8")
        creation.configs[name] = replace(creation.configs[name], machine="renamed")
    parent = tracking.load_record(parent.yaml_path)
    assert parent.execution_machine == "renamed" and parent.pair_ref == original_ref
    child = paired_claims.reciprocal_sibling(parent)
    assert child is not None
    with pytest.raises(ValueError, match="finalized"):
        tracking.settle_resource_claim(parent, original_ref)
    child.status = "finalized"
    tracking.save_record(child)
    assert tracking.settle_resource_claim(parent, original_ref).is_at_rest
    assert tracking.load_record(parent.yaml_path).pair_ref == original_ref


@pytest.mark.parametrize("field", ["creation_nonce", "owner_ref"])
def test_repair_cannot_continue_against_a_replaced_retained_allocation(
    tmp_path, monkeypatch, capsys, field,
):
    creation = _creation_fixture(tmp_path, monkeypatch)
    path = creation.record.yaml_path
    seed_before = launch_seed_state.state_path(path).read_bytes()

    def replace_record(*_args):
        data = yaml.safe_load(path.read_text())
        data[field] = (
            "replacement" if field == "creation_nonce"
            else f"{creation.record.machine}/harness/replacement-parent"
        )
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    monkeypatch.setattr(repair, "_prepare_permissions", replace_record)
    monkeypatch.setattr(
        worktree_creation, "_carve_paired_knowledge", lambda *_a, **_kw: pytest.fail("replacement carved"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "allocation identity changed" in report["error"]
    assert not creation.roots["knowledge"].exists()
    assert launch_seed_state.state_path(path).read_bytes() == seed_before
