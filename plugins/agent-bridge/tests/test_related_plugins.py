"""Tests for related-repo plugin sourcing (agent-bridge side)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge import related_plugins as rp
from agent_bridge.transport import PluginRef


def _write_related(anchor: Path, body: str) -> None:
    d = anchor / ".agent-worktrees"
    d.mkdir(parents=True, exist_ok=True)
    (d / "related.yaml").write_text(body, encoding="utf-8")


_RELATED = """\
primary: odsp-web
related:
  odsp-web:
    role: product
    locus:
      preferred: codespace
      codespace: { repo: odsp-microsoft/odsp-web-codespaces, workspace_folder: /workspaces/odsp-web }
      container: { repo: odsp-microsoft/odsp-web-codespaces, machines: [dev6] }
    delegate: { via: agent-codespaces }
    plugins:
      - { source: odsp-web-codespace@dev-tmichon }
      - { source: extra@dev-tmichon, enable: false }
  no-plugins:
    locus:
      codespace: { repo: org/other-codespaces }
"""


def test_match_by_codespace_repo(tmp_path: Path):
    _write_related(tmp_path, _RELATED)
    refs = rp.related_plugins_for_repo(
        "odsp-microsoft/odsp-web-codespaces", anchors=[tmp_path]
    )
    assert refs == [
        PluginRef("odsp-web-codespace@dev-tmichon", enable=True),
        PluginRef("extra@dev-tmichon", enable=False),
    ]


def test_match_is_case_insensitive(tmp_path: Path):
    _write_related(tmp_path, _RELATED)
    refs = rp.related_plugins_for_repo(
        "ODSP-Microsoft/ODSP-Web-Codespaces", anchors=[tmp_path]
    )
    assert [r.source for r in refs] == [
        "odsp-web-codespace@dev-tmichon", "extra@dev-tmichon",
    ]


def test_entry_without_plugins_returns_empty(tmp_path: Path):
    _write_related(tmp_path, _RELATED)
    assert rp.related_plugins_for_repo("org/other-codespaces", anchors=[tmp_path]) == []


def test_unknown_repo_returns_empty(tmp_path: Path):
    _write_related(tmp_path, _RELATED)
    assert rp.related_plugins_for_repo("org/nope", anchors=[tmp_path]) == []


def test_none_or_missing_file_returns_empty(tmp_path: Path):
    assert rp.related_plugins_for_repo(None, anchors=[tmp_path]) == []
    # No related.yaml written under tmp_path -> empty.
    assert rp.related_plugins_for_repo("any/repo", anchors=[tmp_path]) == []


def test_container_repo_also_matches(tmp_path: Path):
    _write_related(
        tmp_path,
        "related:\n"
        "  x:\n"
        "    locus:\n"
        "      container: { repo: org/x-codespaces }\n"
        "    plugins:\n"
        "      - only@mkt\n",
    )
    refs = rp.related_plugins_for_repo("org/x-codespaces", anchors=[tmp_path])
    assert [r.source for r in refs] == ["only@mkt"]


def test_control_plane_anchors_from_topology(tmp_path, monkeypatch):
    # A topology whose machines_yaml lives at an anchor with related.yaml.
    _write_related(tmp_path, _RELATED)
    (tmp_path / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")

    class _Prof:
        machines_yaml = str(tmp_path / "machines.yaml")

    class _Cfg:
        topologies = {"dotfiles": _Prof()}

    monkeypatch.setattr(rp, "load_config", lambda: _Cfg())
    anchors = rp.control_plane_anchors()
    assert tmp_path in anchors


def test_control_plane_anchors_registry_fallback(tmp_path, monkeypatch):
    # machines_yaml points at a stale/missing worktree; the repos-registry
    # canonical anchor (by topology name) is used instead.
    canonical = tmp_path / "canonical"
    _write_related(canonical, _RELATED)
    stale = tmp_path / "stale" / "machines.yaml"

    class _Prof:
        machines_yaml = str(stale)

    class _Cfg:
        topologies = {"dotfiles": _Prof()}

    monkeypatch.setattr(rp, "load_config", lambda: _Cfg())
    monkeypatch.setattr(
        rp, "_registry_anchor",
        lambda name: canonical if name == "dotfiles" else None,
    )
    anchors = rp.control_plane_anchors()
    assert canonical in anchors
