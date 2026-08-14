"""Tests for the user-level drop-in config providers (~/.agent-codespaces/config.d/).

A plugin (e.g. odsp-web-harness) makes its shipped CodeSpace target config
discoverable with NO control-plane repo by dropping a *pointer* into config.d/.
agent-codespaces resolves the pointer, loads the config, and merges it at the
LOWEST precedence (an adopted-repo / cwd config still wins).
"""

from __future__ import annotations

from pathlib import Path

import agent_codespaces.config as cfg
from agent_codespaces.config import AdoptedRepo

_ODSP_CONFIG = """\
credentials:
  ado_host: onedrive.visualstudio.com
repos:
  odsp-microsoft/odsp-web-codespaces:
    machine_type: largePremiumLinux256gb
    workspace_repo: odsp-web
    devcontainer_path: .devcontainer/devcontainer.json
"""


def _plugin_config(tmp_path: Path, body: str = _ODSP_CONFIG) -> Path:
    p = tmp_path / "plugin" / "references" / "agent-codespaces" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, "utf-8")
    return p


def _config_d(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "config.d"
    d.mkdir()
    monkeypatch.setattr(cfg, "config_d_dir", lambda: d)
    return d


def test_discover_resolves_pointer(tmp_path, monkeypatch):
    plugin_cfg = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    (d / "odsp-web-harness.conf").write_text(
        f"# managed by odsp-web-harness\n{plugin_cfg}\n", "utf-8"
    )
    assert cfg.discover_dropin_configs() == [plugin_cfg]


def test_discover_skips_missing_target(tmp_path, monkeypatch):
    d = _config_d(tmp_path, monkeypatch)
    (d / "stale.conf").write_text(str(tmp_path / "gone" / "config.yaml") + "\n", "utf-8")
    assert cfg.discover_dropin_configs() == []


def test_discover_direct_yaml_entry(tmp_path, monkeypatch):
    d = _config_d(tmp_path, monkeypatch)
    frag = d / "inline.yaml"
    frag.write_text(_ODSP_CONFIG, "utf-8")
    assert cfg.discover_dropin_configs() == [frag]


def test_merged_config_from_dropin_without_any_repo(tmp_path, monkeypatch):
    plugin_cfg = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    (d / "odsp-web-harness.conf").write_text(str(plugin_cfg) + "\n", "utf-8")
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])

    merged = cfg.load_merged_config(include_cwd=False)
    # Discoverable with NO control-plane repo -- the golden-path seam.
    assert "odsp-microsoft/odsp-web-codespaces" in merged.repos
    assert merged.repos["odsp-microsoft/odsp-web-codespaces"].machine_type == (
        "largePremiumLinux256gb"
    )
    assert merged.credentials.ado_host == "onedrive.visualstudio.com"


def test_adopted_repo_overrides_dropin(tmp_path, monkeypatch):
    # A drop-in provides the odsp-web repo at one machine_type…
    plugin_cfg = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    (d / "odsp-web-harness.conf").write_text(str(plugin_cfg) + "\n", "utf-8")
    # …and an ADOPTED repo declares the SAME repo key with a different value.
    repo = tmp_path / "repo"
    repo_cfg = repo / ".agent-codespaces" / "config.yaml"
    repo_cfg.parent.mkdir(parents=True)
    repo_cfg.write_text(
        "repos:\n"
        "  odsp-microsoft/odsp-web-codespaces:\n"
        "    machine_type: OVERRIDDEN\n",
        "utf-8",
    )
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [AdoptedRepo(path=repo)])

    merged = cfg.load_merged_config(include_cwd=False)
    # Adopted repo is merged first -> "first wins", so it beats the drop-in.
    assert merged.repos["odsp-microsoft/odsp-web-codespaces"].machine_type == "OVERRIDDEN"


def test_no_dropin_no_repo_is_empty(tmp_path, monkeypatch):
    _config_d(tmp_path, monkeypatch)  # empty config.d
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    merged = cfg.load_merged_config(include_cwd=False)
    assert merged.repos == {}
    assert merged.source_paths == []
