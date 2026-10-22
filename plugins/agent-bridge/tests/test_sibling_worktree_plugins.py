"""A same-repository child loads its own revision, not its controller's."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import plugin_resolve
import pytest

from agent_bridge import acquisition_plugins, transport
from agent_bridge.spawn_target import PluginRef
from plugin_resolve.preparation import prepare
from plugin_resolve.reflection import build_plan
from plugin_resolve.registration import atomic_json
from test_local_plugin_preparation import checkout, marketplace, target_for


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Synthetic",
         "-c", "user.email=synthetic@example.invalid", *args],
        capture_output=True, text=True, check=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


@pytest.fixture
def sibling_worktrees(tmp_path, monkeypatch):
    parent = checkout(tmp_path / "parent")
    marketplace(parent, "parent-revision")
    settings = parent / ".github" / "copilot" / "settings.json"
    document = json.loads(settings.read_text())
    document["enabledPlugins"] = {"sample@local": True}
    atomic_json(settings, document)
    git(parent, "add", ".")
    git(parent, "commit", "--quiet", "-m", "parent marketplace")
    child = tmp_path / "child"
    git(parent, "worktree", "add", "--quiet", "--detach", str(child))
    (child / ".ai" / "sample" / "selected.txt").write_text("child-revision")
    git(child, "add", ".")
    git(child, "commit", "--quiet", "-m", "child marketplace")
    assert git(parent, "rev-parse", "--path-format=absolute", "--git-common-dir") == git(
        child, "rev-parse", "--path-format=absolute", "--git-common-dir",
    )
    assert git(parent, "rev-parse", "HEAD") != git(child, "rev-parse", "HEAD")
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    monkeypatch.setattr(
        "agent_bridge.related_plugins.related_plugins_for_repo",
        lambda project, anchors: [PluginRef("sample@local")] if anchors == [parent] else [],
    )
    return parent, child


def independent_consumer(child, environment):
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import json,pathlib,sys;"
            "from plugin_resolve.resolve import resolve_repo_plugins;"
            "r=resolve_repo_plugins(sys.argv[1]);assert not r.unresolved;"
            "print(json.dumps({k:{'path':str(v),'marker':"
            "(v/'selected.txt').read_text()} for k,v in r.resolved.items()}))",
            str(child),
        ],
        cwd=child, env={
            **environment, "PYTHONPATH": str(Path(plugin_resolve.__file__).parents[1]),
        },
        capture_output=True, text=True, check=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout)


async def launch_selection(parent, child, mode, environment):
    target = target_for(child, parent)
    if mode == "acp":
        target.env = environment
        args, cwd, _env = await transport.resolve_local_launch(target)
        assert Path(cwd) == child
        return [args[i + 1] for i, arg in enumerate(args) if arg == "--plugin-dir"
                and args[i + 1] not in {"explicit", "request"}]
    if mode == "cli":
        request = SimpleNamespace(
            mode="cli", project="sample-project", caller_worktree=str(parent),
            caller_owner_ref=None, caller_session_id=None,
        )
        target = await acquisition_plugins.local_preparation_target(
            request, {"path": str(child)}, target, replace_target=replace,
        )
        plan = target.plugin_preparation_request
        assert plan["repo"] == str(child)
    else:
        plan = {
            **build_plan(
                [{"source": "sample@local"}], [parent], target_checkout=child,
            ), "repo": str(child),
        }
    return list(prepare(plan, environment=environment).values())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "cli", "copy"])
async def test_child_marketplace_wins_across_revisions_and_repeat(
    sibling_worktrees, tmp_path, mode,
):
    parent, child = sibling_worktrees
    home = tmp_path / "productive-profile"
    home.mkdir()
    preferences = b'{"unrelatedPreference":"keep","enabledPlugins":{"other@elsewhere":true}}\n'
    (home / "settings.json").write_bytes(preferences)
    environment = {**os.environ, "COPILOT_HOME": str(home)}
    original = {
        path: path.read_bytes() for path in (
            parent / ".ai" / "marketplace.json",
            parent / ".github" / "copilot" / "settings.json",
        )
    }
    expected = str(child / ".ai" / "sample")
    for marker in ("child-revision", "new-child-revision"):
        (child / ".ai" / "sample" / "selected.txt").write_text(marker)
        (parent / ".ai" / "sample" / "selected.txt").write_text("new-parent-revision")
        assert await launch_selection(parent, child, mode, environment) == [expected]
        assert independent_consumer(child, environment) == {
            "sample@local": {"path": expected, "marker": marker},
        }
    assert all(path.read_bytes() == content for path, content in original.items())
    assert (home / "settings.json").read_bytes() == preferences
    assert not (parent / ".github" / "copilot" / "settings.local.json").exists()
    git_dir = Path(git(child, "rev-parse", "--absolute-git-dir"))
    assert not list(git_dir.rglob("snapshots"))
    assert not list(git_dir.rglob("marketplace.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "cli", "copy"])
@pytest.mark.parametrize("choice", ["disabled", "missing"])
async def test_child_disable_or_missing_payload_never_falls_back_to_parent(
    sibling_worktrees, mode, choice,
):
    parent, child = sibling_worktrees
    if choice == "disabled":
        atomic_json(child / ".github" / "copilot" / "settings.local.json", {
            "enabledPlugins": {"sample@local": False}, "unrelatedPreference": "keep",
        })
        assert await launch_selection(parent, child, mode, dict(os.environ)) == []
        assert independent_consumer(child, dict(os.environ)) == {}
        document = json.loads(
            (child / ".github" / "copilot" / "settings.local.json").read_text(),
        )
        assert document["enabledPlugins"]["sample@local"] is False
        assert document["unrelatedPreference"] == "keep"
    else:
        (child / ".ai" / "sample" / "plugin.json").unlink()
        with pytest.raises(ValueError, match="Required local plugin is absent"):
            await launch_selection(parent, child, mode, dict(os.environ))
        assert not (child / ".github" / "copilot" / "settings.local.json").exists()
    assert (parent / ".ai" / "sample" / "plugin.json").is_file()
