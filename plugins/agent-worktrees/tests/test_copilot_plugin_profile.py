"""Project-native tool settings and independent personal receipt controls."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from plugin_resolve import read_repo_settings

from agent_worktrees import config as cfg, installer, knowledge_plugins as kp, pr_authority, repos
from agent_worktrees import copilot_plugin_profile as tool, unregister_cli


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}, **no_window_kwargs())


def _json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _overlay(root: Path) -> Path:
    return root / ".github" / "copilot" / "settings.local.json"


def _read(root: Path) -> dict:
    return json.loads(_overlay(root).read_text(encoding="utf-8"))


@pytest.fixture
def family(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: home / "repos.yaml")
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: home / "projects.yaml")
    projects, configurations = {}, {}
    entries = {}

    def create(name="tools", plugin="build@tools"):
        root = tmp_path / name
        root.mkdir()
        _git(root, "init", "-b", "main")
        (root / ".gitignore").write_text(".github/copilot/settings.local.json\n")
        _git(root, "add", ".gitignore")
        _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-m", "fixture")
        worktree = tmp_path / f"{name}-worktree"
        _git(root, "worktree", "add", "-b", f"{name}-work", str(worktree))
        _json(root / "profile.json", {"enabledPlugins": {plugin: True, "off@tools": False}})
        entries[name] = repos.RepoEntry(name, repo_class="worktree",
                                       paths={repos._current_platform(): str(root)})
        projects[name] = {"expose_agent": False}
        registered = repos.read_registry()
        registered.repos = dict(entries)
        repos.write_registry(registered)
        installer.write_projects_registry({"projects": projects})
        configuration = cfg.Config(srcroot=str(tmp_path), machine="fixture", platform=cfg.detect_platform(),
                                   repo_name=name, repos={name: cfg.RepoConfig(
                                       anchor=str(root), worktree_root=str(tmp_path / f"{name}.worktrees"),
                                       copilot_plugin_profile="profile.json")})
        configurations[name] = configuration
        return root, worktree, configuration

    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kw: configurations[name])
    return create


def test_optional_config_and_dropin_validation():
    from agent_worktrees import config_dropins, config_sources

    assert config_sources.parse_plugin_profile(None) is None
    assert config_sources.parse_plugin_profile("profile.json") == "profile.json"
    values: tuple[object, ...] = (True, 3, [], "")
    for value in values:
        with pytest.raises(ValueError, match="copilot_plugin_profile"):
            config_sources.parse_plugin_profile(value)
        assert config_dropins._validate_repo_fragment(
            {"copilot_plugin_profile": value}, location="repo")


def test_attributed_profile_dropin_uses_existing_input_seam(tmp_path):
    from plugin_activation import ActivePlugin, ActivationReport
    from dropin_registry import EntryDecision, ScanAuthority
    from agent_worktrees import config_dropins

    payload = tmp_path / "payload"
    target = payload / "config" / "profile.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("repos:\n  tools:\n    copilot_plugin_profile: profile.json\n")
    directory = tmp_path / "config.d"
    directory.mkdir()
    _json(directory / "profile.json", {
        "schema_version": 1, "plugin": "tools@fixture", "plugin_root": str(payload), "target": str(target)})
    active = ActivePlugin(source="tools@fixture", name="tools", marketplace="fixture",
                          root=payload.resolve(), scopes=("global",))
    report = config_dropins.scan_config_dropin_registry(
        directory, project_name="tools", activation_report=ActivationReport(
            authority=ScanAuthority.COMPLETE, decisions={"tools@fixture": EntryDecision.active(active)}))
    assert report.active_configs[0].raw_config["repos"]["tools"]["copilot_plugin_profile"] == "profile.json"


def test_no_profile_is_noop_even_with_unmanaged_jsonc(tmp_path):
    configuration = cfg.Config(srcroot="", machine="", platform="", repos={
        "tools": cfg.RepoConfig(anchor=str(tmp_path), worktree_root="")})
    _overlay(tmp_path).parent.mkdir(parents=True)
    _overlay(tmp_path).write_text("{ // untouched unmanaged document\n}")
    before = _overlay(tmp_path).read_bytes()
    assert tool.prepare_checkout(tmp_path, config=configuration)["action"] == "no-op"
    assert _overlay(tmp_path).read_bytes() == before


def test_register_stages_existing_family_and_removal_retains_personal(family):
    root, worktree, configuration = family()
    registry = installer.read_projects_registry()
    tool.reconcile_project("tools", root, registry)
    assert _read(root)["enabledPlugins"] == {"build@tools": True, "off@tools": False}
    assert _read(worktree)["enabledPlugins"] == _read(root)["enabledPlugins"]
    knowledge = root.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json",
          {"enabledPlugins": {"notes@personal": True}})
    _git(knowledge, "init", "-b", "main")
    kp.compose(worktree, knowledge, pair_id="pair", pair_kind="worktree")
    assert _read(worktree)[kp._OVERLAY_KEY]["version"] == 2
    tool.prepare_checkout(worktree, config=replace(
        configuration, repos={"tools": replace(configuration.default_repo, copilot_plugin_profile=None)}))
    data = _read(worktree)
    assert tool.KEY not in data
    assert data["enabledPlugins"] == {"notes@personal": True}
    assert data[kp._OVERLAY_KEY]["version"] == 1


def test_personal_optout_retains_tools_and_two_roots_share_knowledge(family):
    first, _, _ = family("first", "first@tools")
    second, _, _ = family("second", "second@tools")
    knowledge = first.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json",
          {"enabledPlugins": {"notes@personal": True}})
    for name, root in (("first", first), ("second", second)):
        tool.reconcile_project(name, root, installer.read_projects_registry())
        kp.compose(root, knowledge, pair_id="pair", pair_kind="anchor")
    kp._retire_disabled_overlay(first)
    assert _read(first)["enabledPlugins"] == {"first@tools": True, "off@tools": False}
    assert _read(second)["enabledPlugins"]["notes@personal"] is True
    assert "first@tools" not in _read(second)["enabledPlugins"]


def test_unmanaged_equal_and_modified_owned_choices_are_not_adopted(family):
    root, _, configuration = family()
    _json(_overlay(root), {"enabledPlugins": {"build@tools": True}, "theme": "mine"})
    summary = tool.prepare_checkout(root, config=configuration)
    assert not summary["conflicts"]["enabledPlugins"]
    assert "build@tools" not in _read(root)[tool.KEY]["enabledPlugins"]
    data = _read(root)
    data["enabledPlugins"]["off@tools"] = True
    _json(_overlay(root), data)
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts.*off@tools"):
        tool.prepare_checkout(root, config=configuration)
    tool.retire_project("tools")
    assert _read(root)["enabledPlugins"]["off@tools"] is True
    assert _read(root)["enabledPlugins"]["build@tools"] is True


def test_legacy_local_operator_choice_is_not_overridden(family):
    root, _, configuration = family()
    _json(root / ".claude" / "settings.local.json",
          {"enabledPlugins": {"build@tools": False, "off@tools": False}})
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts.*build@tools"):
        tool.prepare_checkout(root, config=configuration)
    assert not _overlay(root).exists()


@pytest.mark.parametrize("body", ["{", "{ // JSONC\n}", "[]"])
def test_malformed_overlay_refused_without_mutation(family, body):
    root, _, configuration = family()
    _overlay(root).parent.mkdir(parents=True)
    _overlay(root).write_text(body)
    with pytest.raises(kp.KnowledgePluginError):
        tool.prepare_checkout(root, config=configuration)
    assert _overlay(root).read_text() == body


@pytest.mark.parametrize("selected", ["../foreign.json", "C:\\foreign.json", "\\\\server\\profile.json", "profile.json:stream"])
def test_profile_path_refusal_before_settings_write(family, selected):
    root, _, configuration = family()
    with pytest.raises(kp.KnowledgePluginError, match="registered"):
        tool.prepare_checkout(root, config=replace(configuration, repos={
            "tools": replace(configuration.default_repo, copilot_plugin_profile=selected)}))
    assert not _overlay(root).exists()


@pytest.mark.parametrize("data", [
    {"enabledPlugins": {"build@tools": "false"}},
    {"permissions": {"allow": ["anything"]}},
    {"extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "../outside"}}}},
])
def test_profile_schema_and_local_marketplace_paths_refused(family, data):
    root, _, configuration = family()
    _json(root / "profile.json", data)
    with pytest.raises(kp.KnowledgePluginError):
        tool.prepare_checkout(root, config=configuration)
    assert not _overlay(root).exists()


def test_local_marketplace_staging_and_unsafe_output_refusal(family, monkeypatch):
    from types import SimpleNamespace

    root, worktree, configuration = family()
    (root / "plugins").mkdir()
    _json(root / "profile.json", {"extraKnownMarketplaces": {
        "local": {"source": {"source": "directory", "path": "./plugins"}}},
        "enabledPlugins": {"build@local": True}})
    tool.prepare_checkout(worktree, config=configuration)
    assert Path(_read(worktree)["extraKnownMarketplaces"]["local"]["source"]["path"]) == root / "plugins"
    before = _overlay(worktree).read_bytes()
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == _overlay(worktree).parent:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(kp.KnowledgePluginError, match="unsafe linked"):
        tool.prepare_checkout(worktree, config=configuration)
    assert _overlay(worktree).read_bytes() == before


@pytest.mark.parametrize("key,version", [(kp._OVERLAY_KEY, 77), (tool.KEY, True)])
def test_unsupported_version_preserves_mixed_document(family, key, version):
    root, _, configuration = family()
    tool.prepare_checkout(root, config=configuration)
    marker = _read(root)
    marker[key]["version"] = version
    _json(_overlay(root), marker)
    before = _overlay(root).read_bytes()
    with pytest.raises(kp.KnowledgePluginError, match="unsupported"):
        tool.prepare_checkout(root, config=configuration)
    assert _overlay(root).read_bytes() == before


def test_foreign_marker_and_personal_conflicts_are_explicit(family):
    first, _, configuration = family("first")
    second, _, _ = family("second")
    tool.prepare_checkout(second, config=cfg.load_project_config("second"))
    _json(_overlay(first), _read(second))
    before = _overlay(first).read_bytes()
    with pytest.raises(kp.KnowledgePluginError, match="foreign-root"):
        tool.prepare_checkout(first, config=configuration)
    assert _overlay(first).read_bytes() == before
    _overlay(first).unlink()
    knowledge = first.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json",
          {"enabledPlugins": {"build@tools": True}})
    kp.compose(first, knowledge)
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts.*build@tools"):
        tool.prepare_checkout(first, config=configuration)
    assert tool.KEY not in _read(first)


def test_unregister_bounce_and_partial_retry_keep_registration(family, monkeypatch):
    root, worktree, _ = family()
    tool.reconcile_project("tools", root, installer.read_projects_registry())
    monkeypatch.setattr(cfg, "project_name", lambda: "tools")
    monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: ["unsettled claim"])
    args = argparse.Namespace(force=False, keep_repo_entry=False)
    before = _overlay(root).read_bytes()
    assert unregister_cli._unregister_guarded(args) == 1
    assert _overlay(root).read_bytes() == before
    monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: [])
    original = kp._write_overlay
    def fail_second(path, data):
        if path == _overlay(worktree):
            raise kp.KnowledgePluginError("fixture write failure")
        return original(path, data)
    monkeypatch.setattr(kp, "_write_overlay", fail_second)
    assert unregister_cli._unregister_guarded(args) == 1
    assert "tools" in installer.read_projects_registry()["projects"]
    assert repos.find_repo("tools") is not None
    assert not _overlay(root).exists()
    monkeypatch.setattr(kp, "_write_overlay", original)
    monkeypatch.setattr(installer, "remove_project_binstub", lambda project: [])
    assert unregister_cli._unregister_guarded(args) == 0
    assert "tools" not in installer.read_projects_registry()["projects"]
    assert repos.find_repo("tools") is None
    assert not _overlay(worktree).exists()


@pytest.mark.guard
def test_fresh_native_cli_register_create_and_plain_settings_consumer(tmp_path):
    """Real source CLI and plain plugin_resolve consumer; no native inference."""
    home = tmp_path / "fresh-home"
    root = tmp_path / "tools"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / ".gitignore").write_text(".github/copilot/settings.local.json\n")
    _json(root / "profile.json", {"enabledPlugins": {"build@tools": True, "off@tools": False}})
    settings = root / ".agent-worktrees" / "config.yaml"
    settings.parent.mkdir()
    settings.write_text("default_branch: main\nheadless: true\n"
                        "copilot_plugin_profile: profile.json\n")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "fixture")
    registry = home / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("repos:\n  tools:\n    class: worktree\n    agent: false\n"
                        f"    {repos._current_platform()}: {json.dumps(str(root))}\n")
    environment = {**os.environ, "AGENT_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
                   "AGENT_WORKTREES_STATUS_MONITOR": "0", "GIT_CONFIG_GLOBAL": os.devnull}
    if os.name == "nt":
        environment["HOMEDRIVE"], environment["HOMEPATH"] = os.path.splitdrive(str(home))
    for key in ("COPILOT_AGENT_SESSION_ID", "AGENT_WORKTREES_PROJECT", "AGENT_WORKTREES_PARENT_ID"):
        environment.pop(key, None)
    def cli(*args, expected=0):
        result = subprocess.run([sys.executable, "-m", "agent_worktrees", "--project", "tools", *args],
                                cwd=root, env=environment, capture_output=True, text=True,
                                timeout=90, **no_window_kwargs())
        assert result.returncode == expected, result.stderr + result.stdout
        return result
    cli("register", "tools", "--repo-dir", str(root), "--headless", "--no-agent")
    assert _read(root)["enabledPlugins"]["off@tools"] is False
    result = cli("create", "--json")
    data = json.loads(result.stdout[result.stdout.index("{"):])
    checkout = Path(data["worktree"]["path"])
    assert _read(checkout)["enabledPlugins"]["build@tools"] is True
    consumer = subprocess.run([sys.executable, "-c",
        "import json,sys; from plugin_resolve import read_repo_settings; "
        "print(json.dumps(read_repo_settings(sys.argv[1]).enabled))", str(checkout)],
        cwd=checkout, env=environment, capture_output=True, text=True, timeout=20, **no_window_kwargs())
    assert consumer.returncode == 0, consumer.stderr
    assert json.loads(consumer.stdout) == {"build@tools": True, "off@tools": False}
    _json(root / ".claude" / "settings.local.json", {"enabledPlugins": {"build@tools": False}})
    _git(root, "add", ".claude")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-m", "fixture operator choice")
    failed = cli("create", "--json", expected=3)
    assert "tool plugin conflicts" in failed.stdout + failed.stderr
    failure = json.loads(failed.stdout[failed.stdout.index("{"):])
    assert failure["created"] is True
    assert failure["launch_seed"]["status"] == "unconfirmed"
    assert failure["recovery"]["repeat_new"] is False
    assert read_repo_settings(Path(failure["worktree"]["path"])).enabled["build@tools"] is False


@pytest.mark.parametrize("owned", [True, False])
def test_personal_exclusion_preserves_tool_and_unmanaged_selection(family, owned):
    root, _, configuration = family()
    source = "knowledge-harness@personal"
    if owned:
        _json(root / "profile.json", {"enabledPlugins": {source: True}})
        tool.prepare_checkout(root, config=configuration)
    else:
        _json(_overlay(root), {
            "enabledPlugins": {source: True},
            "extraKnownMarketplaces": {"personal": {"source": {
                "source": "directory", "path": str(root.parent / "knowledge" / "plugins")}}}})
    knowledge = root.parent / "knowledge"
    (knowledge / "plugins").mkdir(parents=True)
    _json(knowledge / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {source: True},
        "extraKnownMarketplaces": {"personal": {"source": {"source": "directory", "path": "./plugins"}}}})
    summary = kp.compose(root, knowledge)
    assert _read(root)["enabledPlugins"][source] is True
    assert source in summary["excluded_enabled_plugins"]
    assert source not in _read(root)[kp._OVERLAY_KEY]["enabledPlugins"]
    if owned:
        assert _read(root)[tool.KEY]["enabledPlugins"][source] is True


def test_tool_conflict_blocks_launch_preparation(family, monkeypatch):
    from agent_worktrees import local_cache_refresh

    root, worktree, configuration = family()
    _json(_overlay(worktree), {"enabledPlugins": {"build@tools": False}})
    def unexpected_refresh(*args, **kwargs):
        pytest.fail("instruction publication cannot report full preparation after a roster conflict")
    monkeypatch.setattr(local_cache_refresh, "refresh_local_cache", unexpected_refresh)
    result = local_cache_refresh.prepare_for_launch(worktree, config=configuration)
    assert result.status == "failed"
    assert result.blocking == 1
    assert "tool plugin conflicts" in result.detail
    assert _read(worktree)["enabledPlugins"]["build@tools"] is False


def test_personal_composition_refuses_overlapping_contributor_receipts(family):
    root, _, configuration = family()
    tool.prepare_checkout(root, config=configuration)
    data = _read(root)
    data[kp._OVERLAY_KEY]["enabledPlugins"]["build@tools"] = True
    _json(_overlay(root), data)
    before = _overlay(root).read_bytes()
    knowledge = root.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json",
          {"enabledPlugins": {"notes@personal": True}})
    with pytest.raises(kp.KnowledgePluginError, match="ambiguous tool/personal"):
        kp.compose(root, knowledge)
    assert _overlay(root).read_bytes() == before


@pytest.mark.parametrize("body", [
    '{"enabledPlugins": {}, "enabledPlugins": {"build@tools": true}}',
    '{"enabledPlugins": {"build@tools": true, "build@tools": false}}',
])
def test_duplicate_local_keys_refused_unchanged_by_both_contributors(family, body):
    root, _, configuration = family()
    _overlay(root).parent.mkdir(parents=True)
    _overlay(root).write_text(body, encoding="utf-8")
    before = _overlay(root).read_bytes()
    with pytest.raises(kp.KnowledgePluginError, match="duplicate JSON key"):
        tool.prepare_checkout(root, config=configuration)
    assert _overlay(root).read_bytes() == before
    knowledge = root.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json",
          {"enabledPlugins": {"notes@personal": True}})
    with pytest.raises(kp.KnowledgePluginError, match="duplicate JSON key"):
        kp.compose(root, knowledge)
    assert _overlay(root).read_bytes() == before


@pytest.mark.parametrize("target", ["anchor", "worktree"])
def test_unregister_wins_before_delayed_first_stage_without_orphan(family, monkeypatch, target):
    root, worktree, configuration = family()
    checkout = root if target == "anchor" else worktree
    profile, desired = tool._profile(root, configuration.default_repo.copilot_plugin_profile)
    registry = installer.read_projects_registry()
    registry["projects"]["tools"][tool.REGISTRY_KEY] = tool._identity(root)
    installer.write_projects_registry(registry)
    monkeypatch.setattr(cfg, "project_name", lambda: "tools")
    monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: [])
    monkeypatch.setattr(installer, "remove_project_binstub", lambda project: [])
    pending, proceed = threading.Event(), threading.Event()

    def delayed_stage():
        pending.set()
        assert proceed.wait(30), "first-stage test was not released"
        return tool._stage(root, checkout, "tools", profile, desired)

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(delayed_stage)
        try:
            assert pending.wait(10)
            assert not _overlay(checkout).exists()
            assert unregister_cli.cmd_unregister(
                argparse.Namespace(force=False, keep_repo_entry=False)) == 0
            assert "tools" not in installer.read_projects_registry()["projects"]
        finally:
            proceed.set()
        with pytest.raises(kp.KnowledgePluginError, match="not registered"):
            result.result(timeout=30)
    assert repos.find_repo("tools") is None
    assert not _overlay(root).exists()
    assert not _overlay(worktree).exists()


def test_prepare_keeps_owner_guard_through_actual_first_write(family, monkeypatch):
    root, _, configuration = family()
    monkeypatch.setattr(cfg, "project_name", lambda: "tools")
    monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: [])
    monkeypatch.setattr(installer, "remove_project_binstub", lambda project: [])
    original_guard, original_write = pr_authority.guard, kp._write_overlay
    writing = threading.Event()

    @contextmanager
    def bounded_guard(*, timeout=None):
        # An immediate real-lock attempt finishes while the first writer is paused.
        if threading.current_thread().name.startswith("contended-unregister"):
            timeout = 0
        with original_guard(timeout=timeout):
            yield

    monkeypatch.setattr(pr_authority, "guard", bounded_guard)

    def unregister_during_write():
        assert writing.wait(10), "first writer was not reached"
        return unregister_cli.cmd_unregister(
            argparse.Namespace(force=False, keep_repo_entry=False))

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="contended-unregister") as pool:
        result = pool.submit(unregister_during_write)

        def paused_write(path, data):
            writing.set()
            assert result.result(timeout=10) == 1
            assert "tools" in installer.read_projects_registry()["projects"]
            return original_write(path, data)

        monkeypatch.setattr(kp, "_write_overlay", paused_write)
        try:
            tool.prepare_checkout(root, config=configuration)
        finally:
            writing.set()
    assert _read(root)["enabledPlugins"]["build@tools"] is True
    assert repos.find_repo("tools") is not None


@pytest.mark.parametrize("selected", [" plugins", "plugins "])
def test_local_marketplace_checks_the_exact_normalized_emitted_path(family, selected):
    if os.name == "nt" and selected.endswith(" "):
        pytest.skip("Win32 normalizes trailing-space directory names; POSIX witness requires native POSIX")
    root, _, configuration = family()
    (root / selected).mkdir()
    foreign = root.parent / "foreign-directory"
    foreign.mkdir()
    try:
        (root / "plugins").symlink_to(foreign, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"host cannot create the required filesystem link: {type(exc).__name__}")
    _json(root / "profile.json", {"extraKnownMarketplaces": {
        "local": {"source": {"source": "directory", "path": selected}}}})
    with pytest.raises(kp.KnowledgePluginError, match="unsafe linked"):
        tool.prepare_checkout(root, config=configuration)
    assert not _overlay(root).exists()


@pytest.mark.parametrize("source,edited", [("build@tools", 1), ("off@tools", 0)])
def test_numeric_operator_edits_are_not_boolean_receipt_matches(family, source, edited):
    root, _, configuration = family()
    tool.prepare_checkout(root, config=configuration)
    data = _read(root)
    data["enabledPlugins"][source] = edited
    _json(_overlay(root), data)
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts"):
        tool.prepare_checkout(root, config=configuration)
    assert type(_read(root)["enabledPlugins"][source]) is int
    tool.prepare_checkout(root, config=replace(configuration, repos={
        "tools": replace(configuration.default_repo, copilot_plugin_profile=None)}))
    assert _read(root)["enabledPlugins"][source] == edited
    assert type(_read(root)["enabledPlugins"][source]) is int


def test_nested_marketplace_numeric_edit_is_preserved(family):
    root, _, configuration = family()
    _json(root / "profile.json", {"extraKnownMarketplaces": {
        "remote": {"source": {"source": "github", "repo": "example/plugins"},
                   "options": {"strict": True}}}})
    tool.prepare_checkout(root, config=configuration)
    data = _read(root)
    data["extraKnownMarketplaces"]["remote"]["options"]["strict"] = 1
    _json(_overlay(root), data)
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts"):
        tool.prepare_checkout(root, config=configuration)
    tool.prepare_checkout(root, config=replace(configuration, repos={
        "tools": replace(configuration.default_repo, copilot_plugin_profile=None)}))
    assert type(_read(root)["extraKnownMarketplaces"]["remote"]["options"]["strict"]) is int


def test_git_family_probes_ignore_inherited_repository_selectors(family, monkeypatch):
    root, _, configuration = family()
    foreign, _, _ = family("foreign", "foreign@tools")
    for key, value in (("GIT_DIR", foreign / ".git"), ("GIT_COMMON_DIR", foreign / ".git"),
                       ("GIT_WORK_TREE", foreign)):
        monkeypatch.setenv(key, str(value))
    tool.prepare_checkout(root, config=configuration)
    assert _read(root)[tool.KEY]["root"] == tool._identity(root)
    assert not _overlay(foreign).exists()


def test_family_evidence_is_reused_only_in_one_guarded_reconcile(family, monkeypatch):
    root, _, _ = family()
    for index in range(2):
        _git(root, "worktree", "add", "-b", f"extra-{index}", str(root.parent / f"extra-{index}"))
    common, census = tool._common, tool.git_ops.list_worktree_paths
    calls = {"common": 0, "census": 0}
    def counted_common(path):
        calls["common"] += 1
        return common(path)
    def counted_census(**kwargs):
        calls["census"] += 1
        return census(**kwargs)
    monkeypatch.setattr(tool, "_common", counted_common)
    monkeypatch.setattr(tool.git_ops, "list_worktree_paths", counted_census)
    for _ in range(2):
        calls.update(common=0, census=0)
        tool.reconcile_project("tools", root, installer.read_projects_registry())
        assert calls == {"common": 8, "census": 1}  # Four family probes plus four final target probes.


@pytest.mark.parametrize("desired", [True, False])
def test_legacy_match_cannot_hide_higher_precedence_native_selection(family, desired):
    root, _, configuration = family()
    _json(root / "profile.json", {"enabledPlugins": {"build@tools": desired}})
    _json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"build@tools": not desired}})
    _json(root / ".claude" / "settings.local.json", {"enabledPlugins": {"build@tools": desired}})
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts"):
        tool.prepare_checkout(root, config=configuration)
    assert read_repo_settings(root).enabled["build@tools"] is (not desired)


@pytest.mark.parametrize("operation", ["stage", "unregister"])
def test_foreign_registered_family_member_is_refused_before_any_mutation(family, monkeypatch, operation):
    root, worktree, configuration = family()
    tool.reconcile_project("tools", root, installer.read_projects_registry())
    repos.add_repo("foreign", str(worktree), repo_class="reference", agent=False)
    before = {path: _overlay(path).read_bytes() for path in (root, worktree)}
    if operation == "stage":
        with pytest.raises(kp.KnowledgePluginError, match="another project"):
            tool.prepare_checkout(root, config=configuration)
    else:
        monkeypatch.setattr(cfg, "project_name", lambda: "tools")
        monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: [])
        assert unregister_cli.cmd_unregister(argparse.Namespace(force=False, keep_repo_entry=False)) == 1
        assert "tools" in installer.read_projects_registry(strict=True)["projects"]
    assert repos.find_repo("tools") is not None
    assert repos.find_repo("foreign") is not None
    assert all(_overlay(path).read_bytes() == raw for path, raw in before.items())


def test_unregister_cannot_treat_unreadable_project_registry_as_absent(family, monkeypatch, capsys):
    root, _, configuration = family()
    tool.prepare_checkout(root, config=configuration)
    before = _overlay(root).read_bytes()
    monkeypatch.setattr(cfg, "project_name", lambda: "tools")
    monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: [])
    def unavailable(*, strict=False):
        if strict:
            raise OSError("fixture project registry unreadable")
        return {"projects": {}}
    monkeypatch.setattr(installer, "read_projects_registry", unavailable)
    assert unregister_cli.cmd_unregister(argparse.Namespace(force=False, keep_repo_entry=False)) == 1
    assert repos.find_repo("tools") is not None
    assert _overlay(root).read_bytes() == before
    assert "Project 'tools' unregistered" not in capsys.readouterr().out


@pytest.mark.parametrize("probe", ["common", "census"])
def test_actual_git_errors_produce_noexec_and_retain_retirement_custody(family, monkeypatch, capfd, probe):
    from types import SimpleNamespace
    from agent_worktrees import resolve_cli

    root, _, configuration = family()
    tool.prepare_checkout(root, config=configuration)
    before = _overlay(root).read_bytes()
    def failed(*args, **kwargs):
        raise tool.git_ops.GitError(["git", "rev-parse"], 128, "fixture Git custody failure")
    monkeypatch.setattr(tool.git_ops, "git" if probe == "common" else "list_worktree_paths", failed)
    args = argparse.Namespace(json=True, base=True, new_worktree=False, auto=False, machine=None,
                              worktree_id=None, codename=None, dry_run=False, defer_projections=True)
    state = resolve_cli.ResolveCommandState.from_args(args)
    state.config = configuration
    monkeypatch.setattr(resolve_cli, "_preflight_launch", lambda *a: SimpleNamespace(error=None))
    assert resolve_cli._resolve_json_mode(state) == 3
    output = json.loads(capfd.readouterr().out)
    assert "Git custody failure" in output["error"]
    assert "cmd" not in output and output.get("action") != "exec"
    with pytest.raises(kp.KnowledgePluginError, match="Git custody failure"):
        tool.retire_project("tools")
    assert "tools" in installer.read_projects_registry(strict=True)["projects"]
    assert _overlay(root).read_bytes() == before


@pytest.mark.parametrize("setting", ["enabledPlugins", "extraKnownMarketplaces"])
def test_missing_personal_live_value_does_not_transfer_receipt_to_tools(family, setting):
    root, _, configuration = family()
    key, value = ("shared@remote", True) if setting == "enabledPlugins" else (
        "remote", {"source": {"source": "github", "repo": "example/plugins"}})
    knowledge = root.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json", {setting: {key: value}})
    kp.compose(root, knowledge)
    data = _read(root)
    data[setting].pop(key)
    _json(_overlay(root), data)
    before = _overlay(root).read_bytes()
    _json(root / "profile.json", {setting: {key: value}})
    with pytest.raises(kp.KnowledgePluginError, match="tool plugin conflicts"):
        tool.prepare_checkout(root, config=configuration)
    assert _overlay(root).read_bytes() == before
    assert tool.KEY not in _read(root)


@pytest.mark.parametrize("setting", ["enabledPlugins", "extraKnownMarketplaces"])
def test_missing_tool_live_value_does_not_transfer_receipt_to_personal(family, setting):
    root, _, configuration = family()
    key, value = ("shared@remote", True) if setting == "enabledPlugins" else (
        "remote", {"source": {"source": "github", "repo": "example/plugins"}})
    _json(root / "profile.json", {setting: {key: value}})
    tool.prepare_checkout(root, config=configuration)
    data = _read(root)
    data[setting].pop(key)
    _json(_overlay(root), data)
    knowledge = root.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json", {setting: {key: value}})
    summary = kp.compose(root, knowledge)
    personal_key = "enabledPlugins" if setting == "enabledPlugins" else "marketplaces"
    assert key in summary["conflicts"]["enabled_plugins" if setting == "enabledPlugins" else "marketplaces"]
    assert key not in _read(root).get(setting, {})
    assert key not in _read(root)[kp._OVERLAY_KEY][personal_key]
    assert _read(root)[tool.KEY][setting][key] == value


@pytest.mark.parametrize("operation", ["stage", "unregister"])
def test_linked_git_metadata_is_refused_before_foreign_family_access(family, monkeypatch, operation):
    root, _, configuration = family()
    tool.prepare_checkout(root, config=configuration)
    before = _overlay(root).read_bytes()
    foreign = root.parent / "unregistered-anchor"
    foreign.mkdir()
    _git(foreign, "init", "-b", "main")
    (root / ".git").rename(root / ".git.saved")
    try:
        (root / ".git").symlink_to(foreign / ".git", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"host cannot create the required filesystem link: {type(exc).__name__}")
    if operation == "stage":
        with pytest.raises(kp.KnowledgePluginError, match="unsafe linked"):
            tool.prepare_checkout(root, config=configuration)
    else:
        monkeypatch.setattr(cfg, "project_name", lambda: "tools")
        monkeypatch.setattr(unregister_cli, "_unregister_blockers", lambda project: [])
        assert unregister_cli.cmd_unregister(argparse.Namespace(force=False, keep_repo_entry=False)) == 1
    assert _overlay(root).read_bytes() == before
    assert not _overlay(foreign).exists()
    assert "tools" in installer.read_projects_registry(strict=True)["projects"]
    assert repos.find_repo("tools") is not None


def test_rejected_tool_marketplace_cannot_enable_new_personal_dependents(family):
    root, _, configuration = family()
    definition = {"source": {"source": "github", "repo": "example/plugins"}}
    _json(root / "profile.json", {"extraKnownMarketplaces": {"remote": definition}})
    tool.prepare_checkout(root, config=configuration)
    data = _read(root)
    data["extraKnownMarketplaces"].pop("remote")
    data["enabledPlugins"] = {"operator@independent": True}
    _json(_overlay(root), data)
    knowledge = root.parent / "knowledge"
    _json(knowledge / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"remote": definition},
        "enabledPlugins": {"notes@remote": True},
    })
    summary = kp.compose(root, knowledge)
    result = _read(root)
    assert summary["conflicts"] == {"marketplaces": ["remote"], "enabled_plugins": ["notes@remote"]}
    assert result["enabledPlugins"] == {"operator@independent": True}
    assert "remote" not in result["extraKnownMarketplaces"]
    assert not result[kp._OVERLAY_KEY]["enabledPlugins"]
    assert summary["enabled_plugins"] == [] and summary["count"] == 0


def test_tool_only_guard_does_not_disable_markerless_personal_repointing(family):
    root, _, configuration = family()
    anchor, pair = root.parent / "knowledge-anchor", root.parent / "knowledge-pair"
    definition = {"source": {"source": "directory", "path": "./.ai"}}
    for path, plugin in ((anchor, "stale@personal"), (pair, "current@personal")):
        (path / ".ai").mkdir(parents=True)
        _json(path / ".github" / "copilot" / "settings.json", {
            "extraKnownMarketplaces": {"personal": definition}, "enabledPlugins": {plugin: True},
        })
    _json(_overlay(root), {
        "extraKnownMarketplaces": {"personal": {
            "source": {"source": "directory", "path": (anchor / ".ai").resolve().as_posix()}}},
        "enabledPlugins": {"stale@personal": True},
    })
    tool.prepare_checkout(root, config=configuration)
    receipt = _read(root)[tool.KEY]
    assert _read(root)[kp._OVERLAY_KEY] == {"version": 2, "marketplaces": {}, "enabledPlugins": {}}
    summary = kp.compose(root, pair, legacy_knowledge_path=anchor, pair_id="fixture-pair")
    result = _read(root)
    assert Path(result["extraKnownMarketplaces"]["personal"]["source"]["path"]) == (pair / ".ai").resolve()
    assert result["enabledPlugins"]["current@personal"] is True
    assert result["enabledPlugins"]["stale@personal"] is True
    assert result["enabledPlugins"]["build@tools"] is True
    assert result[tool.KEY] == receipt
    assert result[kp._OVERLAY_KEY]["pairId"] == "fixture-pair"
    assert "personal" not in summary["conflicts"]["marketplaces"]
    assert "current@personal" not in summary["conflicts"]["enabled_plugins"]


@pytest.mark.parametrize("name", ["caf\u00e9", "ending ", "line\nbreak"], ids=["quoted-unicode", "trailing-space", "newline"])
def test_git_family_paths_remain_exact_through_stage_and_retirement(family, name):
    if os.name == "nt" and (name.endswith(" ") or "\n" in name):
        pytest.skip("This pathname requires native POSIX filesystem semantics")
    root, worktree, configuration = family()
    checkout = root.parent / name
    _git(root, "config", "core.quotePath", "true")
    _git(root, "worktree", "add", "-b", "special-path", str(checkout))
    tool.reconcile_project("tools", root, installer.read_projects_registry(strict=True))
    tool.prepare_checkout(checkout, config=configuration)
    assert checkout in tool.git_ops.list_worktree_paths(cwd=root, isolated_repository=True)
    assert _read(checkout)["enabledPlugins"]["build@tools"] is True
    tool.retire_project("tools")
    assert all(not _overlay(path).exists() for path in (root, worktree, checkout))


@pytest.mark.skipif(os.name == "nt", reason="A trailing-space directory requires native POSIX semantics")
def test_common_directory_probe_preserves_a_bare_anchor_trailing_space(tmp_path):
    root = tmp_path / "bare "
    root.mkdir()
    _git(root, "init", "--bare")
    assert tool._common(root) == tool._identity(root)
