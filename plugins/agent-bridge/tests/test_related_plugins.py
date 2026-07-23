"""Tests for related-repo plugin sourcing (agent-bridge side)."""

from __future__ import annotations

from pathlib import Path


from agent_bridge import related_plugins as rp
from agent_bridge.transport import PluginRef


def _write_related(anchor: Path, body: str) -> None:
    d = anchor / ".agent-worktrees"
    d.mkdir(parents=True, exist_ok=True)
    (d / "related.yaml").write_text(body, encoding="utf-8")


_RELATED = """\
primary: example-web
related:
  example-web:
    role: product
    locus:
      preferred: codespace
      codespace: { repo: example-org/example-web-codespaces, workspace_folder: /workspaces/example-web }
      container: { repo: example-org/example-web-codespaces, machines: [dev6] }
    delegate: { via: agent-codespaces }
    plugins:
      - { source: example-web-codespace@example-marketplace }
      - { source: extra@example-marketplace, enable: false }
  no-plugins:
    locus:
      codespace: { repo: org/other-codespaces }
"""


def test_match_by_codespace_repo(tmp_path: Path):
    _write_related(tmp_path, _RELATED)
    refs = rp.related_plugins_for_repo(
        "example-org/example-web-codespaces", anchors=[tmp_path]
    )
    assert refs == [
        PluginRef("example-web-codespace@example-marketplace", enable=True),
        PluginRef("extra@example-marketplace", enable=False),
    ]


def test_match_is_case_insensitive(tmp_path: Path):
    _write_related(tmp_path, _RELATED)
    refs = rp.related_plugins_for_repo(
        "Example-Org/Example-Web-Codespaces", anchors=[tmp_path]
    )
    assert [r.source for r in refs] == [
        "example-web-codespace@example-marketplace", "extra@example-marketplace",
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


def test_harness_plugins_are_never_propagated(tmp_path: Path):
    # A mis-declared harness plugin in related.plugins must be filtered out.
    _write_related(
        tmp_path,
        "related:\n"
        "  example-web:\n"
        "    locus:\n"
        "      codespace: { repo: org/example-web-codespaces }\n"
        "    plugins:\n"
        "      - example-web-harness@example-marketplace\n"        # harness -> dropped
        "      - { source: example-web-harness-extra@m }\n"  # harness-* -> dropped
        "      - example-web-agent@example-marketplace\n",          # in-context -> kept
    )
    refs = rp.related_plugins_for_repo("org/example-web-codespaces", anchors=[tmp_path])
    assert [r.source for r in refs] == ["example-web-agent@example-marketplace"]


def test_is_harness_plugin():
    assert rp.is_harness_plugin("example-web-harness@example-marketplace") is True
    assert rp.is_harness_plugin("example-web-harness-status@m") is True
    assert rp.is_harness_plugin("example-web-agent@m") is False
    assert rp.is_harness_plugin("example-web-agent-development@m") is False
    assert rp.is_harness_plugin("agent-bridge@copilot-extensions") is False


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
