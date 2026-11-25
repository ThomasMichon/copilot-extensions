"""Retained discovery must not confuse settled responsibility with absent storage."""

from dataclasses import replace
import json
from pathlib import Path
import socket
from typing import Protocol

import pytest
import yaml

from agent_worktrees import config as cfg, record_reporting, tracking
from agent_worktrees import claims_cli, output
from agent_worktrees.__main__ import build_parser
from agent_worktrees.retained_worktrees import inventory
from agent_worktrees.tracking_claims import ResourceClaim
from agent_worktrees.tracking_lifecycle import create_new_record


class SaveRecord(Protocol):
    def __call__(
        self, project: str, wid: str, children: tuple[tuple[str, str], ...] | list[tuple[str, str]] = (),
        *, status: tracking.WorktreeStatus = "active", platform: str = "linux",
    ) -> tracking.WorktreeRecord: ...


Graph = tuple[cfg.Config, SaveRecord]


@pytest.fixture
def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Graph:
    root = tmp_path / "records"
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: root / name)
    monkeypatch.setattr(socket, "gethostname", lambda: "host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    config = cfg.Config(
        srcroot=str(tmp_path), machine="host", platform="linux", repo_name="root",
        repos={"root": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "checkouts" / "root"))},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kwargs: replace(
        config, repo_name=name, platform="windows" if name == "foreign" else "linux",
        repos={name: cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "checkouts" / name))},
    ))

    def save(
        project: str, wid: str, children: tuple[tuple[str, str], ...] | list[tuple[str, str]] = (),
        *, status: tracking.WorktreeStatus = "active", platform: str = "linux",
    ) -> tracking.WorktreeRecord:
        directory = root / project / "worktrees"
        directory.mkdir(parents=True, exist_ok=True)
        record = create_new_record(
            wid, f"worktree/{wid}", str(tmp_path / "checkouts" / project / wid),
            project, "host", platform, directory,
        )
        record.status = status
        record.resources = [ResourceClaim(kind="worktree", ref=ref, state=state)
                            for ref, state in children]
        tracking.save_record(record, directory / f"{wid}.yaml")
        return record

    return config, save


def test_settled_cross_project_children_and_grandchildren_remain_visible(graph: Graph) -> None:
    config, save = graph
    save("root", "parent", [("host/source/child", "at-rest")])
    child = save("source", "child", [("host/library/grandchild", "released")], status="finalized")
    save("library", "grandchild", status="finalized")

    result = inventory("parent", config)

    assert result["complete"] is True
    assert [node["project"] for node in result["nodes"]] == ["root", "source", "library"]
    assert result["nodes"][1]["checkout_path"] == child.worktree_path
    assert result["nodes"][1]["checkout_presence"] == "absent"
    assert len(result["edges"]) == 2
    assert result["removal_authorized"] is False


@pytest.mark.parametrize("opaque", [
    {"kind": {"invalid": 1}, "ref": "host/source/child"},
    {"kind": "worktree", "ref": "host/source/child", "metadata_opaque": True},
    {"kind": "task", "ref": "opaque", "metadata_opaque": True},
])
def test_opaque_claim_metadata_cannot_report_a_complete_inventory(
    graph: Graph, opaque: dict[str, object],
) -> None:
    config, save = graph
    save("root", "parent")
    save("source", "child", status="finalized")
    save("library", "sibling", status="finalized")
    path = cfg.project_dir("root") / "worktrees" / "parent.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["resources"] = [
        opaque, {"kind": "worktree", "ref": "host/library/sibling", "state": "released"},
    ]
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    before = path.read_bytes()

    result = inventory("parent", config)

    assert not result["complete"]
    assert any("opaque" in item["reason"] for item in result["unresolved"])
    assert any(node["worktree_id"] == "sibling" for node in result["nodes"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("child_machine", ["host", "registry-key", "remote"])
def test_reporter_registry_key_and_record_scoped_aliases(
    graph: Graph, monkeypatch: pytest.MonkeyPatch, child_machine: str,
) -> None:
    config, save = graph
    save("root", "parent", [(f"{child_machine}/source/child", "released")])
    save("source", "child", status="finalized")
    anchor = Path(config.default_repo.anchor)
    monkeypatch.setattr(record_reporting, "configured_repository_paths", lambda project: {project: anchor})
    (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "registry-key": {"hostname": "host", "os": "linux"},
        "remote": {"hostname": "another-host", "os": "linux"},
    }}), encoding="utf-8")
    paths = [cfg.project_dir(project) / "worktrees" / f"{wid}.yaml"
             for project, wid in (("root", "parent"), ("source", "child"))]
    before = {path: path.read_bytes() for path in paths}
    assert config.machine == "host"
    assert tracking.load_record(paths[0]).execution_machine == "registry-key"

    result = inventory("parent", config)

    assert result["root"] == "registry-key/root/parent"
    assert [node["worktree_id"] for node in result["nodes"]] == (
        ["parent"] if child_machine == "remote" else ["parent", "child"]
    )
    assert result["complete"] is (child_machine != "remote")
    assert {path: path.read_bytes() for path in paths} == before


def test_missing_remote_and_environment_mismatch_are_not_empty_success(graph: Graph) -> None:
    config, save = graph
    save("root", "parent", [
        ("host/source/missing", "released"), ("remote/source/child", "at-rest"),
        ("host/foreign/wrong-platform", "active"),
    ])
    save("foreign", "wrong-platform", platform="windows")

    result = inventory("parent", config)

    assert not result["complete"]
    assert len(result["nodes"]) == 1
    assert len(result["unresolved"]) == 3


@pytest.mark.parametrize(("pid", "failure"), [
    ({"invalid": 1}, "TypeError"), (float("inf"), "OverflowError"), ("not-a-pid", "ValueError"),
])
def test_corrupt_descendant_is_unresolved_without_hiding_an_independent_sibling(
    graph: Graph, pid: object, failure: str,
) -> None:
    config, save = graph
    save("root", "parent", [
        ("host/source/broken", "released"), ("host/library/sibling", "at-rest"),
    ])
    save("source", "broken")
    save("library", "sibling", status="finalized")
    path = cfg.project_dir("source") / "worktrees" / "broken.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["sessions"] = [{"session_id": "fixture", "pid": pid}]
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    before = path.read_bytes()

    result = inventory("parent", config)

    assert not result["complete"]
    assert [node["worktree_id"] for node in result["nodes"]] == ["parent", "sibling"]
    assert result["unresolved"] == [{
        "ref": "host/source/broken", "reason": f"record unavailable: {failure}",
    }]
    assert path.read_bytes() == before


@pytest.mark.parametrize("limits", [{"max_nodes": 1}, {"max_depth": 1}])
def test_bounds_preserve_an_explicit_unresolved_edge(graph: Graph, limits: dict[str, int]) -> None:
    config, save = graph
    save("root", "parent", [("host/source/child", "released")])
    save("source", "child", [("host/library/grandchild", "at-rest")])
    save("library", "grandchild")
    result = inventory("parent", config, **limits)
    assert not result["complete"]
    assert any(item["reason"] == "traversal limit" for item in result["unresolved"])


@pytest.mark.parametrize("worktree_path", [{"invalid": 1}, ["invalid"], None])
def test_malformed_descendant_path_does_not_hide_an_independent_sibling(
    graph: Graph, worktree_path: object,
) -> None:
    config, save = graph
    save("root", "parent", [
        ("host/source/broken", "released"), ("host/library/sibling", "at-rest"),
    ])
    save("source", "broken")
    save("library", "sibling", status="finalized")
    path = cfg.project_dir("source") / "worktrees" / "broken.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["worktree_path"] = worktree_path
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    before = path.read_bytes()

    result = inventory("parent", config)

    assert not result["complete"]
    assert [node["worktree_id"] for node in result["nodes"]] == ["parent", "sibling"]
    assert len(result["unresolved"]) == 1
    assert result["unresolved"][0]["ref"] == "host/source/broken"
    assert path.read_bytes() == before


def test_cycle_and_unsafe_child_do_not_escape_or_recurse_forever(graph: Graph) -> None:
    config, save = graph
    save("root", "parent", [
        ("host/source/child", "at-rest"), ("host/../escape", "released"),
    ])
    save("source", "child", [("host/root/parent", "released")])
    result = inventory("parent", config)
    assert len(result["nodes"]) == 2
    assert {item["reason"] for item in result["unresolved"]} == {"cycle", "unsafe child identity"}


def test_incomplete_observation_does_not_change_records(graph: Graph) -> None:
    config, save = graph
    record = save("root", "parent", [("host/source/child", "active")])
    path = cfg.project_dir("root") / "worktrees" / "parent.yaml"
    before = path.read_bytes()
    result = inventory("parent", replace(config, platform="windows"))
    assert result["nodes"] == []
    assert not result["complete"]
    assert path.read_bytes() == before
    assert record.resources[0].state == "active"


def test_cli_dispatch_renders_settled_child_without_changing_obligations(
    graph: Graph, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, save = graph
    save("root", "parent", [("host/source/child", "released")])
    save("source", "child", status="finalized")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    rendered: list[str] = []
    monkeypatch.setattr(output, "_json_output", lambda value: rendered.append(json.dumps(value)))
    args = build_parser().parse_args(
        ["claims", "retained", "--worktree-id", "parent", "--json"])

    assert claims_cli.cmd_claims(args) == 0
    result = json.loads(rendered[0])
    assert result["complete"] is True
    assert [node["worktree_id"] for node in result["nodes"]] == ["parent", "child"]
    assert result["edges"][0]["state"] == "released"
