"""Retained repair preserves the original allocation and execution representation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, creation_tail_repair as repair
from agent_worktrees import launch_seed_state, tracking, worktree_creation
from test_creation_tail_repair import _creation_fixture, _run


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


def test_changed_legacy_config_spelling_keeps_proven_original_pair_owner(tmp_path, monkeypatch, capsys):
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
    assert parent.machine == child.machine == original_machine
    assert parent.machine_identity is None and child.machine_identity is None
    assert parent.pair_ref == f"{original_machine}/knowledge/{child.worktree_id}"
    assert child.pair_ref == f"{original_machine}/harness/{parent.worktree_id}"
    assert launch_seed_state.state_path(parent.yaml_path).read_bytes() == seed_before


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
