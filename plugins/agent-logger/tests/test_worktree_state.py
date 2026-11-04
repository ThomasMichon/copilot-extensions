"""Typed export publication, policy refusal and revision-bound local readback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest

from agent_logger.aggregate import (
    Admission, AggregateInputs, Claim, DiscoveredDeclaration, ExecutionMode,
    MachineIdentity, Provenance, RepositoryDeclaration, RepositoryPolicy,
    ResolvedPlan, ResourceBinding, SourceSet, compile_aggregate,
)
from agent_logger.config import Config
from agent_logger.aggregate_machine import default_machine_identity
from agent_logger.sync.targets.filesystem import LocalTarget, OneDriveTarget
from agent_logger.sync.worktree_state import (
    WorktreeStateError, WorktreeStateReceipt, WorktreeStateRevision, canonical_bytes,
)
from agent_logger.sync.worktree_state_archive import (
    collect_worktree_state, readback_worktree_state,
)
from agent_logger.sync.protection import ProtectionSettings

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX collection adapter")
REPOSITORY = "github.com/example/project"
IDENTITY = WorktreeStateRevision(REPOSITORY, "full-worktree-1", "incarnation-1", "a" * 32, 7)
Packet = tuple[Config, LocalTarget, ResolvedPlan, Path]


def plan_for(root: Path, *, mode: ExecutionMode = ExecutionMode.ENFORCE) -> ResolvedPlan:
    checkout = root.parent / "repository"
    provenance = Provenance(path=str(checkout / "config.yaml"), repository=REPOSITORY)
    declaration = RepositoryDeclaration(
        1, REPOSITORY, provenance, RepositoryPolicy("default", claims=(
            Claim("source", SourceSet(repositories=frozenset({REPOSITORY})),
                  collection_target="archive"),
        )),
    )
    return compile_aggregate(AggregateInputs(
        machine=replace(default_machine_identity(), name="worker"), mode=mode,
        admissions=(Admission(REPOSITORY, checkout, collection_targets={
            "archive": ResourceBinding("filesystem", str(root)),
        }),),
        discoveries=(DiscoveredDeclaration(checkout, REPOSITORY, declaration),),
    ))


def fake_owner(directory: Path, *, worktree: str, store_id: str, expected_revision: int) -> dict:
    """Transport fixture only; real SQLite semantics belong to the owner packet."""
    manifest = json.loads((directory / "manifest.json").read_bytes())
    raw = (directory / "agenda.sqlite3").read_bytes()
    if (
        manifest["worktree"] != worktree or manifest["store_id"] != store_id
        or manifest["revision"] != expected_revision
        or manifest["members"]["agenda.sqlite3"] != {
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        }
    ):
        raise WorktreeStateError("owner fixture rejected export identity or content")
    return manifest


@pytest.fixture
def packet(tmp_path: Path) -> Packet:
    root = tmp_path / "archive"
    cfg = Config({"sync": {"target": "local", "targets": {"local": {"path": str(root)}}},
                  "machine": {"name": "worker"}},
                 home=tmp_path / "logger-home")
    target = LocalTarget({"path": str(root)})
    source = tmp_path / "export"
    source.mkdir(mode=0o700)
    raw = b"synthetic exported ledger; not a live database"
    (source / "agenda.sqlite3").write_bytes(raw)
    (source / "manifest.json").write_bytes(canonical_bytes({
        "schema": "efforts.agenda-export", "version": 1, "completeness": "complete",
        "worktree": IDENTITY.worktree, "store_id": IDENTITY.store_id,
        "revision": IDENTITY.revision,
        "members": {"agenda.sqlite3": {
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        }},
    }))
    return cfg, target, plan_for(root), source


def collect(packet: Packet, *, sessions: tuple[str, ...] = ("session-1",)) -> WorktreeStateReceipt:
    cfg, target, plan, source = packet
    return collect_worktree_state(cfg, target, plan, source, IDENTITY, sessions, fake_owner)


def test_one_revision_many_session_references_and_fresh_readback(packet: Packet) -> None:
    cfg, target, plan, source = packet
    before = {item.name: item.read_bytes() for item in source.iterdir()}
    receipt = collect(packet, sessions=("session-2", "session-1", "session-2"))
    assert collect(packet, sessions=("session-3",)) == receipt
    revision = target._root() / IDENTITY.relative_directory
    assert len(list((revision / "references").glob("*.json"))) == 3
    references = [json.loads(item.read_bytes()) for item in (revision / "references").iterdir()]
    assert {item["session"] for item in references} == {"session-1", "session-2", "session-3"}
    assert all(item["identity"] == IDENTITY.as_dict() for item in references)
    assert len(list(target._root().rglob("agenda.sqlite3"))) == 1
    assert {item.name: item.read_bytes() for item in source.iterdir()} == before
    assert readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner) == receipt
    assert receipt.as_dict()["local_readback_verified"] is True
    assert receipt.as_dict()["remote_archive_acknowledged"] is False
    assert receipt.as_dict()["reclamation_authorized"] is False


@pytest.mark.parametrize("policy", [
    {"redact_secrets": True}, {"session_files": "essential"},
    {"repo_allowlist": ["project"]}, {"repo_denylist": ["other"]},
    {"require_repo_opt_in": True},
])
def test_actual_privacy_and_legacy_scope_refuse_before_owner_read_or_write(
    packet: Packet, policy: dict[str, Any],
) -> None:
    cfg, target, plan, source = packet
    cfg._data["sync"].update(policy)

    def forbidden(*args: Any, **kwargs: Any) -> dict:
        pytest.fail("unadmitted collection reached the owner")

    with pytest.raises(WorktreeStateError):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, ("session-1",), forbidden)
    assert not target._root().exists()


@pytest.mark.parametrize("change", ["observe", "passive", "unauthorized", "wrong-source",
                                  "wrong-destination", "unready", "profile", "retention",
                                  "wrong-machine"])
def test_diagnostic_plans_and_unimplemented_policies_never_admit_effects(
    packet: Packet, change: str,
) -> None:
    cfg, target, plan, source = packet
    if change == "observe":
        plan.mode = ExecutionMode.OBSERVE
    elif change == "passive":
        plan.passive = True
    elif change == "unauthorized":
        plan.authorized = False
    elif change == "wrong-machine":
        plan.machine = MachineIdentity("another-worker", "linux")
    else:
        claim = plan.claims[0]
        if change == "wrong-source":
            claim = replace(claim, sources=SourceSet(
                repositories=frozenset({"github.com/example/other"}),
            ))
        elif change == "wrong-destination":
            claim = replace(claim, resource=ResourceBinding("filesystem", str(source.parent / "other")))
        elif change == "unready":
            claim = replace(claim, resource=replace(claim.resource, ready=False))
        else:
            claim = replace(claim, **{change: "unsupported"})
        plan.claims = [claim]
    with pytest.raises(WorktreeStateError):
        collect(packet)
    assert not target._root().exists()


def test_cloud_target_does_not_inherit_local_collection_capability(packet: Packet) -> None:
    cfg, target, plan, source = packet
    cfg._data["sync"]["target"] = "onedrive"
    cloud = OneDriveTarget({"root": str(source.parent / "cloud")})
    with pytest.raises(WorktreeStateError, match="does not support"):
        collect_worktree_state(cfg, cloud, plan, source, IDENTITY, ("session-1",), fake_owner)
    assert not (source.parent / "cloud").exists()


@pytest.mark.parametrize("kind", ["database", "receipt", "manifest", "source-revision"])
def test_receipt_is_not_a_cache_for_tampered_or_changed_bytes(packet: Packet, kind: str) -> None:
    cfg, target, plan, source = packet
    collect(packet)
    revision = target._root() / IDENTITY.relative_directory
    if kind == "database":
        (revision / "export/agenda.sqlite3").write_bytes(b"truncated")
    elif kind == "receipt":
        (revision / "receipt.json").write_bytes(b"{}")
    elif kind == "manifest":
        (revision / "export/manifest.json").write_bytes(b"{}")
    else:
        manifest = json.loads((source / "manifest.json").read_bytes())
        manifest["revision"] += 1
        (source / "manifest.json").write_bytes(canonical_bytes(manifest))
    with pytest.raises((WorktreeStateError, KeyError)):
        if kind == "source-revision":
            collect(packet)
        else:
            readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner)


def test_same_revision_conflicting_export_and_partial_collection_are_retained(packet: Packet) -> None:
    cfg, target, plan, source = packet
    collect(packet)
    revision = target._root() / IDENTITY.relative_directory
    original = (revision / "export/agenda.sqlite3").read_bytes()
    changed = b"same identity, different accepted bytes"
    manifest = json.loads((source / "manifest.json").read_bytes())
    (source / "agenda.sqlite3").write_bytes(changed)
    manifest["members"]["agenda.sqlite3"] = {
        "bytes": len(changed), "sha256": hashlib.sha256(changed).hexdigest(),
    }
    (source / "manifest.json").write_bytes(canonical_bytes(manifest))
    with pytest.raises(WorktreeStateError, match="conflicts"):
        collect(packet)
    assert (revision / "export/agenda.sqlite3").read_bytes() == original
    partial_identity = replace(IDENTITY, revision=8)
    partial = target._root() / partial_identity.relative_directory
    partial.mkdir(parents=True)
    (partial / "collection.json").write_bytes(b'{"foreign":true}')
    manifest["revision"] = 8
    (source / "manifest.json").write_bytes(canonical_bytes(manifest))
    with pytest.raises(WorktreeStateError, match="conflicts"):
        collect_worktree_state(cfg, target, plan, source, partial_identity, ("session-1",),
                              fake_owner)
    assert (partial / "collection.json").read_bytes() == b'{"foreign":true}'


def test_failed_owner_readback_never_issues_receipt_or_overwrites_partial_bytes(packet: Packet) -> None:
    cfg, target, plan, source = packet

    def fail_copy(directory: Path, **selection: Any) -> dict:
        if directory != source:
            raise WorktreeStateError("injected destination owner failure")
        return fake_owner(directory, **selection)

    with pytest.raises(WorktreeStateError, match="injected"):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, ("session-1",), fail_copy)
    revision = target._root() / IDENTITY.relative_directory
    assert not (revision / "receipt.json").exists()
    retained = {item.name: item.read_bytes() for item in (revision / "export").iterdir()}
    with pytest.raises(WorktreeStateError, match="incomplete"):
        collect(packet)
    assert {item.name: item.read_bytes() for item in (revision / "export").iterdir()} == retained


def test_source_and_destination_aliases_do_not_publish_through_links(packet: Packet) -> None:
    cfg, target, plan, source = packet
    linked_source = source.parent / "linked-export"
    linked_source.symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        collect_worktree_state(cfg, target, plan, linked_source, IDENTITY, ("session-1",), fake_owner)
    outside = source.parent / "outside"
    outside.mkdir()
    target._root().symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        collect(packet)
    assert list(outside.iterdir()) == []


def test_missing_readback_does_not_create_destination(packet: Packet) -> None:
    cfg, target, plan, _ = packet
    with pytest.raises(WorktreeStateError, match="missing or unowned"):
        readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner)
    assert not target._root().exists()


def test_same_revision_racing_publishers_never_duplicate_or_overwrite(packet: Packet) -> None:
    def publish(session: str) -> WorktreeStateReceipt | None:
        try:
            return collect(packet, sessions=(session,))
        except (OSError, WorktreeStateError):
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        receipts = list(workers.map(publish, ("session-1", "session-2")))
    assert any(receipts)
    receipt = collect(packet, sessions=("session-1", "session-2"))
    assert all(value is None or value == receipt for value in receipts)
    revision = packet[1]._root() / IDENTITY.relative_directory
    assert len(list(packet[1]._root().rglob("agenda.sqlite3"))) == 1
    assert len(list((revision / "references").glob("*.json"))) == 2


def test_source_changes_after_owner_verification_never_issue_a_receipt(packet: Packet) -> None:
    cfg, target, plan, source = packet

    def changed(directory: Path, **selection: Any) -> dict:
        manifest = fake_owner(directory, **selection)
        (directory / "agenda.sqlite3").write_bytes(b"changed after owner verification")
        return manifest

    with pytest.raises(WorktreeStateError, match="changed"):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, ("session-1",), changed)
    assert not list(target._root().rglob("receipt.json"))


def test_changed_privacy_rejects_receipt_readback_before_the_owner(packet: Packet) -> None:
    cfg, target, plan, _ = packet
    collect(packet)
    cfg._data["sync"]["redact_secrets"] = True

    def forbidden(*args: Any, **kwargs: Any) -> dict:
        pytest.fail("readback ignored the current privacy policy")

    with pytest.raises(WorktreeStateError, match="privacy"):
        readback_worktree_state(cfg, target, plan, IDENTITY, forbidden)


@pytest.mark.parametrize("kind", ["role", "platform", "malformed-role", "malformed-machine"])
def test_complete_current_machine_identity_is_required_before_any_owner_read(
    packet: Packet, kind: str,
) -> None:
    cfg, target, plan, source = packet
    if kind == "role":
        cfg._data["machine"]["role"] = "different-role"
    elif kind == "platform":
        plan.machine = replace(plan.machine, platform="different-platform")
    elif kind == "malformed-role":
        cfg._data["machine"]["role"] = 12
    else:
        cfg._data["machine"] = []

    def forbidden(*args: Any, **kwargs: Any) -> dict:
        pytest.fail("stale or malformed machine identity reached the owner")

    with pytest.raises(ValueError):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, ("session-1",), forbidden)
    assert not target._root().exists()


def test_stale_target_and_case_folded_claim_cannot_admit_another_native_root(
    packet: Packet,
) -> None:
    cfg, target, plan, source = packet

    def forbidden(*args: Any, **kwargs: Any) -> dict:
        pytest.fail("unselected or case-distinct destination reached the owner")

    configured = cfg._data["sync"]["targets"]["local"]
    configured["path"] = str(source.parent / "new-approved-destination")
    with pytest.raises(WorktreeStateError, match="current sync configuration"):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, ("session-1",), forbidden)
    configured["path"] = str(target._root())
    old_binding = plan.claims[0].resource
    different_case = target._root().with_name(target._root().name.upper())
    new_binding = ResourceBinding("filesystem", str(different_case))
    assert new_binding.canonical_identity == old_binding.canonical_identity
    plan.claims = [replace(plan.claims[0], resource=new_binding)]
    with pytest.raises(WorktreeStateError, match="admitted collection destination"):
        collect_worktree_state(cfg, target, plan, source, IDENTITY, ("session-1",), forbidden)
    assert not target._root().exists() and not different_case.exists()


def test_readback_requalifies_changed_role_and_configured_destination(packet: Packet) -> None:
    cfg, target, plan, _ = packet
    collect(packet)
    cfg._data["machine"]["role"] = "other-role"
    with pytest.raises(WorktreeStateError, match="different executing machine"):
        readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner)
    cfg._data["machine"]["role"] = None
    cfg._data["sync"]["targets"]["local"]["path"] = str(target._root().with_name("other"))
    with pytest.raises(WorktreeStateError, match="current sync configuration"):
        readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner)


_PRODUCE = r"""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import agenda_store as owner
from agenda_export import export_store
home,destination=Path(sys.argv[2]),Path(sys.argv[3])
worktree,store_id=sys.argv[4:6]
rich=sys.argv[6:] == ["rich"]
definition={"source_ref":"fixture/task","scope_ref":"fixture","action":"Run fixture",
    "exit":["fixture recorded"],"on":{"done":"advance","blocked":"wait","no-progress":"replan"},
    "relevant_subjects":["dataset"]}
def context(value):
    return {"facts":[{"kind":"input","subject":"dataset","value":value,"evidence_ref":"fixture"}]}
def apply(change):
    revision=owner.read(home)["revision"] if (home/"agenda.sqlite3").exists() else 0
    request={"schema":"efforts.agenda-request","version":2,"mode":"context-qualified",
        "expected_revision":revision,"operation_id":f"op-{revision}","change":change}
    actor = "writer/\u975eASCII" if rich and revision else None
    session = (None if revision==0 else "../arbitrary session") if rich else (
        "session-1" if revision==0 else "session-2")
    owner.mutate(home,request,session=session,actor=actor,
        initial_store_id=store_id if revision==0 else None)
apply({"operation":"init","worktree":worktree,"board":{"version":2,
    "source":{"kind":"task","ref":"fixture"},"revision":1,"items":{"one":definition},"order":["one"]},
    "baseline_context":context({"content":"initial"}),"resource_caps":{
        "max_depth":4,"max_pending_items":32,"max_deferred_frames":4,"max_no_parent_progress":3}})
for value in range(2):
    cursor=owner.read(home)["documents"]["cursor"]
    frame=cursor["frames"][-1]
    item=cursor["frame_state"][frame]["pending"][0]
    attempt=cursor["item_state"][item]["current_attempt_id"]
    apply({"operation":"outcome","frame_id":frame,"item_id":item,"attempt_id":attempt,
        "outcome":"no-progress","evidence":[{"criterion":"fixture recorded","kind":"measured",
            "ref":"fixture","inputs":"fixture revision"}]})
    apply({"operation":"revisit","frame_id":frame,"item_id":item,"predecessor_attempt_id":attempt,
        "context":context({"content":f"changed-{value}"}),"judgment":{
            "qualification":"agent-judgment","changed_subjects":["dataset"],
            "causal_change":"Changed fixture input enables another attempt.","evidence_refs":["fixture"]},
        "approach":{"action":"Run fixture"}})
print(json.dumps(export_store(home,destination,worktree=worktree,store_id=store_id,
    expected_revision=5)))
"""


@pytest.mark.parametrize("redacted", [False, True])
@pytest.mark.parametrize("provenance", [False, True])
def test_actual_efforts_export_round_trips_complete_ledger_without_owner_import(
    packet: Packet, redacted: bool, provenance: bool,
) -> None:
    cfg, target, plan, source = packet
    scripts = Path(__file__).resolve().parents[2] / "efforts/skills/driving-effort-agendas/scripts"
    home, exported = source.parent / "actual-store", source.parent / "actual-export"
    home.mkdir(mode=0o700)
    exported.mkdir(mode=0o700)
    produced = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _PRODUCE, str(scripts), str(home), str(exported),
         IDENTITY.worktree, IDENTITY.store_id, *(["rich"] if provenance else [])],
        capture_output=True, text=True, timeout=30, check=True,
    )
    manifest = json.loads(produced.stdout)
    assert manifest["tables"]["operations"]["rows"] == 5
    assert manifest["tables"]["agenda_records"]["rows"] > 5
    identity = replace(IDENTITY, revision=5)

    def owner_command(command: str, directory: Path, **selection: Any) -> dict:
        arguments = [] if command == "info" else [
            "--destination", str(directory), "--worktree", selection["worktree"],
            "--store-id", selection["store_id"],
            "--expected-revision", str(selection["expected_revision"]), "--contract-version", "1",
        ]
        result = subprocess.run([
            sys.executable, "-I", "-B", "-c",
            "import runpy,sys; from pathlib import Path; script=sys.argv.pop(1); "
            "sys.path.insert(0,str(Path(script).parent)); runpy.run_path(script,run_name='__main__')",
            str(scripts / "agenda_export_cli.py"), command, *arguments,
        ], capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["schema"] == "efforts.agenda-export-cli" and value["version"] == 1
        assert value["ok"] is True
        return value

    def owner_verify(directory: Path, **selection: Any) -> dict:
        return owner_command("verify", directory, **selection)["manifest"]

    negotiated = False
    if provenance:
        info = owner_command("info", exported)
        assert info["capabilities"] == ["agenda-complete-export-v1"]
        assert "agenda-export-contributors-v1" in info["optional_capabilities"]
        negotiated = True

    def owner_contributors(directory: Path, **selection: Any) -> dict:
        assert negotiated
        return owner_command("contributors", directory, **selection)["contributors"]

    reader = owner_contributors if provenance else None
    receipt = collect_worktree_state(
        cfg, target, plan, exported, identity, () if provenance else ("session-1", "session-2"),
        owner_verify, contributors=reader,
    )
    retained = target._root() / identity.relative_directory / "export"
    assert (retained / "agenda.sqlite3").read_bytes() == (exported / "agenda.sqlite3").read_bytes()
    assert owner_verify(retained, worktree=identity.worktree, store_id=identity.store_id,
                        expected_revision=5) == manifest
    assert readback_worktree_state(
        cfg, target, plan, identity, owner_verify, contributors=reader,
    ) == receipt
    if provenance:
        actual = json.loads((retained.parent / "contributors.json").read_bytes())["contributors"]
        assert [(entry["actor"], entry["session"], entry["operations"]) for entry in actual] == [
            (None, None, 1), ("writer/\u975eASCII", "../arbitrary session", 4),
        ]
    assert (home / "agenda.sqlite3").exists()

    legacy = target._root() / "worker/session-state/old"
    legacy.mkdir(parents=True)
    shutil.copytree(target._root() / "worktree-state", legacy / "worktree-state")
    retained = legacy / identity.relative_directory / "export"
    assert owner_verify(retained, worktree=identity.worktree, store_id=identity.store_id,
                        expected_revision=5) == manifest
    paths_before = {path.relative_to(legacy) for path in legacy.rglob("*")}
    source = source.parent / "unmarked-source"
    replacement = source / "session-state/old" / retained.relative_to(legacy) / "newdir/file"
    replacement.parent.mkdir(parents=True)
    replacement.write_bytes(b"{}")
    if redacted:
        target.protection = ProtectionSettings(redact_secrets=True)
    result = target.push(source, "worker")
    assert not result.ok and "worktree-state" in result.detail
    assert {path.relative_to(legacy) for path in legacy.rglob("*")} == paths_before
    assert owner_verify(retained, worktree=identity.worktree, store_id=identity.store_id,
                        expected_revision=5) == manifest
