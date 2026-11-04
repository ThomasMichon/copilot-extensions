"""Pre-export policy, current placement and private staging govern the composed flow."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError

from agent_logger import configuration_layers
from agent_logger.aggregate import Admission, ExecutionMode
from agent_logger.sync import worktree_state_pipeline as pipeline
from agent_logger.sync.targets.filesystem import OneDriveTarget
from agent_logger.sync.worktree_state import WorktreeStateError, WorktreeStateReceipt
from agent_logger.sync.worktree_state_providers import (
    EffortsExporter,
    ManagedPlacement,
    OwnerScope,
    ProjectInventory,
    read_project_inventory,
)

from .conftest import init_git_repo
from .test_worktree_state import _PRODUCE, IDENTITY, Packet
from .test_worktree_state import packet as packet
from .test_worktree_state_providers import (
    _RETAINED,
)
from .test_worktree_state_providers import (
    efforts_payload as efforts_payload,
)
from .test_worktree_state_providers import (
    native_owner as native_owner,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX collection adapter")


def selection(home: Path) -> tuple[Admission, ProjectInventory, ManagedPlacement]:
    admission = Admission(IDENTITY.repository, home.parent / "repository")
    placement = ManagedPlacement(
        IDENTITY.worktree,
        IDENTITY.creation_nonce,
        IDENTITY.store_id,
        "main",
        1,
        home.parent / "managed-home",
        "missing",
    )
    inventory = ProjectInventory(
        admission.repository,
        "registered-alias",
        "network:" + admission.repository,
        (placement,),
        OwnerScope("registered-alias", "owner-id", None, 1, 2),
    )
    return admission, inventory, placement


@pytest.mark.parametrize("change", ["privacy", "unadmitted", "observe", "unsupported"])
def test_actual_composition_refuses_before_discovery_export_or_staging(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    cfg, target, plan, _ = packet
    root = target._root()
    admission, inventory, placement = selection(cfg.home)
    if change == "privacy":
        cfg._data["sync"]["redact_secrets"] = True
    elif change == "unadmitted":
        plan.claims = []
    elif change == "observe":
        plan.mode = ExecutionMode.OBSERVE
    else:
        cfg._data["sync"]["target"] = "onedrive"
        target = OneDriveTarget({"root": str(cfg.home.parent / "cloud")})
    for name in ("read_project_inventory", "select_efforts_exporter", "_export_directory"):
        monkeypatch.setattr(
            pipeline, name, lambda *args, **kwargs: pytest.fail("unadmitted effect")
        )
    with pytest.raises(WorktreeStateError):
        pipeline.collect_managed_worktree_state(
            cfg,
            target,
            plan,
            admission,
            inventory,
            placement,
            OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5),
        )
    assert not cfg.home.exists() and not root.exists()


@pytest.mark.parametrize("moment", ["before-export", "after-export"])
@pytest.mark.parametrize("change", ["scope", "placement"])
def test_changed_owner_scope_or_placement_never_publishes_stale_export(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
    moment: str,
    change: str,
) -> None:
    cfg, target, plan, source = packet
    admission, inventory, placement = selection(cfg.home)
    changed = (
        replace(inventory, scope=replace(inventory.scope, repository_id="replaced-owner"))
        if change == "scope"
        else replace(
            inventory,
            placements=(
                replace(placement, placement_generation=2, state_home=source.parent / "moved"),
            ),
        )
    )
    observations = iter([changed] if moment == "before-export" else [inventory, changed])
    monkeypatch.setattr(
        pipeline, "read_project_inventory", lambda *args, **kwargs: next(observations)
    )
    monkeypatch.setattr(
        pipeline,
        "select_efforts_exporter",
        lambda *args, **kwargs: EffortsExporter("efforts@fixture", source / "export.py"),
    )
    exports = []

    class Client:
        def __init__(self, *args: Any) -> None:
            pass

        def export(self, directory: Path, *args: Any) -> Any:
            exports.append(directory)
            for name in ("manifest.json", "agenda.sqlite3"):
                (directory / name).write_bytes((source / name).read_bytes())
            return IDENTITY

    monkeypatch.setattr(pipeline, "EffortsExportClient", Client)
    monkeypatch.setattr(
        pipeline,
        "collect_worktree_state",
        lambda *args, **kwargs: pytest.fail("changed placement reached publication"),
    )
    with pytest.raises(WorktreeStateError, match="scope changed"):
        pipeline.collect_managed_worktree_state(
            cfg,
            target,
            plan,
            admission,
            inventory,
            placement,
            OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5),
        )
    assert not target._root().exists()
    if moment == "before-export":
        assert exports == [] and not cfg.home.exists()
    else:
        assert len(exports) == 1
        assert (exports[0] / "agenda.sqlite3").read_bytes() == (
            source / "agenda.sqlite3"
        ).read_bytes()


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "failure",
        "cleanup",
        "unexpected-member",
        "prepared-manifest",
        "changed-prepared",
    ],
)
def test_staging_cleanup_is_exact_and_failed_or_uncertain_exports_are_retained(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    cfg, target, plan, source = packet
    admission, inventory, placement = selection(cfg.home)
    monkeypatch.setattr(pipeline, "read_project_inventory", lambda *args, **kwargs: inventory)
    monkeypatch.setattr(
        pipeline,
        "select_efforts_exporter",
        lambda *args, **kwargs: EffortsExporter("efforts@fixture", source / "export.py"),
    )
    cleanup = OwnedCleanupError(None)
    exports = []
    receipt = WorktreeStateReceipt(IDENTITY, str(target._root()), "a" * 64, "b" * 64, 1)

    class Client:
        verify = contributors = None

        def __init__(self, *args: Any) -> None:
            pass

        def export(self, directory: Path, *args: Any) -> Any:
            exports.append(directory)
            for name in ("manifest.json", "agenda.sqlite3"):
                (directory / name).write_bytes((source / name).read_bytes())
            if outcome == "unexpected-member":
                (directory / "unknown-evidence").write_bytes(b"retain")
            elif outcome in ("prepared-manifest", "changed-prepared"):
                (directory / "manifest.pending.json").write_bytes(
                    (source / "manifest.json").read_bytes()
                    if outcome == "prepared-manifest"
                    else b"changed",
                )
            return IDENTITY

    def collect(*args: Any, **kwargs: Any) -> WorktreeStateReceipt:
        if outcome == "failure":
            raise WorktreeStateError("injected refusal")
        if outcome == "cleanup":
            raise cleanup
        return receipt

    monkeypatch.setattr(pipeline, "EffortsExportClient", Client)
    monkeypatch.setattr(pipeline, "collect_worktree_state", collect)
    arguments = (
        cfg,
        target,
        plan,
        admission,
        inventory,
        placement,
        OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5),
    )
    if outcome in ("success", "prepared-manifest"):
        assert pipeline.collect_managed_worktree_state(*arguments) == receipt
        assert not exports[0].exists()
    else:
        with pytest.raises(
            OwnedCleanupError if outcome == "cleanup" else WorktreeStateError
        ) as caught:
            pipeline.collect_managed_worktree_state(*arguments)
        if outcome == "cleanup":
            assert caught.value is cleanup
        assert (exports[0] / "agenda.sqlite3").read_bytes() == (
            source / "agenda.sqlite3"
        ).read_bytes()
        if outcome == "unexpected-member":
            assert (exports[0] / "unknown-evidence").read_bytes() == b"retain"


@pytest.mark.contract("managed-worktree-export-collection")
def test_actual_native_retired_placement_exports_and_collects_under_current_policy(
    packet: Packet,
    native_owner: Path,
    efforts_payload: tuple[Path, Path, Path],
) -> None:
    cfg, target, plan, source = packet
    _, root, script = efforts_payload
    init_git_repo(root, remote="https://" + IDENTITY.repository + ".git")
    (native_owner / "projects.yaml").write_text(
        yaml.safe_dump({"projects": {"registered-alias": {}}}),
        encoding="utf-8",
    )
    (native_owner / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "registered-alias": {
                        "remote": "https://" + IDENTITY.repository + ".git",
                        **{platform: str(root) for platform in ("linux", "wsl", "windows")},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    prefix, environment, _ = configuration_layers.worktrees_owner_command(cfg.home)
    produced = subprocess.run(
        [prefix[0], "-B", "-c", _RETAINED],
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert produced.returncode == 0, produced.stderr
    admission = Admission(IDENTITY.repository, root)
    inventory = read_project_inventory(admission, cfg.home)
    placement = inventory.placements[0]
    initial = source.parent / "seed"
    initial.mkdir(mode=0o700)
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _PRODUCE,
            str(script.parent),
            str(placement.state_home),
            str(initial),
            placement.worktree,
            placement.store_id,
            "rich",
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    before = {path: path.read_bytes() for path in native_owner.parent.rglob("*") if path.is_file()}
    receipt = pipeline.collect_managed_worktree_state(
        cfg,
        target,
        plan,
        admission,
        inventory,
        placement,
        OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5),
    )
    assert receipt.identity.worktree == placement.worktree
    assert receipt.identity.revision == 5 and receipt.contributors_sha256 is not None
    assert not receipt.as_dict()["remote_archive_acknowledged"]
    retained = target._root() / receipt.identity.relative_directory
    assert (retained / "export/agenda.sqlite3").read_bytes() == (
        initial / "agenda.sqlite3"
    ).read_bytes()
    assert list((cfg.home / "worktree-state-exports").iterdir()) == []
    assert {
        path: path.read_bytes() for path in native_owner.parent.rglob("*") if path.is_file()
    } == before
