"""Complete historical WSL handoffs require per-record receiving authority."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import claim_handoff_accept_support, claim_handoffs, config as cfg, tracking
from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_store import GitLeaseStore

MACHINE = "example-host-wsl"


@pytest.fixture(params=["unscoped", "projected"])
def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path / (project or "consumer-project"))
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "consumer-project")
    paths: dict[str, Path] = {}
    anchors: dict[str, Path] = {}
    for role in ("source", "consumer", "child"):
        project, worktree_id = f"{role}-project", f"wt-{role}"
        anchor = tmp_path / "anchors" / project
        anchor.mkdir(parents=True)
        host: dict = {"hostname": "generated-host", "alias": "native-alias"}
        if request.param == "projected":
            host["ssh"] = {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]}
            machines = {"example-host": host}
        else:
            machines = {"example-host": host, MACHINE: {}}
        (anchor / "machines.yaml").write_text(
            yaml.safe_dump({"machines": machines}), encoding="utf-8",
        )
        namespace = tmp_path / "trees" / project
        checkout = namespace / worktree_id
        checkout.mkdir(parents=True)
        directory = tmp_path / project / "worktrees"
        directory.mkdir(parents=True)
        (directory.parent / "config.yaml").write_text(yaml.safe_dump({
            "repo_name": project, "machine": MACHINE, "platform": "wsl",
            "repos": {project: {"anchor": str(anchor), "worktree_root": str(namespace)}},
        }), encoding="utf-8")
        record = tracking.create_new_record(
            worktree_id, f"worktree/{worktree_id}", str(checkout), project,
            "generated-host" if role == "source" else "example-host", "wsl", directory,
        )
        paths[role] = record.yaml_path
        anchors[role] = anchor
    source = "native-alias/source-project/wt-source"
    consumer = "example-host/consumer-project/wt-consumer"
    child_ref = "generated-host/child-project/wt-child"
    child = tracking.load_record(paths["child"])
    child.owner_ref = "example-host/source-project/wt-source"
    tracking.save_record(child, paths["child"])
    record = tracking.load_record(paths["source"])
    record.resources = [
        tracking.ResourceClaim(kind="worktree", ref=child_ref),
        tracking.ResourceClaim(kind="pr", ref="example-org/project#42"),
    ]
    tracking.save_record(record, paths["source"])
    return SimpleNamespace(
        root=tmp_path, paths=paths, anchors=anchors, source=source, consumer=consumer,
        child_ref=child_ref, projected=request.param == "projected",
    )


def _offer(graph: SimpleNamespace) -> claim_handoffs.ClaimBundle:
    return claim_handoffs.offer(
        graph.source, graph.consumer,
        [claim.ref for claim in tracking.load_record(graph.paths["source"]).resources],
        machine=MACHINE, id_factory=lambda: "legacy-bundle",
    )[0]


@pytest.mark.parametrize("labels", ["both-legacy", "qualified-consumer", "qualified-source"])
def test_complete_legacy_offer_accept_transfers_graph_and_real_git_lease(
    graph: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, labels: str,
) -> None:
    if labels == "qualified-consumer":
        graph.consumer = f"{MACHINE}/consumer-project/wt-consumer"
    elif labels == "qualified-source":
        graph.source = f"{MACHINE}/source-project/wt-source"
    origin = graph.root / "lease-origin.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(origin)], check=True,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    settings = LeaseSettings(origin=str(origin))
    monkeypatch.setattr(claim_handoffs, "load_lease_settings", lambda: settings)
    monkeypatch.setattr(claim_handoff_accept_support, "load_lease_settings", lambda: settings)
    store = GitLeaseStore(settings)
    store.acquire("codespace", "example-space", graph.source)
    source = tracking.load_record(graph.paths["source"])
    source.resources.append(tracking.ResourceClaim(kind="codespace", ref="example-space"))
    tracking.save_record(source, graph.paths["source"])
    bundle = _offer(graph)
    identities = {
        role: (record.machine, record.platform)
        for role, path in graph.paths.items() for record in [tracking.load_record(path)]
    }
    monkeypatch.setattr(
        claim_handoffs, "remote_accept_source",
        lambda **_kwargs: pytest.fail("verified local WSL actors must not use native-host SSH"),
    )
    actor = f"{MACHINE}/consumer-project/wt-consumer"
    accepted = claim_handoffs.accept(bundle.bundle_id, actor=actor, machine=MACHINE)
    assert accepted.state == "accepted"
    assert (accepted.source, accepted.consumer) == (graph.source, graph.consumer)
    assert tracking.load_record(graph.paths["source"]).resources == []
    consumer = tracking.load_record(graph.paths["consumer"])
    assert {claim.ref for claim in consumer.resources} == {
        graph.child_ref, "example-org/project#42", "example-space",
    }
    assert tracking.load_record(graph.paths["child"]).owner_ref == graph.consumer
    assert store.inspect("codespace", "example-space").record.holder == graph.consumer
    assert all(
        (tracking.load_record(path).machine, tracking.load_record(path).platform) == identities[role]
        for role, path in graph.paths.items()
    )
    assert claim_handoffs.accept(bundle.bundle_id, actor=actor, machine=MACHINE).state == "accepted"


@pytest.mark.parametrize("violation", [
    "native-source", "native-consumer", "scoped-source", "scoped-consumer",
    "namespace-source", "namespace-consumer", "record-id-source", "record-id-consumer",
])
def test_accept_rejects_unproven_legacy_actors_without_local_ledger_mutation(
    graph: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, violation: str,
) -> None:
    bundle = _offer(graph)
    kind, role = violation.rsplit("-", 1)
    path = graph.paths[role]
    record = tracking.load_record(path)
    if kind == "native":
        record = replace(record, platform="windows")
    elif kind == "namespace":
        record = replace(record, worktree_path=str(graph.root))
    elif kind == "record-id":
        record = replace(record, worktree_id="another-worktree")
    elif kind == "scoped":
        (graph.anchors[role] / "machines.yaml").write_text(
            "machines:\n"
            "  example-host:\n    execution_platform: windows\n    alias: native-alias\n"
            "  example-host-wsl:\n    execution_platform: wsl\n", encoding="utf-8",
        )
    tracking.save_record(record, path)
    registry = claim_handoffs.registry_path()
    before = {p: p.read_bytes() for p in [*graph.paths.values(), registry]}
    remote_calls: list[str] = []

    def refuse_remote(**_kwargs):
        remote_calls.append("remote")
        raise claim_handoffs.ClaimHandoffError("unproven source requires its remote authority")

    monkeypatch.setattr(claim_handoffs, "remote_accept_source", refuse_remote)
    monkeypatch.setattr(
        claim_handoffs, "acquire_bundle_fence",
        lambda **_kwargs: (SimpleNamespace(), SimpleNamespace(oid="fixture-fence")),
    )
    monkeypatch.setattr(claim_handoffs, "release_bundle_fence", lambda *_args, **_kwargs: None)
    with pytest.raises(claim_handoffs.ClaimHandoffError):
        claim_handoffs.accept(
            bundle.bundle_id, actor=f"{MACHINE}/consumer-project/wt-consumer", machine=MACHINE,
        )
    assert all(p.read_bytes() == content for p, content in before.items())
    if role == "consumer" or graph.projected:
        assert remote_calls == []
