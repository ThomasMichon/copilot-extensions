from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from dropin_registry import EntryStatus, ScanAuthority

from plugin_activation import (
    ActivePlugin,
    normalize_remote,
    resolve_active_plugins,
    resolver,
)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _plugin(
    root: Path,
    marketplace: str,
    name: str,
    *,
    source: str | None = None,
    plugin_root: str | None = None,
) -> Path:
    market = root / ".ai"
    manifest: dict[str, object] = {
        "name": marketplace,
        "plugins": [{"name": name, "source": source or f"./{name}"}],
    }
    if plugin_root is not None:
        manifest["metadata"] = {"pluginRoot": plugin_root}
    _write_json(market / ".claude-plugin" / "marketplace.json", manifest)
    plugin = market / (plugin_root or "") / (source or name)
    _write_json(plugin / ".claude-plugin" / "plugin.json", {"name": name})
    return plugin.resolve()


def _settings(
    repo: Path,
    marketplace: str,
    name: str,
    *,
    enabled: object = True,
) -> Path:
    path = repo / ".github" / "copilot" / "settings.json"
    _write_json(
        path,
        {
            "extraKnownMarketplaces": {
                marketplace: {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {f"{name}@{marketplace}": enabled},
        },
    )
    return path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _register_project(
    home: Path,
    name: str,
    repo: Path,
    remote: str,
    *,
    actual_remote: str | None = None,
) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", actual_remote or remote)
    aw = home / ".agent-worktrees"
    aw.mkdir(parents=True, exist_ok=True)
    projects_path = aw / "projects.yaml"
    repos_path = aw / "repos.yaml"
    projects = (
        yaml.safe_load(projects_path.read_text(encoding="utf-8"))
        if projects_path.exists()
        else {"projects": {}}
    )
    projects["projects"][name] = {"config_dir": f"~/.{name}"}
    projects_path.write_text(yaml.safe_dump(projects), encoding="utf-8")
    repos = (
        yaml.safe_load(repos_path.read_text(encoding="utf-8"))
        if repos_path.exists()
        else {"repos": {}}
    )
    platform_key = "windows" if os.name == "nt" else "linux"
    repos["repos"][name] = {
        platform_key: str(repo),
        "remote": remote,
        "class": "worktree",
    }
    repos_path.write_text(yaml.safe_dump(repos), encoding="utf-8")


def _installed(home: Path, marketplace: str, name: str, *, manifest_name=None) -> Path:
    root = home / ".copilot" / "installed-plugins" / marketplace / name
    _write_json(root / "plugin.json", {"name": manifest_name or name})
    return root.resolve()


def _prior(source: str = "demo@local") -> ActivePlugin:
    name, marketplace = source.split("@", 1)
    return ActivePlugin(
        source=source,
        name=name,
        marketplace=marketplace,
        root=Path("prior"),
        scopes=("project:prior",),
    )


def test_remote_normalization_for_network_forms():
    expected = "network:github.com/example/repo"
    assert normalize_remote("https://github.com/example/repo.git") == expected
    assert normalize_remote("git@github.com:example/repo.git") == expected
    assert normalize_remote("ssh://git@github.com/example/repo.git") == expected
    assert normalize_remote("https://GITHUB.COM/example/repo/") == expected
    assert normalize_remote("https://github.com/Example/repo.git") != expected


def test_remote_normalization_for_local_paths():
    windows = "file:c:\\src\\example\\repo"
    assert normalize_remote(r"C:\Src\Example\repo.GIT") == windows
    assert normalize_remote("file:///C:/Src/Example/repo.git") == windows
    assert normalize_remote("file:C:/Src/Example/repo.git") == windows
    assert normalize_remote(r"file:C:\Src\Example\repo.git") == windows
    unc = "file:\\\\server\\share\\repo"
    assert normalize_remote(r"\\SERVER\Share\repo.git") == unc
    assert normalize_remote("file://server/Share/repo.git") == unc
    assert normalize_remote("file:////server/Share/repo.git") == unc
    assert normalize_remote("/srv/Example/repo.git") == "file:/srv/Example/repo"
    assert normalize_remote("file:///srv/Example/repo.git") == (
        "file:/srv/Example/repo"
    )


@pytest.mark.parametrize(
    "remote",
    [
        "https://[bad",
        "https://example.com:bad/repo",
        "https://example.com/repo.git?identity=one",
        "https://example.com/repo.git#fragment",
        "",
        None,
    ],
)
def test_remote_normalization_is_fail_safe(remote):
    assert normalize_remote(remote) is None


def test_no_settings_or_project_registry_is_absent(tmp_path):
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.ABSENT
    assert report.active == {}
    assert report.reconcile({"demo@local": _prior()}) == {}


def test_global_local_plugin_is_active(tmp_path):
    plugin = _plugin(tmp_path / ".copilot", "local", "demo")
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"demo@local": True},
        },
    )
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.COMPLETE
    assert report.active["demo@local"].root == plugin
    assert report.active["demo@local"].scopes == ("global",)


def test_local_override_disables_base_setting(tmp_path):
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {"enabledPlugins": {"demo@local": True}},
    )
    _write_json(
        tmp_path / ".copilot" / "settings.local.json",
        {"enabledPlugins": {"demo@local": False}},
    )
    _installed(tmp_path, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.COMPLETE
    assert report.active == {}
    assert report.decisions == {}


@pytest.mark.parametrize("value", ["false", 1, None])
def test_non_boolean_override_is_indeterminate(tmp_path, value):
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {"enabledPlugins": {"demo@local": True}},
    )
    _write_json(
        tmp_path / ".copilot" / "settings.local.json",
        {"enabledPlugins": {"demo@local": value}},
    )
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}
    assert any(f.reason == "invalid-entry" for f in report.findings)


def test_malformed_local_override_is_indeterminate(tmp_path):
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {"enabledPlugins": {"demo@local": True}},
    )
    override = tmp_path / ".copilot" / "settings.local.json"
    override.write_text("{", encoding="utf-8")
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}
    assert any(f.entry == str(override) and f.status == "indeterminate" for f in report.findings)


def test_unreadable_global_settings_is_indeterminate(tmp_path, monkeypatch):
    settings = tmp_path / ".copilot" / "settings.json"
    _write_json(settings, {"enabledPlugins": {"demo@local": True}})
    original = Path.read_text

    def denied(path: Path, *args, **kwargs):
        if path == settings:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": _prior()})


def test_registered_project_plugin_is_active(tmp_path):
    repo = tmp_path / "src" / "demo-repo"
    remote = "https://github.com/example/demo-repo.git"
    _register_project(tmp_path, "demo-repo", repo, remote)
    plugin = _plugin(repo, "local", "demo")
    _settings(repo, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.COMPLETE
    assert report.active["demo@local"].root == plugin
    assert report.active["demo@local"].scopes == ("project:demo-repo",)


def test_wrong_registered_remote_cannot_authorize(tmp_path):
    repo = tmp_path / "src" / "demo-repo"
    _register_project(
        tmp_path,
        "demo-repo",
        repo,
        "https://github.com/example/expected.git",
        actual_remote="https://github.com/example/other.git",
    )
    _plugin(repo, "local", "demo")
    _settings(repo, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.COMPLETE
    assert "demo@local" not in report.active
    assert any(f.reason == "identity-mismatch" for f in report.findings)


def test_malformed_registered_remote_is_indeterminate(tmp_path):
    repo = tmp_path / "src" / "demo-repo"
    _register_project(
        tmp_path,
        "demo-repo",
        repo,
        "https://[bad",
        actual_remote="https://github.com/example/demo-repo.git",
    )
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}
    assert any(f.reason == "registry-indeterminate" for f in report.findings)


def test_query_qualified_registered_remote_is_indeterminate(tmp_path):
    repo = tmp_path / "src" / "demo-repo"
    _register_project(
        tmp_path,
        "demo-repo",
        repo,
        "https://git.example/repo.git?identity=trusted",
        actual_remote="https://git.example/repo.git?identity=other",
    )
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}


def test_project_git_timeout_is_indeterminate(tmp_path, monkeypatch):
    repo = tmp_path / "src" / "demo-repo"
    _register_project(
        tmp_path,
        "demo-repo",
        repo,
        "https://github.com/example/demo-repo.git",
    )

    def timeout(*_args):
        raise subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr(resolver, "_git", timeout)
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}
    assert any(f.reason == "registry-indeterminate" for f in report.findings)


def test_missing_repos_for_adopted_projects_is_indeterminate(tmp_path):
    aw = tmp_path / ".agent-worktrees"
    aw.mkdir()
    (aw / "projects.yaml").write_text(
        yaml.safe_dump({"projects": {"demo": {"config_dir": "~/.demo"}}}),
        encoding="utf-8",
    )
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.INDETERMINATE
    assert any(f.reason == "registry-indeterminate" for f in report.findings)


def test_malformed_projects_registry_is_indeterminate(tmp_path):
    aw = tmp_path / ".agent-worktrees"
    aw.mkdir()
    (aw / "projects.yaml").write_text("projects: [", encoding="utf-8")
    report = resolve_active_plugins(home=tmp_path)
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.reconcile({"demo@local": _prior()})


def test_git_environment_is_sanitized(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'remote.origin.url=bad'")
    monkeypatch.setenv("GIT_DIR", "bad")
    env = resolver._clean_git_env()
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert "GIT_DIR" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


def test_installed_payload_wins_over_convergent_local_source(tmp_path):
    repo = tmp_path / "src" / "demo-repo"
    remote = "https://github.com/example/demo-repo.git"
    _register_project(tmp_path, "demo-repo", repo, remote)
    _plugin(repo, "local", "demo")
    _settings(repo, "local", "demo")
    installed = _installed(tmp_path, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert report.active["demo@local"].root == installed


def test_installed_payload_cannot_mask_distinct_local_roots(tmp_path):
    for name in ("one", "two"):
        repo = tmp_path / "src" / name
        _register_project(
            tmp_path,
            name,
            repo,
            f"https://github.com/example/{name}.git",
        )
        _plugin(repo, "local", "demo")
        _settings(repo, "local", "demo")
    _installed(tmp_path, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert "demo@local" not in report.active
    assert report.decisions["demo@local"].status is EntryStatus.INACTIVE
    assert any(f.reason == "root-ambiguous" for f in report.findings)


def test_installed_payload_requires_exact_manifest_identity(tmp_path):
    repo = tmp_path / "src" / "demo-repo"
    remote = "https://github.com/example/demo-repo.git"
    _register_project(tmp_path, "demo-repo", repo, remote)
    local = _plugin(repo, "local", "demo")
    _settings(repo, "local", "demo")
    _installed(tmp_path, "local", "demo", manifest_name="other")
    report = resolve_active_plugins(home=tmp_path)
    assert report.active["demo@local"].root == local
    assert report.decisions["demo@local"].status is EntryStatus.ACTIVE_WITH_ADVISORY
    assert any(f.reason == "identity-mismatch" for f in report.findings)


def test_installed_payload_does_not_mask_unreadable_local_evidence(
    tmp_path,
    monkeypatch,
):
    copilot = tmp_path / ".copilot"
    _plugin(copilot, "local", "demo")
    _write_json(
        copilot / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"demo@local": True},
        },
    )
    _installed(tmp_path, "local", "demo")
    manifest = copilot / ".ai" / ".claude-plugin" / "marketplace.json"
    original = Path.read_text

    def denied(path: Path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.COMPLETE
    assert report.decisions["demo@local"].status is EntryStatus.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}


@pytest.mark.parametrize(
    "definition",
    [
        {"source": []},
        {"source": {"source": [], "path": "./.ai"}},
        {"source": {"source": "directory", "path": []}},
        {"source": {"source": "", "path": "./.ai"}},
        {"source": {"source": "unsupported", "path": "./.ai"}},
    ],
)
@pytest.mark.parametrize("has_installed", [False, True])
def test_malformed_marketplace_evidence_is_indeterminate(
    tmp_path,
    definition,
    has_installed,
):
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {"local": definition},
            "enabledPlugins": {"demo@local": True},
        },
    )
    if has_installed:
        _installed(tmp_path, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    prior = _prior()
    assert report.authority is ScanAuthority.COMPLETE
    assert report.decisions["demo@local"].status is EntryStatus.INDETERMINATE
    assert report.reconcile({"demo@local": prior}) == {"demo@local": prior}
    assert any(f.reason == "invalid-entry" for f in report.findings)


def test_remote_marketplace_can_use_exact_installed_payload(tmp_path):
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {"enabledPlugins": {"demo@catalog": True}},
    )
    installed = _installed(tmp_path, "catalog", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert report.active["demo@catalog"].root == installed


def test_disabled_everywhere_does_not_resurrect_installed_payload(tmp_path):
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {"enabledPlugins": {"demo@local": False}},
    )
    _installed(tmp_path, "local", "demo")
    report = resolve_active_plugins(home=tmp_path)
    assert report.active == {}
    assert report.findings == ()


@pytest.mark.parametrize(
    ("source", "plugin_root"),
    [("../outside/demo", None), ("demo", "../outside")],
)
def test_local_marketplace_paths_cannot_escape(tmp_path, source, plugin_root):
    copilot = tmp_path / ".copilot"
    market = copilot / ".ai"
    outside = copilot / "outside" / "demo"
    _write_json(outside / "plugin.json", {"name": "demo"})
    manifest: dict[str, object] = {
        "name": "local",
        "plugins": [{"name": "demo", "source": source}],
    }
    if plugin_root:
        manifest["metadata"] = {"pluginRoot": plugin_root}
    _write_json(market / ".claude-plugin" / "marketplace.json", manifest)
    _write_json(
        copilot / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"demo@local": True},
        },
    )
    report = resolve_active_plugins(home=tmp_path)
    assert "demo@local" not in report.active
    assert any(
        f.reason == "identity-mismatch" and "escapes" in (f.detail or "")
        for f in report.findings
    )


def test_local_marketplace_symlink_escape_is_rejected(tmp_path):
    copilot = tmp_path / ".copilot"
    market = copilot / ".ai"
    outside = copilot / "outside"
    _write_json(outside / "plugin.json", {"name": "demo"})
    _write_json(
        market / ".claude-plugin" / "marketplace.json",
        {
            "name": "local",
            "plugins": [{"name": "demo", "source": "./demo"}],
        },
    )
    try:
        (market / "demo").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    _write_json(
        copilot / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"demo@local": True},
        },
    )
    report = resolve_active_plugins(home=tmp_path)
    assert "demo@local" not in report.active
    assert any(f.reason == "identity-mismatch" for f in report.findings)


def test_duplicate_local_plugin_entries_are_ambiguous(tmp_path):
    copilot = tmp_path / ".copilot"
    market = copilot / ".ai"
    _write_json(
        market / ".claude-plugin" / "marketplace.json",
        {
            "name": "local",
            "plugins": [
                {"name": "demo", "source": "first"},
                {"name": "demo", "source": "second"},
            ],
        },
    )
    _write_json(market / "first" / "plugin.json", {"name": "demo"})
    _write_json(market / "second" / "plugin.json", {"name": "demo"})
    _write_json(
        copilot / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"demo@local": True},
        },
    )
    report = resolve_active_plugins(home=tmp_path)
    assert "demo@local" not in report.active
    assert any(f.reason == "root-ambiguous" for f in report.findings)
