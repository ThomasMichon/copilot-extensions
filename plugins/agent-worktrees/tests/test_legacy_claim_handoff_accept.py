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
from agent_worktrees import claimant, legacy_wsl_record_locality
from agent_worktrees.tracking_claims import ANCHOR_ID, load_or_create_anchor_record
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


def test_cross_project_claimant_keeps_actual_caller_and_receiving_alias_authority(graph, monkeypatch):
    caller = cfg.load_project_config("consumer-project", include_control_plane_related_pr=False)
    load_config = cfg.load_config

    def caller_config(*args, **kwargs):
        return load_config(*args, **kwargs) if args or kwargs.get("path") else caller

    monkeypatch.setattr(cfg, "load_config", caller_config)
    monkeypatch.setattr(claimant, "_owner_looks_dead", lambda _record: False)
    ref = tracking.parse_claim_ref(graph.source)
    before = {path: path.read_bytes() for path in graph.paths.values()}
    assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(ref, caller) == graph.paths["source"]
    assert legacy_wsl_record_locality.legacy_wsl_owner_ref_is_local(ref)
    assert claimant.local_claimant_alive(graph.source) is True
    assert claimant.resolve_claimant_alive(graph.source, allow_remote=False) is True
    assert before == {path: path.read_bytes() for path in graph.paths.values()}
    caller = replace(caller, machine="foreign-host-wsl")
    assert claimant.local_claimant_alive(graph.source) is None


def _anchor_graph(graph: SimpleNamespace, *, qualified: bool) -> None:
    claims = tracking.load_record(graph.paths["source"]).resources
    original = tracking.load_record(graph.paths["source"])
    original.resources = []
    tracking.save_record(original, graph.paths["source"])
    for role in ("source", "consumer"):
        project = f"{role}-project"
        record = load_or_create_anchor_record(
            str(graph.anchors[role]), project,
            MACHINE if qualified else "example-host", "wsl", cfg.project_dir(project) / "worktrees",
        )
        if role == "source":
            record.resources = claims
        tracking.save_record(record, record.yaml_path)
        graph.paths[role] = record.yaml_path
    graph.source = f"{MACHINE if qualified else 'native-alias'}/source-project/{ANCHOR_ID}"
    graph.consumer = f"{MACHINE if qualified else 'example-host'}/consumer-project/{ANCHOR_ID}"
    child = tracking.load_record(graph.paths["child"])
    child.owner_ref = graph.source
    tracking.save_record(child, graph.paths["child"])


@pytest.mark.parametrize("qualified", [False, True])
def test_anchor_actors_use_receiving_anchor_for_full_offer_accept(graph, monkeypatch, qualified):
    _anchor_graph(graph, qualified=qualified)
    monkeypatch.setattr(claimant, "_owner_looks_dead", lambda _record: False)
    ref = tracking.parse_claim_ref(graph.source)
    assert claimant.local_claimant_alive(graph.source) is True
    assert claim_handoff_accept_support.local_record_ref(ref, machine=MACHINE)
    bundle = _offer(graph)
    result = claim_handoffs.accept(
        bundle.bundle_id, actor=f"{MACHINE}/consumer-project/{ANCHOR_ID}", machine=MACHINE,
    )
    assert result.state == "accepted"
    assert tracking.load_record(graph.paths["source"]).resources == []
    assert tracking.load_record(graph.paths["child"]).owner_ref == graph.consumer
    for role in ("source", "consumer"):
        record = tracking.load_record(graph.paths[role])
        assert record.worktree_path == str(graph.anchors[role])
        assert record.machine == (MACHINE if qualified else "example-host")


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("violation", ["relative", "ordinary-root", "symlink", "platform"])
def test_anchor_actor_still_requires_absolute_anchor_platform_and_no_redirect(
    graph, qualified, violation,
):
    _anchor_graph(graph, qualified=qualified)
    path = graph.paths["source"]
    record = tracking.load_record(path)
    if violation == "relative":
        record = replace(record, worktree_path="relative-anchor")
    elif violation == "ordinary-root":
        record = replace(record, worktree_path=str(graph.root / "trees" / "source-project" / ANCHOR_ID))
    elif violation == "platform":
        record = replace(record, platform="windows")
    else:
        redirect = graph.root / "redirect-anchor"
        try:
            redirect.symlink_to(graph.anchors["source"], target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"host cannot create the contained symlink fixture: {exc}")
        config_path = cfg.project_dir("source-project") / "config.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        data["repos"]["source-project"]["anchor"] = str(redirect)
        config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        record = replace(record, worktree_path=str(redirect))
    tracking.save_record(record, path)
    before = {path: path.read_bytes() for path in graph.paths.values()}
    with pytest.raises(claim_handoffs.ClaimHandoffError):
        _offer(graph)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_handoff_equivalence_uses_named_source_alias_not_ambient_consumer_topology(graph):
    for role in ("source", "consumer", "child"):
        topology = graph.anchors[role] / "machines.yaml"
        data = yaml.safe_load(topology.read_text(encoding="utf-8"))
        data["machines"]["example-host"]["alias"] = f"{role}-alias"
        topology.write_text(yaml.safe_dump(data), encoding="utf-8")
    graph.source = "source-alias/source-project/wt-source"
    graph.consumer = "consumer-alias/consumer-project/wt-consumer"
    old_owner = "example-host/source-project/wt-source"
    assert legacy_wsl_record_locality.claim_refs_equivalent(
        tracking.parse_claim_ref(old_owner), tracking.parse_claim_ref(graph.source),
    )
    bundle = _offer(graph)
    result = claim_handoffs.accept(
        bundle.bundle_id, actor=f"{MACHINE}/consumer-project/wt-consumer", machine=MACHINE,
    )
    assert result.state == "accepted"
    assert tracking.load_record(graph.paths["source"]).resources == []
    assert tracking.load_record(graph.paths["child"]).owner_ref == graph.consumer


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
