"""Real Git consumers keep cross-repository finality separate from cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import socket

import pytest
from machine_transport import detect_platform, resolve_local_execution_context

from agent_worktrees import claims_cli, cleanup_gc_cli, config as cfg, finalize, sessions, tracking, worktree_identity
from agent_worktrees.__main__ import build_parser
from agent_worktrees.retained_worktrees import inventory
from agent_worktrees.tracking_claims import ResourceClaim, format_claim_ref
from agent_worktrees.tracking_lifecycle import create_new_record
from pr_test_helpers import _git

pytestmark = pytest.mark.usefixtures("git_only_mux")


@dataclass
class Graph:
    configs: dict[str, cfg.Config]
    records: dict[str, tracking.WorktreeRecord]
    selected: list[str]

    def select(self, name: str) -> cfg.Config:
        self.selected[0] = self.records[name].repo
        return self.configs[self.selected[0]]


@pytest.fixture
def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Graph:
    platform = detect_platform()
    hostname = "fixture-host"
    machine = resolve_local_execution_context(
        {}, hostname=hostname, environment=platform,
    ).execution_machine
    selected = ["control"]
    state = tmp_path / "records"
    configs: dict[str, cfg.Config] = {}
    records: dict[str, tracking.WorktreeRecord] = {}
    monkeypatch.setattr(socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: state / (name or selected[0]))
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: state / (name or selected[0]) / "worktrees")
    monkeypatch.setattr(cfg, "project_name", lambda *a, **k: selected[0])
    monkeypatch.setattr(cfg, "active_project", lambda *a, **k: selected[0])
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: configs[selected[0]])
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kwargs: configs[name])
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: [])
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    for project in ("control", "source", "library"):
        anchor = tmp_path / project / "anchor"
        anchor.mkdir(parents=True)
        _git("init", "--template=", "-b", "master", cwd=anchor)
        _git("config", "user.name", "Test", cwd=anchor)
        _git("config", "user.email", "test@example.com", cwd=anchor)
        (anchor / ".gitignore").write_text("private-evidence/\n", encoding="utf-8")
        _git("add", ".gitignore", cwd=anchor)
        _git("commit", "-m", "fixture base", cwd=anchor)
        repo = cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / project / "trees"),
            default_branch="master", pr=cfg.PRConfig(),
        )
        configs[project] = cfg.Config(
            srcroot=str(tmp_path), machine=machine, platform=platform, repo_name=project,
            repos={project: repo},
        )
        directory = state / project
        (directory / "worktrees").mkdir(parents=True)
        (directory / "config.yaml").write_text(json.dumps({
            "repo_name": project, "machine": machine, "platform": platform,
            "repos": {project: {"anchor": repo.anchor, "worktree_root": repo.worktree_root}},
        }), encoding="utf-8")

    for name, project, parent in (
        ("parent", "control", None), ("child", "source", "parent"),
        ("grandchild", "library", "child"), ("sibling", "library", "parent"),
    ):
        repo = configs[project].default_repo
        checkout = Path(repo.worktree_root) / name
        checkout.parent.mkdir(parents=True, exist_ok=True)
        _git("worktree", "add", "-b", f"worktree/{name}", str(checkout), cwd=Path(repo.anchor))
        owner = records[parent] if parent else None
        record = create_new_record(
            name, f"worktree/{name}", str(checkout), project, machine, platform,
            state / project / "worktrees",
            owner_ref=format_claim_ref(machine, owner.repo, owner.worktree_id) if owner else None,
        )
        records[name] = record
        if owner is not None:
            owner.resources.append(ResourceClaim(
                kind="worktree", ref=format_claim_ref(machine, project, name), state="active",
            ))
            tracking.save_record(owner, state / owner.repo / "worktrees" / f"{owner.worktree_id}.yaml")
    return Graph(configs, records, selected)


def test_retained_chain_and_blocked_descendant_do_not_hide_an_eligible_sibling(
    graph: Graph,
) -> None:
    root = graph.select("parent")
    before = inventory("parent", root)
    assert before["complete"] is True
    assert len(before["nodes"]) == 4 and len(before["edges"]) == 3
    protected = Path(graph.records["grandchild"].worktree_path) / "private-evidence"
    protected.mkdir()
    evidence = protected / "capture.txt"
    evidence.write_bytes(b"synthetic retained evidence")

    for name in ("grandchild", "child", "sibling", "parent"):
        config = graph.select(name)
        assert finalize.validate_and_finalize(name, config, retain_checkout=True)
        assert Path(graph.records[name].worktree_path).is_dir()
        assert tracking.load_record(cfg.tracking_dir() / f"{name}.yaml").status == "finalized"
    root = graph.select("parent")
    settled = inventory("parent", root)
    assert settled["complete"] is True
    assert len(settled["nodes"]) == 4 and len(settled["edges"]) == 3
    assert all(node["status"] == "finalized" for node in settled["nodes"])
    assert all(edge["state"] in {"at-rest", "released"} for edge in settled["edges"])
    assert settled["removal_authorized"] is False

    parser = build_parser()
    for project in ("library", "source", "control"):
        graph.selected[0] = project
        cleanup_gc_cli.cmd_cleanup(parser.parse_args(["cleanup"]))
    assert all(Path(record.worktree_path).is_dir() for record in graph.records.values())

    for project in ("library", "source", "control"):
        graph.selected[0] = project
        cleanup_gc_cli.cmd_cleanup(parser.parse_args(["cleanup", "--clean"]))
    assert evidence.read_bytes() == b"synthetic retained evidence"
    assert Path(graph.records["grandchild"].worktree_path).is_dir()
    for name in ("parent", "child", "sibling"):
        assert not Path(graph.records[name].worktree_path).exists()


@pytest.mark.parametrize("tracked", [True, False])
def test_cwd_discovery_reads_existing_records_without_adopting(
    graph: Graph, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], tracked: bool,
) -> None:
    config = graph.select("parent")
    checkout = Path(graph.records["parent"].worktree_path)
    if not tracked:
        checkout = Path(config.default_repo.worktree_root) / "external"
        _git("worktree", "add", "-b", "worktree/external", str(checkout),
             cwd=Path(config.default_repo.anchor))
    monkeypatch.chdir(checkout)
    records = cfg.project_dir(config.repo_name)
    before = {path.relative_to(records): path.read_bytes() for path in records.rglob("*") if path.is_file()}

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("read-only retained discovery must not infer through an adopting resolver")

    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", unexpected)
    monkeypatch.setattr(worktree_identity, "_adopt_linked_worktree", unexpected)
    args = build_parser().parse_args(["claims", "retained", "--json"])
    result = claims_cli.cmd_claims(args)
    rendered = json.loads(capfd.readouterr().out)

    assert result == (0 if tracked else 2)
    if tracked:
        assert rendered["root"].endswith("/control/parent")
    else:
        assert "existing tracked worktree" in rendered["error"]
        assert not (cfg.tracking_dir() / "external.yaml").exists()
    assert {path.relative_to(records): path.read_bytes() for path in records.rglob("*") if path.is_file()} == before
