"""Ordinary relocation retains original creator namespaces after reporter drift."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, cast

import pytest
import yaml

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import config as cfg, receiving_allocation, record_reporting, tracking
from test_creation_tail_repair import _creation_fixture
from test_identity_rename_ownership import ordinary_child, rename


@dataclass
class CreatorFixture:
    parent: tracking.WorktreeRecord
    child: tracking.WorktreeRecord
    config: cfg.Config
    configs: dict[str, cfg.Config]
    anchors: dict[str, Path]
    original_ref: str


@pytest.fixture(params=[False, True], ids=["nonce-backed", "unversioned"])
def creator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> CreatorFixture:
    create = cast(Callable[..., SimpleNamespace], _creation_fixture)
    creation = create(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    make_child = cast(
        Callable[[SimpleNamespace], tuple[tracking.WorktreeRecord, tracking.WorktreeRecord, str]],
        ordinary_child,
    )
    parent, child, ref = make_child(creation)
    parent.resources[0].state = "at-rest"
    tracking.save_record(parent)
    child.status = "finalized"
    child.owner_ref = (child.owner_ref or "") + "#original-session"
    tracking.save_record(child)
    if request.param:
        retained = yaml.safe_load(child.yaml_path.read_bytes())
        retained.pop("creation_nonce")
        child.yaml_path.write_text(yaml.safe_dump(retained), encoding="utf-8")
    rename(creation)
    configs = cast(dict[str, cfg.Config], creation.configs)
    return CreatorFixture(
        tracking.load_record(parent.yaml_path), tracking.load_record(child.yaml_path),
        configs["knowledge"], configs, cast(dict[str, Path], creation.anchors), ref,
    )


def _classify(fixture: CreatorFixture) -> bool:
    return receiving_allocation.has_retained_receiving_operation(
        fixture.child, fixture.child.yaml_path.parent, lock_snapshot=False,
        unversioned=not bool(fixture.child.creation_nonce),
    )


@pytest.mark.parametrize("different_project_reporter", [False, True])
def test_original_generic_creator_survives_reporter_rename(
    creator: CreatorFixture, monkeypatch: pytest.MonkeyPatch, different_project_reporter: bool,
) -> None:
    if different_project_reporter:
        (creator.anchors["knowledge"] / "machines.yaml").unlink()
        creator.child = tracking.load_record(creator.child.yaml_path)
        assert creator.child.execution_machine != creator.parent.execution_machine
    before = (creator.parent.yaml_path.read_bytes(), creator.child.yaml_path.read_bytes())
    assert not _classify(creator)
    record, proof, _guard = relocation._receiving_admission(
        creator.child.yaml_path, creator.config,
    )
    assert proof is None and record.owner_ref == creator.child.owner_ref
    monkeypatch.setattr(relocation, "_native_supported", lambda: True)
    with pytest.raises(ValueError, match="qualified passive native owner evidence"):
        relocation.plan(
            creator.child.worktree_id,
            str(Path(creator.child.worktree_path).parent / "destination" / creator.child.worktree_id),
            creator.config,
        )
    assert before == (creator.parent.yaml_path.read_bytes(), creator.child.yaml_path.read_bytes())


@pytest.mark.parametrize("project", ["harness", "knowledge"])
def test_explicit_foreign_creator_is_not_reclassified_as_local(
    creator: CreatorFixture, project: str,
) -> None:
    topology = creator.anchors[project] / "machines.yaml"
    data = cast(dict[str, dict[str, object]], yaml.safe_load(topology.read_text(encoding="utf-8")))
    owner = tracking.parse_claim_ref(creator.child.owner_ref or "")
    assert owner is not None and owner.machine
    data["machines"][owner.machine] = {
        "hostname": "another-host", "execution_platform": creator.config.platform,
    }
    topology.write_text(yaml.safe_dump(data), encoding="utf-8")
    creator.child = tracking.load_record(creator.child.yaml_path)
    before = (creator.parent.yaml_path.read_bytes(), creator.child.yaml_path.read_bytes())
    with pytest.raises(ValueError, match="proven local creator|identity is unproven"):
        _classify(creator)
    assert before == (creator.parent.yaml_path.read_bytes(), creator.child.yaml_path.read_bytes())


@pytest.mark.parametrize("condition", ["missing", "duplicate", "unsettled", "opaque", "changed"])
def test_renamed_creator_does_not_weaken_original_subject_or_claim_proof(
    creator: CreatorFixture, condition: str,
) -> None:
    if condition in {"changed", "opaque"}:
        path = creator.child.yaml_path if condition == "changed" else creator.parent.yaml_path
        data = cast(dict[str, object], yaml.safe_load(path.read_text(encoding="utf-8")))
        if condition == "changed":
            data["creation_nonce"] = "different-incarnation"
        else:
            resources = cast(list[dict[str, object]], data["resources"])
            resources[0]["producer"] = {"namespace": "unknown", "version": 99}
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    else:
        if condition == "missing":
            creator.parent.resources = []
        elif condition == "duplicate":
            creator.parent.resources.append(tracking.ResourceClaim(
                kind="worktree", ref=creator.original_ref, state="at-rest",
            ))
        elif condition == "unsettled":
            creator.parent.resources[0].state = "active"
        tracking.save_record(creator.parent)
    before = (creator.parent.yaml_path.read_bytes(), creator.child.yaml_path.read_bytes())
    with pytest.raises(ValueError, match="proven local creator|responsibility is missing, unsettled"):
        _classify(creator)
    assert before == (creator.parent.yaml_path.read_bytes(), creator.child.yaml_path.read_bytes())


def test_unavailable_reporter_does_not_qualify_a_retained_creator(
    creator: CreatorFixture,
) -> None:
    context = creator.child._reporting_context
    assert context is not None
    record_reporting.apply_context(
        creator.child, record_reporting.UnavailableReportingContext(**vars(context)),
    )
    with pytest.raises(ValueError, match="proven local creator|identity is unproven"):
        _classify(creator)


def test_handoff_reservation_is_looked_up_in_the_original_creator_namespace(
    creator: CreatorFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import claim_handoffs

    owner = tracking.parse_claim_ref(creator.child.owner_ref or "")
    assert owner is not None
    source = tracking.format_claim_ref(owner.machine, owner.project, owner.worktree_id)
    registry = tmp_path / "claim-handoffs.yaml"
    bundle = claim_handoffs.ClaimBundle(
        "original-bundle", "offered", source, "custom-space/harness/recipient",
        ({"kind": "worktree", "ref": creator.original_ref},),
        "2020-01-01T00:00:00", "2020-01-01T00:00:00",
    )
    registry.write_text(
        yaml.safe_dump({"version": 1, "bundles": [bundle.to_dict()]}), encoding="utf-8",
    )
    monkeypatch.setattr(claim_handoffs, "registry_path", lambda: registry)
    before = registry.read_bytes()
    with pytest.raises(ValueError, match="handoff-reserved"):
        _classify(creator)
    assert registry.read_bytes() == before
