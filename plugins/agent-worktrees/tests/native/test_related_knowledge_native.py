"""Actual related readers preserve bare-anchor and aligned-worktree boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sysconfig

from agent_procutil import no_window_kwargs
import pytest

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.real_installer_processes,
    pytest.mark.timeout(60),
]


@pytest.mark.parametrize("entry_kind", ["bare-anchor", "harness-worktree"])
def test_native_bound_knowledge_discovery_is_read_only(tmp_path, entry_kind):
    home, seed = tmp_path / "home", tmp_path / "seed"
    anchor, worktree = tmp_path / "harness.git", tmp_path / "harness-worktree"
    knowledge, allocations = tmp_path / "knowledge", tmp_path / "allocations"
    seed.mkdir()
    knowledge.mkdir()

    def git(*arguments, cwd=seed):
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
             "-c", "commit.gpgsign=false", *map(str, arguments)],
            cwd=cwd, check=True, capture_output=True, timeout=20,
            **no_window_kwargs(),
        )

    git("init", "--initial-branch=main")
    (seed / "README.md").write_text("Fixture harness\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "Fixture")
    git("clone", "--bare", seed, anchor)
    git("--git-dir", anchor, "worktree", "add", "--detach", worktree, "main")
    git("init", "--initial-branch=main", cwd=knowledge)
    (knowledge / "retained.txt").write_text("Retained fixture state\n", encoding="utf-8")
    git("add", "retained.txt", cwd=knowledge)
    git("commit", "-m", "Retained state", cwd=knowledge)
    selected = anchor if entry_kind == "bare-anchor" else worktree

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    platform = "windows" if os.name == "nt" else "linux"
    write(home / ".agent-worktrees" / "projects.yaml", {"projects": ["sample"]})
    write(home / ".agent-worktrees" / "repos.yaml", {"repos": {
        "sample": {"class": "worktree", platform: str(anchor)},
        "notes": {"class": "knowledge", platform: str(knowledge)},
    }})
    write(home / ".sample" / "config.yaml", {
        "repo_name": "sample", "machine": "fixture", "knowledge_repo": "notes",
        "repos": {"sample": {
            "anchor": str(anchor), "worktree_root": str(allocations),
            "stateless": True,
        }},
    })
    environment = {
        **os.environ, "AGENT_HOME": str(home), "HOME": str(home),
        "USERPROFILE": str(home), "COPILOT_HOME": str(home / ".copilot"),
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    entry = Path(sysconfig.get_path("scripts")) / (
        "agent-worktrees.exe" if os.name == "nt" else "agent-worktrees"
    )
    assert entry.is_file()

    def snapshot():
        return {
            root: {
                str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
            }
            for root in (anchor, worktree, knowledge)
        }

    before = snapshot()
    configuration_path = home / ".sample" / "config.yaml"
    configuration_before = configuration_path.read_bytes()

    def invoke(*arguments):
        return subprocess.run(
            [str(entry), "--project", "sample", "related", *arguments,
             "--repo", str(selected)],
            cwd=selected, env=environment, capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )

    listed = invoke("list", "--role", "knowledge", "--json")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    values = json.loads(listed.stdout)["related"]
    assert [value["name"] for value in values] == ["notes"], (
        f"bound knowledge missing: {values!r}"
    )
    assert values[0]["registry"]["class"] == "knowledge"
    assert values[0]["registry"]["path"] == str(knowledge)
    assert values[0]["state_root"]["anchor_writable"] is False
    resolved = invoke("resolve", "notes", "--json")
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    plan = json.loads(resolved.stdout)
    assert plan["role"] == "knowledge" and plan["locus_kind"] == "local"
    assert plan["editing_model"] == "paired-knowledge-worktree"
    assert plan["registry"]["path"] == str(knowledge)
    assert "state-root --pair --json" in " ".join(plan["steps"])
    narrative = invoke("doc", "notes")
    assert narrative.returncode != 0
    assert "no narrative is scaffolded" in narrative.stdout + narrative.stderr
    registry_path = home / ".agent-worktrees" / "repos.yaml"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["repos"]["notes"]["class"] = "reference"
    write(registry_path, registry)
    registry_before_refusal = registry_path.read_bytes()
    refused = invoke("list", "--role", "knowledge", "--json")
    assert refused.returncode != 0
    assert "bound knowledge related source" in json.loads(refused.stdout)["error"]
    assert "Traceback" not in refused.stdout + refused.stderr
    assert not allocations.exists()
    assert snapshot() == before
    assert configuration_path.read_bytes() == configuration_before
    assert registry_path.read_bytes() == registry_before_refusal
