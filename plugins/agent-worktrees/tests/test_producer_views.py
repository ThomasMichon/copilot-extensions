"""Strict local ownership projection, corruption and accepted-transfer coverage."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from agent_worktrees import claim_handoffs, config as cfg, installer, tracking
from agent_worktrees import producer_views_cli
from agent_worktrees.producer_view_scan import scan_ownership
from agent_worktrees.producer_views import ObservedClaim, ownership_snapshot
from agent_worktrees.tracking_producer_write import apply_resource_producer


@pytest.fixture
def ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    registry = tmp_path / "projects.yaml"
    registry.write_text("projects:\n  p: {}\n  q: {}\n", encoding="utf-8")
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: registry)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / (name or "p"))
    config = cfg.Config(
        srcroot=str(tmp_path), machine="m", platform="linux", repo_name="p",
        repos={"p": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path / "p" / "worktrees")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    for project in ("p", "q"):
        records = tmp_path / project / "worktrees"
        records.mkdir(parents=True)
        for name in ("owner", "consumer", "last"):
            tracking.create_new_record(name, f"worktree/{name}", str(tmp_path / "trees" / name), project, "m", "linux", records)
    return tmp_path


def operation(root: Path, name: str, payload: dict, owner: str = "owner") -> dict:
    result = apply_resource_producer({
        "schema": "copilot-extensions.resource-producer", "version": 1,
        "yaml_path": str(root / "p" / "worktrees" / f"{owner}.yaml"),
        "owner_ref": f"m/p/{owner}", "operation": name, "input": payload,
    })
    assert result.get("ok"), result
    return result


def reserve(root: Path, *, bound: bool = False) -> dict:
    result = operation(root, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {}})
    if bound:
        permit = operation(root, "begin", {"token": result["token"]})
        result = operation(root, "bind", {
            "token": result["token"], "allocation_fence": permit["allocation_fence"],
            "identity": {"id": "immutable", "daemon": "opaque"},
        })
    return result


def mutate(root: Path, change: Callable[[dict], object], *, project: str = "p", owner: str = "owner") -> None:
    path = root / project / "worktrees" / f"{owner}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_real_pending_allocating_bound_and_read_only(ledgers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = reserve(ledgers)
    for phase in ("pending", "allocating", "bound"):
        if phase == "allocating":
            permit = operation(ledgers, "begin", {"token": result["token"]})
        elif phase == "bound":
            operation(ledgers, "bind", {"token": result["token"], "allocation_fence": permit["allocation_fence"], "identity": {"id": "i"}})
        before = {path: path.read_bytes() for path in ledgers.rglob("*") if path.is_file()}
        with monkeypatch.context() as patch:
            from agent_worktrees import claim_providers
            import subprocess
            patch.setattr(tracking, "load_record", lambda *a, **k: pytest.fail("cached record read"))
            patch.setattr(tracking, "save_record", lambda *a, **k: pytest.fail("snapshot wrote"))
            patch.setattr(claim_providers, "discover_claim_providers", lambda *a, **k: pytest.fail("provider discovery"))
            patch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("snapshot spawned"))
            snapshot = scan_ownership(owner="m/p/owner")
        assert snapshot["complete"] and snapshot["entries"][0]["phase"] == phase
        assert snapshot["entries"][0]["current_owner"] == "m/p/owner"
        assert snapshot["cleanup_authority"] is snapshot["absence_proof"] is False
        assert before == {path: path.read_bytes() for path in ledgers.rglob("*") if path.is_file()}


def test_real_transfer_chain_preserves_creator_and_current_owner(ledgers: Path) -> None:
    bound = reserve(ledgers, bound=True)
    for source, consumer in (("owner", "consumer"), ("consumer", "last")):
        bundle, _ = claim_handoffs.offer(f"m/p/{source}", f"m/p/{consumer}", [bound["ref"]], machine="m")
        claim_handoffs.accept(bundle.bundle_id, actor=f"m/p/{consumer}", machine="m")
    snapshot = scan_ownership()
    assert snapshot["complete"], snapshot["issues"]
    assert len(snapshot["entries"]) == 3
    assert {row["creator"] for row in snapshot["entries"]} == {"m/p/owner"}
    assert {row["current_owner"] for row in snapshot["entries"]} == {"m/p/last"}
    assert [row["record_owner"] for row in snapshot["entries"] if row["resolution"] == "current"] == ["m/p/last"]
    only_creator = scan_ownership(owner="m/p/owner")
    assert only_creator["entries"][0]["current_owner"] == "m/p/last"


@pytest.mark.parametrize("damage", ["missing", "marker", "source", "mismatch", "cycle"])
def test_interrupted_or_invalid_transfer_is_unknown(ledgers: Path, damage: str) -> None:
    bound = reserve(ledgers, bound=True)
    bundle, _ = claim_handoffs.offer("m/p/owner", "m/p/consumer", [bound["ref"]], machine="m")
    claim_handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m")
    def change(data: dict) -> None:
        claim = data["resources"][0]
        if damage == "missing":
            data["resources"] = []
        elif damage == "marker":
            claim["handoff_bundle"] = bundle.bundle_id
        elif damage == "mismatch":
            claim["kind"] = "volume"
        elif damage == "source":
            claim["producer"]["transfer"]["source"] = "m/p/last"
        elif damage == "cycle":
            claim["producer"]["phase"] = "transferred"
            claim["producer"]["transfer"] = {"accepted": True, "bundle": "cycle", "source": "m/p/consumer", "consumer": "m/p/owner"}
    mutate(ledgers, change, owner="owner" if damage == "source" else "consumer")
    snapshot = scan_ownership(owner="m/p/owner")
    assert not snapshot["complete"] and snapshot["issues"]
    assert all(row["current_owner"] is None for row in snapshot["entries"])


def test_cross_project_identity_collision_not_hidden_by_filter(ledgers: Path) -> None:
    result = reserve(ledgers, bound=True)
    def duplicate(data: dict) -> None:
        metadata = copy.deepcopy(result["producer"])
        metadata["token"] = "x" * 40
        metadata["creator"] = "m/q/owner"
        from agent_worktrees.resource_producer import claim_ref
        data["resources"] = [{"kind": "container", "ref": claim_ref(metadata), "producer": metadata}]
    mutate(ledgers, duplicate, project="q")
    snapshot = scan_ownership(owner="m/p/owner", identity={"daemon": "opaque", "id": "immutable"})
    assert not snapshot["complete"]
    assert "duplicate-identity" in {item["code"] for item in snapshot["issues"]}
    assert len(snapshot["entries"]) == 1 and snapshot["entries"][0]["current_owner"] is None


@pytest.mark.parametrize("resources", [
    None, {}, [None], [""], [{"ref": ""}], [{"ref": 1}],
    [{"ref": "x", "producer": None}], [{"ref": "x", "kind": ""}],
    [{"ref": "x", "state": []}], [{"ref": "x", "handoff_bundle": []}],
])
def test_raw_malformed_claims_are_not_silently_skipped(ledgers: Path, resources: object) -> None:
    mutate(ledgers, lambda data: data.update(resources=resources))
    snapshot = scan_ownership()
    assert not snapshot["complete"] and snapshot["resolution"] == "unknown"


@pytest.mark.parametrize("content", ["", "{}", "projects:", "projects: []", "projects: {p: {}, p: {}}", "projects: {../escape: {}}"])
def test_registry_missing_or_malformed_is_unknown(ledgers: Path, content: str) -> None:
    (ledgers / "projects.yaml").write_text(content, encoding="utf-8")
    assert not scan_ownership()["complete"]


def test_explicit_empty_registry_is_only_not_observed(ledgers: Path) -> None:
    (ledgers / "projects.yaml").write_text("projects: {}", encoding="utf-8")
    snapshot = scan_ownership()
    assert snapshot["complete"] and snapshot["resolution"] == "not-observed"
    assert not snapshot["absence_proof"] and not snapshot["cleanup_authority"]


@pytest.mark.parametrize("field,value", [("machine", None), ("repo", "other"), ("worktree_id", "other"), ("branch", [])])
def test_record_identity_damage_is_explicit(ledgers: Path, field: str, value: object) -> None:
    mutate(ledgers, lambda data: data.update({field: value}))
    assert not scan_ownership()["complete"]


def test_duplicate_yaml_keys_and_duplicate_claims_are_unknown(ledgers: Path) -> None:
    reserve(ledgers)
    mutate(ledgers, lambda data: data["resources"].append(copy.deepcopy(data["resources"][0])))
    assert "duplicate-token" in {issue["code"] for issue in scan_ownership()["issues"]}
    path = ledgers / "p" / "worktrees" / "consumer.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\nresources: []\nresources: []\n", encoding="utf-8")
    assert "record-unavailable" in {issue["code"] for issue in scan_ownership()["issues"]}


def test_projection_invalid_recursive_metadata_cannot_break_wire_output() -> None:
    recursive: dict = {}
    recursive["cycle"] = recursive
    snapshot = ownership_snapshot([ObservedClaim("m/p/w", "container", "x", "active", "", recursive)], [])
    assert not snapshot["complete"]
    assert json.loads(json.dumps(snapshot))["entries"][0]["producer"] == {"error": "invalid-producer-metadata"}


@pytest.mark.parametrize("identity", ['{"id":1,"id":2}', '{"id":NaN}', "[]", "[" * 1500], ids=["duplicate", "nan", "list", "depth"])
def test_cli_rejects_invalid_identity_without_scanning(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture, identity: str) -> None:
    monkeypatch.setattr(producer_views_cli, "scan_ownership", lambda **k: pytest.fail("invalid input scanned"))
    assert producer_views_cli.cmd_ownership(argparse.Namespace(json=True, identity_json=identity), []) == 2
    assert "error" in json.loads(capfd.readouterr().out)


def test_cli_strict_snapshot_failure_is_nonzero(ledgers: Path, capfd: pytest.CaptureFixture) -> None:
    mutate(ledgers, lambda data: data.update(resources=None))
    assert producer_views_cli.cmd_ownership(argparse.Namespace(json=True), []) == 1
    assert json.loads(capfd.readouterr().out)["resolution"] == "unknown"


def test_real_closed_receipt_is_history_not_current_owner(ledgers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import claim_providers
    result = reserve(ledgers, bound=True)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: {
        "available": True, "exists": False,
        "producer_verification": {"version": 1, "subject": ref, "absent": True},
    })
    operation(ledgers, "close", {"token": result["token"]})
    snapshot = scan_ownership(identity=result["producer"]["identity"])
    assert snapshot["complete"]
    assert snapshot["entries"][0]["resolution"] == "history"
    assert snapshot["entries"][0]["current_owner"] is None
    assert snapshot["entries"][0]["producer"]["closeout"]["exists"] is False


@pytest.mark.parametrize("target", ["registry", "directory", "record"])
def test_missing_input_cannot_be_a_clean_empty_snapshot(ledgers: Path, target: str) -> None:
    from agent_worktrees import producer_view_scan as scanner
    if target == "registry":
        path = ledgers / "projects.yaml"
    elif target == "directory":
        path = ledgers / "p" / "worktrees"
    else:
        path = ledgers / "p" / "worktrees" / "owner.yaml"
    # A record vanishing between enumeration and read must remain visible.
    original = scanner._read
    def missing(candidate: Path) -> tuple[object, tuple[int, ...]]:
        if candidate == path:
            raise FileNotFoundError("fixture input vanished")
        return original(candidate)
    if target == "directory":
        path.rename(path.with_name("unavailable"))
        snapshot = scan_ownership()
    else:
        from unittest.mock import patch
        with patch.object(scanner, "_read", missing):
            snapshot = scan_ownership()
    assert not snapshot["complete"] and snapshot["issues"]


def test_malformed_repository_remote_does_not_abort_the_whole_scan(
    ledgers: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cfg.project_dir() can reach project_state._normalized_repository_remote(),
    which raises AttributeError on a namespaced installation-cell's malformed
    (e.g. null) repository remote. That must become a per-project
    'project-unavailable' finding, not an uncaught crash that stops the scan
    before the remaining registered projects are read (copilot-extensions
    PR #7102)."""
    reserve(ledgers)
    real_project_dir = cfg.project_dir
    def project_dir(name: str | None = None) -> Path:
        if name == "q":
            raise AttributeError("'NoneType' object has no attribute 'strip'")
        return real_project_dir(name)
    monkeypatch.setattr(cfg, "project_dir", project_dir)
    snapshot = scan_ownership()
    assert not snapshot["complete"]
    issues = {(i["code"], i["subject"]) for i in snapshot["issues"]}
    assert ("project-unavailable", "q") in issues
    assert snapshot["entries"], "project 'p' must still be scanned and reported"


@pytest.mark.parametrize("target", ["registry", "directory", "record"])
def test_symlink_inputs_are_explicit_uncertainty(ledgers: Path, target: str) -> None:
    path = (
        ledgers / "projects.yaml" if target == "registry" else
        ledgers / "p" / "worktrees" if target == "directory" else
        ledgers / "p" / "worktrees" / "owner.yaml"
    )
    real = path.with_name(path.name + "-real")
    path.rename(real)
    try:
        path.symlink_to(real, target_is_directory=real.is_dir())
    except OSError:
        pytest.skip("symlink creation unavailable on this platform")
    assert not scan_ownership()["complete"]


def test_read_error_in_another_project_cannot_be_filtered_away(ledgers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import producer_view_scan as scanner
    reserve(ledgers)
    original = scanner._read
    def unreadable(path: Path) -> tuple[object, tuple[int, ...]]:
        if path.parent.parent.name == "q":
            raise PermissionError("fixture cannot read q")
        return original(path)
    monkeypatch.setattr(scanner, "_read", unreadable)
    snapshot = scan_ownership(owner="m/p/owner")
    assert not snapshot["complete"] and len(snapshot["entries"]) == 1
    assert snapshot["entries"][0]["current_owner"] is None


@pytest.mark.parametrize("changed", ["registry", "directory", "record"])
def test_input_replacement_during_scan_is_explicit(ledgers: Path, monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    from agent_worktrees import producer_view_scan as scanner
    reserve(ledgers)
    target = (
        ledgers / "projects.yaml" if changed == "registry" else
        ledgers / "p" / "worktrees" if changed == "directory" else
        ledgers / "p" / "worktrees" / "owner.yaml"
    )
    stamp = scanner._stamp
    calls = 0
    def replacement(path: Path, *, directory: bool = False) -> tuple[int, ...]:
        nonlocal calls
        value = stamp(path, directory=directory)
        if path == target:
            calls += 1
            if calls >= (2 if directory else 3):
                return (*value[:-1], value[-1] + 1)
        return value
    monkeypatch.setattr(scanner, "_stamp", replacement)
    snapshot = scan_ownership()
    assert not snapshot["complete"] and "snapshot-changed" in {item["code"] for item in snapshot["issues"]}


def test_cli_parser_dispatches_filters_without_owner_resolution(ledgers: Path, capfd: pytest.CaptureFixture) -> None:
    from agent_worktrees import claims_cli
    parser = argparse.ArgumentParser()
    claims_cli.add_parsers(parser.add_subparsers(dest="command"))
    reserve(ledgers, bound=True)
    args = parser.parse_args([
        "claims", "ownership", "--owner-ref", "m/p/owner", "--namespace", "raw-resource",
        "--kind", "container", "--identity-json", '{"id":"immutable","daemon":"opaque"}', "--json",
    ])
    assert claims_cli.cmd_claims(args) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["complete"] and result["entries"][0]["record_owner"] == "m/p/owner"
    args.claim_owner_ref = "m/p/owner#session"
    assert claims_cli.cmd_claims(args) == 2


def test_duplicate_pending_token_and_damaged_ref_are_unknown(ledgers: Path) -> None:
    pending = reserve(ledgers)
    def duplicate(data: dict) -> None:
        data["resources"] = [{"kind": "container", "ref": pending["ref"], "producer": pending["producer"]}]
    mutate(ledgers, duplicate, owner="consumer")
    snapshot = scan_ownership()
    assert not snapshot["complete"]
    mutate(ledgers, lambda data: data["resources"][0].update(ref="wrong"))
    assert "invalid-producer" in {item["code"] for item in scan_ownership()["issues"]}


def test_exact_identity_is_not_partial_matching(ledgers: Path) -> None:
    reserve(ledgers, bound=True)
    snapshot = scan_ownership(identity={"id": "immutable"})
    assert snapshot["complete"] and snapshot["entries"] == []
    assert snapshot["resolution"] == "not-observed" and not snapshot["absence_proof"]


@pytest.mark.parametrize("state", ["released", "at-rest", "abandoned", "corrupt"])
@pytest.mark.parametrize("kind", ["container", "volume"])
def test_retention_phase_outweighs_generic_state(ledgers: Path, state: str, kind: str) -> None:
    reserve(ledgers, bound=True)
    mutate(ledgers, lambda data: data["resources"][0].update(state=state, kind=kind))
    snapshot = scan_ownership(kind=kind)
    assert snapshot["complete"] and snapshot["entries"][0]["phase"] == "bound"
    assert snapshot["entries"][0]["current_owner"] == "m/p/owner"
    assert snapshot["entries"][0]["state"] == state


def test_reparse_point_is_not_treated_as_an_ordinary_tracking_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import stat
    from types import SimpleNamespace
    from agent_worktrees import producer_view_scan as scanner
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_file_attributes=0x400))
    with pytest.raises(ValueError, match="reparse-point"):
        scanner._stamp(tmp_path / "owner.yaml")


def test_programmatic_cli_without_json_flag_handles_output_and_errors(ledgers: Path, capfd: pytest.CaptureFixture) -> None:
    assert producer_views_cli.cmd_ownership(argparse.Namespace(), []) == 0
    assert "not-observed" in capfd.readouterr().out
    assert producer_views_cli.cmd_ownership(argparse.Namespace(), ["unexpected"]) == 2
    assert "takes no positional selectors" in capfd.readouterr().out


def test_projection_reuses_canonical_identity_for_selection_and_collisions(ledgers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import resource_producer as producer
    bound = reserve(ledgers, bound=True)
    metadata = bound["producer"]
    identity = metadata["identity"]
    original = producer.canonical_json
    calls = 0
    def encode(value: object) -> str:
        nonlocal calls
        if value is identity:
            calls += 1
        return original(value)
    monkeypatch.setattr(producer, "canonical_json", encode)
    snapshot = ownership_snapshot(
        [ObservedClaim("m/p/owner", "container", bound["ref"], "active", "", metadata)],
        [], identity=copy.deepcopy(identity),
    )
    assert snapshot["complete"] and len(snapshot["entries"]) == 1 and calls == 1


def test_filtered_rows_are_validated_globally_but_not_serialized(ledgers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import resource_producer as producer
    bound = reserve(ledgers, bound=True)
    monkeypatch.setattr(producer, "wire_metadata", lambda value: pytest.fail("filtered row serialized"))
    snapshot = ownership_snapshot(
        [ObservedClaim("m/p/owner", "container", bound["ref"], "active", "", bound["producer"])],
        [], owner="m/p/consumer",
    )
    assert snapshot["complete"] and snapshot["entries"] == []


def test_shared_strict_yaml_loader_has_per_read_labels_and_located_errors() -> None:
    from agent_worktrees.projects_registry import load_projects_document
    from agent_worktrees.strict_yaml import load_strict_yaml_document
    duplicate = "nested:\n  field: 1\n  field: 2\n"
    with pytest.raises(ValueError, match="projects registry at line 3, column 3: 'field'"):
        load_projects_document(duplicate)
    with pytest.raises(ValueError, match="YAML document at line 3, column 3: 'field'"):
        load_strict_yaml_document(duplicate)
    assert load_strict_yaml_document("nested: {field: 1}") == {"nested": {"field": 1}}


@pytest.mark.parametrize("metadata", [
    None, [], "invalid", {"version": True}, {"phase": []}, {"recovery": {1: "value"}},
])
def test_projection_invalid_wire_shapes_are_sentinels_not_exceptions(metadata: object) -> None:
    snapshot = ownership_snapshot([ObservedClaim("m/p/owner", "container", "bad", "active", "", metadata)], [])
    assert not snapshot["complete"] and snapshot["issues"][0]["code"] == "invalid-producer"
    assert snapshot["entries"][0]["producer"] == {"error": "invalid-producer-metadata"}
    assert snapshot["entries"][0]["current_owner"] is None


def test_strict_yaml_unhashable_key_keeps_label_and_location() -> None:
    from agent_worktrees.strict_yaml import load_strict_yaml_document
    with pytest.raises(ValueError, match="unhashable mapping key in fixture at line 1, column 3"):
        load_strict_yaml_document("? [a, b]\n: value\n", label="fixture")


@pytest.mark.parametrize("value", [b"binary", {"set"}, float("nan"), {1: "non-string-key"}])
def test_full_producer_metadata_with_non_json_yaml_cannot_break_projection(value: object) -> None:
    metadata = {
        "version": 1, "lifetime": "disposable-owned", "token": "x" * 40,
        "namespace": "raw-resource", "phase": "pending", "creator": "m/p/owner",
        "recovery": {"value": value},
    }
    snapshot = ownership_snapshot([ObservedClaim("m/p/owner", "container", "bad", "active", "", metadata)], [])
    assert not snapshot["complete"]
    assert snapshot["entries"][0]["producer"] == {"error": "invalid-producer-metadata"}
    json.dumps(snapshot, allow_nan=False)
