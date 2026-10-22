"""Source declarations, verified availability, and producer-grounded target ownership."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from plugin_resolve.preparation import prepare
from plugin_resolve.reflection import build_plan
from plugin_resolve.registration import atomic_json
from plugin_resolve.settings import read_repo_settings
from test_plugin_preparation import init_checkout, local_plan


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Synthetic", "-c",
         "user.email=synthetic@example.invalid", *args],
        check=True, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


@pytest.fixture
def git_origin(tmp_path):
    root = tmp_path / "origin"
    init_checkout(root)
    for folder, marker in (("catalog-a", "first"), ("catalog-b", "other-location")):
        payload = root / folder / "helper"
        payload.mkdir(parents=True)
        atomic_json(payload / "plugin.json", {"name": "helper", "version": "1.0.0"})
        (payload / "marker.txt").write_text(marker)
        atomic_json(payload.parent / "marketplace.json", {
            "name": "git-fixture", "owner": {"name": "Synthetic"},
            "plugins": [{"name": "helper", "source": "helper", "version": "1.0.0"}],
        })
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "first fixture")
    first = git(root, "rev-parse", "HEAD")
    git(root, "tag", "first")
    (root / "catalog-a" / "helper" / "marker.txt").write_text("second")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "second fixture")
    second = git(root, "rev-parse", "HEAD")
    return root, first, second


@pytest.mark.parametrize("mode", ["copy", "reference"])
@pytest.mark.parametrize("selection,folder,marker", [
    ("first", "catalog-a", "first"), ("HEAD", "catalog-a", "second"),
    ("HEAD", "catalog-b", "other-location"), ("sha", "catalog-a", "first"),
])
def test_original_git_definition_drives_acquisition_before_install(
    tmp_path, git_origin, mode, selection, folder, marker,
):
    root, first, _second = git_origin
    target = tmp_path / "target"
    init_checkout(target)
    origin = {"source": "git", "url": root.as_uri(), "ref": selection if selection != "sha" else "HEAD",
              "path": folder}
    if selection == "sha":
        origin["sha"] = first
    definition = {"source": origin}
    plan = build_plan([{"source": "helper@git-fixture"}], [], marketplaces={"git-fixture": definition})
    log = tmp_path / "acquisition.json"
    environment = {**os.environ, "STRUCTURED_GIT_LOG": str(log), "COPILOT_HOME": str(tmp_path / "productive")}
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "structured_git_installer.py"))
    request = {"repo": str(target), **plan, "local_mode": mode}
    first_result = prepare(request, environment=environment, copilot_command=command)
    acquired = json.loads(log.read_text())
    assert acquired["definition_before_acquisition"] == definition
    assert acquired["payload"] == marker
    assert not Path(acquired["private_cache"]).exists()
    assert Path(first_result["helper@git-fixture"], "marker.txt").read_text() == marker
    assert acquired["acquired_commit"] == (first if selection in {"first", "sha"} else git_origin[2])
    assert read_repo_settings(target).marketplaces["git-fixture"] == definition
    assert not (tmp_path / "productive").exists()
    assert not list((target / ".git").rglob("snapshots"))
    assert not list((target / ".git" / "copilot-venue-plugins" / "marketplaces").glob("*/marketplace.json"))
    assert prepare(request, environment=environment, copilot_command=command) == first_result


def test_owned_git_definition_changes_and_old_directory_projection_migrate(tmp_path, git_origin):
    root, _first, _second = git_origin
    target = tmp_path / "target"
    init_checkout(target)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "structured_git_installer.py"))
    definition = {"source": {"source": "git", "url": root.as_uri(), "ref": "first", "path": "catalog-a"}}
    def request():
        return {"repo": str(target), **build_plan(
            [{"source": "helper@git-fixture"}], [], marketplaces={"git-fixture": definition},
        )}
    first = prepare(request(), copilot_command=command)
    path = target / ".github" / "copilot" / "settings.local.json"
    receipt = target / ".git" / "copilot-venue-plugins" / "ownership.json"
    old = {"source": {"source": "directory", "path": str(target / ".git" / "copilot-venue-plugins" /
                                                       "marketplaces" / "git-fixture")}}
    state = json.loads(receipt.read_text())
    state["marketplaceOrigins"] = {"git-fixture": definition}
    state["marketplaceOriginActive"] = {"git-fixture": True}
    state["extraKnownMarketplaces"]["git-fixture"] = old
    atomic_json(receipt, state)
    settings = json.loads(path.read_text())
    settings["extraKnownMarketplaces"]["git-fixture"] = old
    atomic_json(path, settings)
    assert prepare(request(), copilot_command=command) == first
    assert read_repo_settings(target).marketplaces["git-fixture"] == definition
    definition = {"source": {**definition["source"], "ref": "HEAD"}}
    changed = prepare(request(), copilot_command=command)
    assert changed != first
    assert Path(changed["helper@git-fixture"], "marker.txt").read_text() == "second"
    assert read_repo_settings(target).marketplaces["git-fixture"] == definition


def absolute_controller(controller, anchor):
    local_plan(controller)
    path = controller / ".github" / "copilot" / "settings.json"
    settings = json.loads(path.read_text())
    settings["extraKnownMarketplaces"]["local"]["source"]["path"] = str(anchor / ".ai")
    atomic_json(path, settings)


@pytest.mark.parametrize("mode", ["reference", "copy"])
def test_target_owned_absolute_anchor_rebinds_to_real_worktree_without_copy(tmp_path, mode):
    anchor, callee, controller = tmp_path / "anchor", tmp_path / "callee", tmp_path / "controller"
    local_plan(anchor)
    init_checkout(anchor)
    git(anchor, "worktree", "add", "--orphan", str(callee))
    local_plan(callee)
    absolute_controller(controller, anchor)
    plan = build_plan(
        [{"source": "sample@local"}], [controller], local_mode=mode, target_checkout=callee,
    )
    item = plan["plugins"][0]
    assert item["target_relative"]["marketplace_relative"] == ".ai"
    assert "archive" not in item and "reference" not in item
    # The target need not carry a native declaration for the relocation exception.
    (callee / ".github" / "copilot" / "settings.json").unlink()
    result = prepare({"repo": str(callee), **plan, "local_mode": mode})
    assert result == {"sample@local": str(callee / ".ai" / "sample")}
    assert read_repo_settings(callee).marketplaces["local"]["source"]["path"] == "./.ai"
    assert not list(callee.rglob("snapshots"))


def test_authored_relative_and_unrelated_wip_roots_are_not_relocated(tmp_path):
    source, target, controller = tmp_path / "source", tmp_path / "target", tmp_path / "controller"
    local_plan(source)
    init_checkout(source)
    init_checkout(target)
    absolute_controller(controller, source)
    plan = build_plan(
        [{"source": "sample@local"}], [controller], local_mode="reference",
        allow_external=True, target_checkout=target,
    )
    assert "target_relative" not in plan["plugins"][0]
    assert prepare({"repo": str(target), **plan, "local_mode": "reference"}) == {
        "sample@local": str(source / ".ai" / "sample"),
    }
    path = controller / ".github" / "copilot" / "settings.json"
    settings = json.loads(path.read_text())
    settings["extraKnownMarketplaces"]["local"]["source"]["path"] = "../source/.ai"
    atomic_json(path, settings)
    plan = build_plan(
        [{"source": "sample@local"}], [controller], local_mode="reference",
        allow_external=True, target_checkout=source,
    )
    assert not plan["plugins"][0]["local_source"]["authored_absolute"]
    assert "target_relative" not in plan["plugins"][0]


def test_provider_bound_target_relative_does_not_read_host_path_on_receiver(tmp_path):
    source, target, controller = tmp_path / "source", tmp_path / "target", tmp_path / "controller"
    local_plan(source)
    init_checkout(source)
    local_plan(target)
    init_checkout(target)
    absolute_controller(controller, source)
    plan = build_plan(
        [{"source": "sample@local"}], [controller], target_repository="registered-target",
        target_source_roots=[source],
    )
    source.rename(tmp_path / "host-no-longer-visible")
    (target / ".github" / "copilot" / "settings.json").unlink()
    assert prepare({"repo": str(target), **plan}) == {"sample@local": str(target / ".ai" / "sample")}
    bad = {**plan, "target_repository": "another-repository"}
    with pytest.raises(ValueError, match="execution repository"):
        prepare({"repo": str(target), **bad})


def test_global_inventory_leading_jsonc_header_uses_existing_bounded_reading_contract(tmp_path):
    target, home, original = tmp_path / "target", tmp_path / "home", tmp_path / "original"
    init_checkout(target)
    local_plan(original)
    home.mkdir()
    settings = {"extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": str(original / ".ai")}}}}
    raw = "// native settings header\n\n" + json.dumps(settings)
    (home / "settings.json").write_text(raw)
    plan = build_plan([{"source": "sample@local"}], [], local_mode="reference")
    assert prepare(
        {"repo": str(target), **plan, "local_mode": "reference"},
        environment={**os.environ, "COPILOT_HOME": str(home), "COPILOT_CACHE_HOME": str(home / "cache")},
    ) == {"sample@local": str(original / ".ai" / "sample")}
    assert (home / "settings.json").read_text() == raw


@pytest.mark.parametrize("mode", ["reference", "copy"])
def test_native_git_namespace_and_controller_local_source_are_both_preserved(tmp_path, mode):
    controller, target = tmp_path / "controller", tmp_path / "target"
    local_plan(controller)
    init_checkout(target)
    definition = {"source": {"source": "github", "repo": "example/native"}}
    directory = target / ".github" / "copilot"
    directory.mkdir(parents=True)
    atomic_json(directory / "settings.json", {
        "enabledPlugins": {"native@local": True}, "extraKnownMarketplaces": {"local": definition},
    })
    plan = build_plan([{"source": "sample@local"}], [controller], local_mode=mode)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    request = {"repo": str(target), **plan, "local_mode": mode}
    first = prepare(request, copilot_command=command)
    assert set(first) == {"sample@local", "native@local"}
    assert prepare(request, copilot_command=command) == first
    settings = read_repo_settings(target)
    assert settings.marketplaces["local"] == definition
    alias = next(source for source in settings.enabled if source.startswith("sample@"))
    assert alias != "sample@local"
    assert settings.marketplaces[alias.split("@")[1]]["source"]["source"] == "directory"
    assert "installation" in Path(first["native@local"]).parts
    if mode == "reference":
        assert first["sample@local"] == str(controller / ".ai" / "sample")
    else:
        assert "snapshots" in Path(first["sample@local"]).parts


@pytest.mark.parametrize("bad", ["escape", "wrong-kind", "not-authored-absolute", "nested", "linked"])
def test_target_relative_rejects_unproven_or_confused_paths(tmp_path, bad):
    source, target, controller = tmp_path / "source", tmp_path / "target", tmp_path / "controller"
    local_plan(source)
    init_checkout(source)
    local_plan(target)
    init_checkout(target)
    absolute_controller(controller, source)
    plan = build_plan(
        [{"source": "sample@local"}], [controller], target_repository="exact-binding",
        target_source_roots=[source],
    )
    (target / ".github" / "copilot" / "settings.json").unlink()
    item = plan["plugins"][0]
    if bad == "escape":
        item["target_relative"]["marketplace_relative"] = "../outside"
    elif bad == "wrong-kind":
        item["local_source"]["definition"]["source"] = {"source": "git", "url": "file:///unused"}
    elif bad == "not-authored-absolute":
        item["local_source"]["authored_absolute"] = False
    elif bad == "nested":
        init_checkout(target / ".ai" / "sample")
    else:
        import shutil
        shutil.rmtree(target / ".ai")
        try:
            (target / ".ai").symlink_to(source / ".ai", target_is_directory=True)
        except OSError:
            pytest.skip("native symlink creation is unavailable")
    with pytest.raises(ValueError):
        prepare({"repo": str(target), **plan})
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


def test_target_native_operator_origin_overrides_target_relative_rebinding(tmp_path):
    source, target, controller, operator = (
        tmp_path / "source", tmp_path / "target", tmp_path / "controller", tmp_path / "operator"
    )
    local_plan(source)
    init_checkout(source)
    local_plan(target)
    init_checkout(target)
    local_plan(operator)
    absolute_controller(controller, source)
    plan = build_plan(
        [{"source": "sample@local"}], [controller], target_repository="exact-binding",
        target_source_roots=[source],
    )
    definition = {"source": {"source": "directory", "path": str(operator / ".ai")}}
    atomic_json(target / ".github" / "copilot" / "settings.local.json", {
        "extraKnownMarketplaces": {"local": definition},
    })
    import shutil
    shutil.rmtree(target / ".ai")
    assert prepare({"repo": str(target), **plan}) == {"sample@local": str(operator / ".ai" / "sample")}
    assert read_repo_settings(target).marketplaces["local"] == definition
    atomic_json(target / ".github" / "copilot" / "settings.local.json", {
        "enabledPlugins": {"sample@local": False},
    })
    assert prepare({"repo": str(target), **plan}) == {}
