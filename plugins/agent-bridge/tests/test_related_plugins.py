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


def test_explicit_missing_override_survives_related_selection(tmp_path):
    _write_related(tmp_path, """related:
  sample:
    plugins:
      - {source: retired@local, enable: false, allow_missing: true}
""")
    refs = rp.related_plugins_for_repo("sample", anchors=[tmp_path])
    assert refs == [PluginRef("retired@local", enable=False, allow_missing=True)]
    assert refs[0].selection() == {
        "source": "retired@local", "enable": False, "allow_missing": True,
    }
    with pytest.raises(ValueError, match="allow_missing"):
        rp._parse_plugin_items([{"source": "retired@local", "allow_missing": "true"}])


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


def test_entry_key_matches_for_local_ssh_targets(tmp_path: Path):
    # A static-registry local/SSH target has no codespace/container repo
    # coordinate of its own -- only the related.yaml entry's own registry
    # key, which is what a local-loopback/SSH dispatch's ``project`` is.
    _write_related(
        tmp_path,
        "related:\n"
        "  copilot-extensions:\n"
        "    locus:\n"
        "      preferred: local\n"
        "      machines: [dev6]\n"
        "    plugins:\n"
        "      - only@mkt\n",
    )
    refs = rp.related_plugins_for_repo("copilot-extensions", anchors=[tmp_path])
    assert [r.source for r in refs] == ["only@mkt"]


def test_entry_key_match_is_case_insensitive(tmp_path: Path):
    _write_related(
        tmp_path,
        "related:\n"
        "  Copilot-Extensions:\n"
        "    locus: { preferred: local }\n"
        "    plugins:\n"
        "      - only@mkt\n",
    )
    refs = rp.related_plugins_for_repo("copilot-extensions", anchors=[tmp_path])
    assert [r.source for r in refs] == ["only@mkt"]


def test_non_string_yaml_key_does_not_crash_other_entries(tmp_path: Path):
    # YAML deserializes an unquoted `42:`/`true:` key as int/bool, not str --
    # a bare .lower() on that key must not raise and drop every later entry.
    _write_related(
        tmp_path,
        "related:\n"
        "  42:\n"
        "    locus: { preferred: local }\n"
        "  copilot-extensions:\n"
        "    locus: { preferred: local }\n"
        "    plugins:\n"
        "      - only@mkt\n",
    )
    refs = rp.related_plugins_for_repo("copilot-extensions", anchors=[tmp_path])
    assert [r.source for r in refs] == ["only@mkt"]


def test_non_string_yaml_key_never_matches_its_string_form(tmp_path: Path):
    # Regression: a non-string key must be SKIPPED, not coerced via str() --
    # a repo literally named "42" must not match a YAML int key 42.
    _write_related(
        tmp_path,
        "related:\n"
        "  42:\n"
        "    locus: { preferred: local }\n"
        "    plugins:\n"
        "      - should-not-match@mkt\n",
    )
    assert rp.related_plugins_for_repo("42", anchors=[tmp_path]) == []


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


# --- Layered lane (agent-worktrees grafted ``related list --json``) ---------

_real_read_layered_related = rp._read_layered_related

_LAYERED_ROWS = [
    {"name": "other", "locus": {"container": {"repo": "elsewhere"}},
     "plugins": [{"source": "other@synthetic", "enable": True}]},
    {"name": "product", "locus": {"container": {"repo": "example-org/product-venue"}},
     "provenance": {"layer": "plugin"},
     "plugins": [
         {"source": "dev-locally@synthetic", "enable": True},
         {"source": "product-harness@synthetic", "enable": True},
     ]},
]


def test_layered_view_supplies_plugins_when_checkout_declares_nothing(tmp_path, monkeypatch):
    """Harness case: ``related: {}`` in the checkout, plugins only in a grafted layer."""
    _write_related(tmp_path, "related: {}\n")
    seen = []
    monkeypatch.setattr(rp, "_read_layered_related", lambda a: seen.append(a) or _LAYERED_ROWS)
    for repo in ("product", "EXAMPLE-ORG/product-venue"):
        assert rp.declared_related_plugins(repo, [tmp_path]) == [PluginRef("dev-locally@synthetic")]
    assert seen == [tmp_path, tmp_path]


def test_layered_view_outranks_legacy_file(tmp_path, monkeypatch):
    _write_related(tmp_path, "related:\n  product:\n    plugins:\n      - { source: stale@synthetic }\n")
    monkeypatch.setattr(rp, "_read_layered_related", lambda a: _LAYERED_ROWS)
    assert rp.declared_related_plugins("product", [tmp_path]) == [PluginRef("dev-locally@synthetic")]


@pytest.mark.parametrize("rows", [None, [], [{"name": "product", "locus": {}}]])
def test_legacy_file_used_when_layered_view_unavailable_or_older(tmp_path, monkeypatch, rows):
    """No owner, empty view, or an owner that predates the ``plugins`` field."""
    _write_related(tmp_path, "related:\n  product:\n    plugins:\n      - { source: legacy@synthetic }\n")
    monkeypatch.setattr(rp, "_read_layered_related", lambda a: rows)
    assert rp.declared_related_plugins("product", [tmp_path]) == [PluginRef("legacy@synthetic")]


def test_layered_view_checks_anchors_in_order(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    views = {first: [{"name": "product", "plugins": []}], second: _LAYERED_ROWS}
    monkeypatch.setattr(rp, "_read_layered_related", lambda a: views[a])
    assert rp.declared_related_plugins("product", [first, second]) == [PluginRef("dev-locally@synthetic")]


def _fake_owner(tmp_path: Path, body: str) -> list[str]:
    import sys

    script = tmp_path / "fake_owner.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


def test_layered_reader_invokes_owner_related_list(tmp_path, monkeypatch):
    prefix = _fake_owner(tmp_path, (
        "import json, sys\n"
        "print(json.dumps({'related': [{'name': 'argv', 'plugins': sys.argv[1:]}, 'junk']}))\n"
    ))
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix", lambda: prefix)
    rows = _real_read_layered_related(tmp_path)
    assert rows == [{"name": "argv", "plugins": ["related", "list", "--json", "--repo", str(tmp_path)]}]


@pytest.mark.parametrize("body", [
    "import sys\nsys.exit(3)\n",
    "print('not json')\n",
    "print('{\"related\": {}}')\n",
])
def test_layered_reader_fails_safe(tmp_path, monkeypatch, body):
    prefix = _fake_owner(tmp_path, body)
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix", lambda: prefix)
    assert _real_read_layered_related(tmp_path) is None


def test_layered_reader_without_owner_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix", lambda: None)
    assert _real_read_layered_related(tmp_path) is None
