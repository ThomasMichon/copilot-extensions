"""Tests for the per-project "related repos" layer (related.yaml)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from agent_worktrees import related
from agent_worktrees.related import Locus, RelatedConfig, RelatedEntry

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def test_path_helpers(tmp_path: Path):
    assert related.related_dir(tmp_path) == tmp_path / ".agent-worktrees"
    assert related.related_path(tmp_path) == tmp_path / ".agent-worktrees" / "related.yaml"
    assert related.docs_dir(tmp_path) == tmp_path / ".agent-worktrees" / "related"
    assert related.default_doc_rel("example-web") == "related/example-web.md"


def test_doc_abs_path_default_and_explicit(tmp_path: Path):
    # default for a bare name
    assert related.doc_abs_path(tmp_path, "foo") == (
        tmp_path / ".agent-worktrees" / "related" / "foo.md"
    )
    # explicit doc on the entry wins
    e = RelatedEntry(name="foo", doc="related/custom.md")
    assert related.doc_abs_path(tmp_path, e) == (
        tmp_path / ".agent-worktrees" / "related" / "custom.md"
    )
    # entry without doc falls back to the default
    assert related.doc_abs_path(tmp_path, RelatedEntry(name="bar")) == (
        tmp_path / ".agent-worktrees" / "related" / "bar.md"
    )


def test_doc_abs_path_honors_origin_anchor(tmp_path: Path):
    # A grafted overlay entry carries the anchor it was read from; its doc must
    # resolve against THAT anchor, not the base passed to doc_abs_path.
    base = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    e = RelatedEntry(name="example-web", doc="related/example-web.md",
                     origin_anchor=str(knowledge))
    assert related.doc_abs_path(base, e) == (
        knowledge / ".agent-worktrees" / "related" / "example-web.md"
    )


# ---------------------------------------------------------------------------
# State-root config-graft (E1e): read_related_grafted + grafted accessors
# ---------------------------------------------------------------------------

def _write_related(anchor: Path, cfg: RelatedConfig) -> None:
    related.write_related(anchor, cfg)


def test_grafted_union_overlays_knowledge_on_harness(tmp_path: Path):
    base = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    _write_related(base, RelatedConfig(
        primary="base-primary",
        related={"shared": RelatedEntry(name="shared", role="tooling",
                                        summary="from harness"),
                 "harness-only": RelatedEntry(name="harness-only",
                                              role="docs")},
    ))
    _write_related(knowledge, RelatedConfig(
        primary="example-web",
        related={"shared": RelatedEntry(name="shared", role="product",
                                        summary="from knowledge"),
                 "example-web": RelatedEntry(name="example-web", role="product")},
    ))

    merged = related.read_related_grafted([base, knowledge])
    # later (knowledge) primary wins
    assert merged.primary == "example-web"
    # union of both anchors' entries
    assert set(merged.related) == {"shared", "harness-only", "example-web"}
    # knowledge overlays the harness entry wholesale on a name collision
    assert merged.related["shared"].role == "product"
    assert merged.related["shared"].summary == "from knowledge"
    # origin_anchor tracks the source per entry
    assert merged.related["shared"].origin_anchor == str(knowledge)
    assert merged.related["harness-only"].origin_anchor == str(base)
    assert merged.related["example-web"].origin_anchor == str(knowledge)


def test_grafted_single_anchor_matches_ungrafted(tmp_path: Path):
    base = tmp_path / "harness"
    _write_related(base, RelatedConfig(
        primary="p",
        related={"a": RelatedEntry(name="a", role="tooling")},
    ))
    assert related.get_primary_grafted([base]) == related.get_primary(base)
    assert [e.name for e in related.list_related_grafted([base])] == \
           [e.name for e in related.list_related(base)]
    assert related.get_related_grafted([base], "a").role == "tooling"


def test_grafted_primary_falls_through_when_overlay_unset(tmp_path: Path):
    base = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    _write_related(base, RelatedConfig(primary="base-primary"))
    # knowledge has entries but no primary of its own
    _write_related(knowledge, RelatedConfig(
        related={"example-web": RelatedEntry(name="example-web", role="product")},
    ))
    merged = related.read_related_grafted([base, knowledge])
    assert merged.primary == "base-primary"  # base primary retained


def test_grafted_list_role_filter(tmp_path: Path):
    base = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    _write_related(base, RelatedConfig(
        related={"tool": RelatedEntry(name="tool", role="tooling")}))
    _write_related(knowledge, RelatedConfig(
        related={"web": RelatedEntry(name="web", role="product"),
                 "core": RelatedEntry(name="core", role="product")}))
    products = related.list_related_grafted([base, knowledge], role="product")
    assert [e.name for e in products] == ["core", "web"]


# ---------------------------------------------------------------------------
# Installed-plugin config-graft: plugins contribute related entries by install
# ---------------------------------------------------------------------------

def _make_installed_plugin(root: Path, marketplace: str, name: str,
                           cfg: RelatedConfig, *, manifest: str = "plugin.json"):
    """Create a fake installed plugin under root/<marketplace>/<name>/."""
    plugin_dir = root / marketplace / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if manifest == "plugin.json":
        (plugin_dir / "plugin.json").write_text("{}", encoding="utf-8")
    elif manifest == ".claude-plugin":
        (plugin_dir / ".claude-plugin").mkdir(exist_ok=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            "{}", encoding="utf-8")
    # else: no manifest (negative case)
    _write_related(plugin_dir, cfg)
    return plugin_dir


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _make_local_marketplace_plugin(
    settings_root: Path,
    marketplace: str,
    name: str,
    cfg: RelatedConfig,
) -> Path:
    market = settings_root / ".ai"
    manifest_path = market / ".claude-plugin" / "marketplace.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {"name": marketplace, "plugins": []}
    )
    manifest["plugins"].append({"name": name, "source": f"./{name}"})
    _write_json(
        manifest_path,
        manifest,
    )
    plugin = market / name
    _write_json(plugin / ".claude-plugin" / "plugin.json", {"name": name})
    _write_related(plugin, cfg)
    return plugin.resolve()


def _register_project(home: Path, name: str, repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    remote = f"https://example.com/{name}.git"
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", remote],
        check=True,
    )
    platform_key = "windows" if os.name == "nt" else "linux"
    _write_json(
        home / ".agent-worktrees" / "projects.yaml",
        {"projects": {name: {"config_dir": f"~/.{name}"}}},
    )
    _write_json(
        home / ".agent-worktrees" / "repos.yaml",
        {
            "repos": {
                name: {
                    platform_key: str(repo),
                    "remote": remote,
                    "class": "worktree",
                }
            }
        },
    )


def test_installed_plugin_related_anchors_discovers_and_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "installed-plugins"
    # (a) nested layout with plugin.json manifest
    p1 = _make_installed_plugin(
        root, "example-marketplace", "example-web-harness",
        RelatedConfig(related={"example-web": RelatedEntry(
            name="example-web", role="product", delegate="agent-codespaces")}))
    # (b) nested layout with .claude-plugin manifest
    p2 = _make_installed_plugin(
        root, "acme", "other-harness",
        RelatedConfig(related={"other": RelatedEntry(name="other", role="tooling")}),
        manifest=".claude-plugin")
    # (c) ships related.yaml but NO manifest -> must be ignored
    _make_installed_plugin(
        root, "acme", "not-a-plugin",
        RelatedConfig(related={"nope": RelatedEntry(name="nope")}),
        manifest="none")

    monkeypatch.setenv(related.INSTALLED_PLUGINS_ENV, str(root))
    anchors = related.installed_plugin_related_anchors()
    assert set(anchors) == {str(p1), str(p2)}  # (c) excluded, deterministic set
    assert anchors == sorted(anchors)          # deterministic order


def test_installed_plugin_related_anchors_empty_when_root_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(related.INSTALLED_PLUGINS_ENV, str(tmp_path / "nope"))
    assert related.installed_plugin_related_anchors() == []


def test_filesystem_plugin_scan_isolates_resolve_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "installed-plugins"
    broken = _make_installed_plugin(
        root,
        "marketplace",
        "broken-harness",
        RelatedConfig(
            related={"broken": RelatedEntry(name="broken", role="tooling")}
        ),
    )
    healthy = _make_installed_plugin(
        root,
        "marketplace",
        "healthy-harness",
        RelatedConfig(
            related={"healthy": RelatedEntry(name="healthy", role="tooling")}
        ),
    )
    original = Path.resolve

    def resolve(path: Path, *args, **kwargs):
        if path == broken:
            raise OSError("unreadable candidate")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert related.installed_plugin_related_anchors(root=root) == [
        str(healthy.resolve())
    ]


def test_plugin_related_anchors_use_effective_active_local_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    enabled = _make_local_marketplace_plugin(
        tmp_path / ".copilot",
        "local",
        "enabled-harness",
        RelatedConfig(
            related={"enabled": RelatedEntry(name="enabled", role="tooling")}
        ),
    )
    disabled = _make_local_marketplace_plugin(
        tmp_path / ".copilot",
        "local",
        "disabled-harness",
        RelatedConfig(
            related={"disabled": RelatedEntry(name="disabled", role="tooling")}
        ),
    )
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {
                "enabled-harness@local": True,
                "disabled-harness@local": False,
            },
        },
    )

    assert related.installed_plugin_related_anchors(home=tmp_path) == [
        str(enabled)
    ]
    assert str(disabled) not in related.installed_plugin_related_anchors(
        home=tmp_path
    )


def test_active_local_plugin_primary_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    plugin = _make_local_marketplace_plugin(
        tmp_path / ".copilot",
        "local",
        "example-harness",
        RelatedConfig(
            primary="plugin-must-not-win",
            related={"example": RelatedEntry(name="example", role="tooling")},
        ),
    )
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"example-harness@local": True},
        },
    )
    base = tmp_path / "base"
    _write_related(base, RelatedConfig(primary="base-primary"))

    active = related.installed_plugin_related_anchors(home=tmp_path)
    assert active == [str(plugin)]
    merged = related.read_related_grafted([*active, base])

    assert merged.primary == "base-primary"
    assert "example" in merged.related


def test_plugin_related_anchors_include_adopted_project_local_marketplace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    repo = tmp_path / "control-harness"
    _register_project(tmp_path, "control-harness", repo)
    plugin = _make_local_marketplace_plugin(
        repo,
        "control-marketplace",
        "example-harness",
        RelatedConfig(
            related={"example": RelatedEntry(name="example", role="tooling")}
        ),
    )
    _write_json(
        repo / ".github" / "copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "control-marketplace": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"example-harness@control-marketplace": True},
        },
    )

    assert related.installed_plugin_related_anchors(home=tmp_path) == [
        str(plugin)
    ]


def test_plugin_related_anchors_include_mixed_live_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    installed = _make_installed_plugin(
        tmp_path / ".copilot" / "installed-plugins",
        "local",
        "example-harness",
        RelatedConfig(
            related={"installed": RelatedEntry(name="installed", role="tooling")}
        ),
    ).resolve()
    _write_json(installed / "plugin.json", {"name": "example-harness"})
    repo = tmp_path / "control-harness"
    _register_project(tmp_path, "control-harness", repo)
    local = _make_local_marketplace_plugin(
        repo,
        "local",
        "example-harness",
        RelatedConfig(
            related={"local": RelatedEntry(name="local", role="tooling")}
        ),
    )
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {"enabledPlugins": {"example-harness@local": True}},
    )
    _write_json(
        repo / ".github" / "copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"example-harness@local": True},
        },
    )

    assert related.installed_plugin_related_anchors(home=tmp_path) == sorted(
        [str(installed), str(local)],
        key=os.path.normcase,
    )


def test_plugin_related_anchors_fail_closed_on_indeterminate_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    _make_local_marketplace_plugin(
        tmp_path / ".copilot",
        "local",
        "example-harness",
        RelatedConfig(
            related={"example": RelatedEntry(name="example", role="tooling")}
        ),
    )
    _write_json(
        tmp_path / ".copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "local": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"example-harness@local": True},
        },
    )
    (tmp_path / ".copilot" / "settings.local.json").write_text(
        "{not valid json",
        encoding="utf-8",
    )
    assert related.installed_plugin_related_anchors(home=tmp_path) == []
    assert related.installed_plugin_related_anchors(home=tmp_path) == []


def test_grafted_plugin_is_lowest_precedence_and_primary_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "installed-plugins"
    # plugin contributes example-web AND (wrongly) a primary -- primary must be ignored
    plugin = _make_installed_plugin(
        root, "example-marketplace", "example-web-harness",
        RelatedConfig(primary="example-web", related={
            "example-web": RelatedEntry(name="example-web", role="product",
                                     summary="from plugin",
                                     delegate="agent-codespaces"),
            "vessel": RelatedEntry(name="vessel", role="tooling")}))
    monkeypatch.setenv(related.INSTALLED_PLUGINS_ENV, str(root))

    base = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    _write_related(base, RelatedConfig(primary="base-primary"))
    # user/knowledge overrides the plugin's example-web entry wholesale
    _write_related(knowledge, RelatedConfig(related={
        "example-web": RelatedEntry(name="example-web", role="product",
                                 summary="from knowledge")}))

    # anchor order as _related_config_source_anchors builds it: plugin, base, knowledge
    merged = related.read_related_grafted([str(plugin), base, knowledge])

    # union: plugin-only entry survives; collision resolves to knowledge
    assert set(merged.related) == {"example-web", "vessel"}
    assert merged.related["example-web"].summary == "from knowledge"   # user overrides plugin
    assert merged.related["example-web"].origin_anchor == str(knowledge)
    # plugin-only entry is contributed purely by being installed
    assert merged.related["vessel"].origin_anchor == str(plugin)
    # a plugin's primary is NEVER adopted; the base primary stands
    assert merged.primary == "base-primary"


def test_grafted_plugin_only_entry_resolves_when_no_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "installed-plugins"
    plugin = _make_installed_plugin(
        root, "example-marketplace", "example-web-harness",
        RelatedConfig(related={"example-web": RelatedEntry(
            name="example-web", role="product", delegate="agent-codespaces")}))
    monkeypatch.setenv(related.INSTALLED_PLUGINS_ENV, str(root))
    base = tmp_path / "harness"
    _write_related(base, RelatedConfig(primary="base-primary"))
    # merely installing the plugin makes example-web resolvable
    e = related.get_related_grafted([str(plugin), base], "example-web")
    assert e is not None and e.delegate == "agent-codespaces"


# ---------------------------------------------------------------------------
# Parsers / normalizers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("local", ("local", "")),
    ("codespace", ("codespace", "")),
    ("machine:dev6", ("machine", "dev6")),
    ("  machine: cloud1 ", ("machine", "cloud1")),
    ("MACHINE:Dev6", ("machine", "dev6")),
    ("", ("", "")),
    (None, ("", "")),
])
def test_parse_preferred(raw, expected):
    assert related.parse_preferred(raw) == expected


def test_normalizers():
    assert related.normalize_role("  Product ") == "product"
    assert related.normalize_delegate(" Agent-Bridge ") == "agent-bridge"


# ---------------------------------------------------------------------------
# read: missing / malformed degrade safely
# ---------------------------------------------------------------------------

def test_read_missing_returns_empty(tmp_path: Path):
    cfg = related.read_related(tmp_path)
    assert cfg == RelatedConfig()
    assert cfg.primary == ""
    assert cfg.related == {}


def test_read_malformed_returns_empty(tmp_path: Path):
    p = related.related_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("primary: [unclosed\n", encoding="utf-8")
    assert related.read_related(tmp_path) == RelatedConfig()


def test_read_non_mapping_returns_empty(tmp_path: Path):
    p = related.related_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert related.read_related(tmp_path) == RelatedConfig()


# ---------------------------------------------------------------------------
# round-trip
# ---------------------------------------------------------------------------

def test_write_then_read_roundtrip(tmp_path: Path):
    cfg = RelatedConfig(
        primary="example-web",
        related={
            "example-web": RelatedEntry(
                name="example-web",
                role="product",
                summary="Primary product monorepo.",
                doc="related/example-web.md",
                locus=Locus(
                    preferred="codespace",
                    codespace={"repo": "org/example-web-codespaces",
                               "machine": "largePremiumLinux256gb",
                               "location": "EastUs"},
                ),
                delegate="agent-codespaces",
            ),
            "copilot-extensions": RelatedEntry(
                name="copilot-extensions",
                role="tooling",
                summary="Source of the plugins.",
                locus=Locus(preferred="machine:dev6", machines=["dev6", "cloud1"]),
                delegate="agent-bridge",
            ),
        },
    )
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path)

    assert got.primary == "example-web"
    assert set(got.related) == {"example-web", "copilot-extensions"}

    ow = got.related["example-web"]
    assert ow.role == "product"
    assert ow.summary == "Primary product monorepo."
    assert ow.doc == "related/example-web.md"
    assert ow.locus.preferred == "codespace"
    assert ow.locus.codespace["repo"] == "org/example-web-codespaces"
    assert ow.locus.codespace["location"] == "EastUs"
    assert ow.delegate == "agent-codespaces"

    ce = got.related["copilot-extensions"]
    assert ce.locus.preferred == "machine:dev6"
    assert ce.locus.machines == ["dev6", "cloud1"]
    assert ce.locus.codespace == {}
    assert ce.delegate == "agent-bridge"


def test_written_file_is_valid_yaml_and_minimal(tmp_path: Path):
    cfg = RelatedConfig(
        primary="a",
        related={"a": RelatedEntry(name="a", role="tooling")},
    )
    related.write_related(tmp_path, cfg)
    text = related.related_path(tmp_path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)  # must parse
    assert data["primary"] == "a"
    assert data["related"]["a"]["role"] == "tooling"
    # empty fields are omitted (minimal files)
    assert "summary" not in data["related"]["a"]
    assert "locus" not in data["related"]["a"]
    assert "delegate" not in data["related"]["a"]


# ---------------------------------------------------------------------------
# delegate: nested vs bare-string read leniency
# ---------------------------------------------------------------------------

def test_delegate_read_nested_and_bare(tmp_path: Path):
    p = related.related_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(
        "related:\n"
        "  a:\n"
        "    delegate: { via: agent-bridge }\n"
        "  b:\n"
        "    delegate: agent-codespaces\n",
        encoding="utf-8",
    )
    cfg = related.read_related(tmp_path)
    assert cfg.related["a"].delegate == "agent-bridge"
    assert cfg.related["b"].delegate == "agent-codespaces"


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def test_primary_get_set(tmp_path: Path):
    assert related.get_primary(tmp_path) == ""
    related.set_primary(tmp_path, "example-web")
    assert related.get_primary(tmp_path) == "example-web"


def test_upsert_insert_then_merge(tmp_path: Path):
    related.upsert_related(tmp_path, RelatedEntry(name="x", role="tooling",
                                                  summary="first"))
    assert related.get_related(tmp_path, "x").summary == "first"

    # merge: only set fields overwrite; unset ones are preserved
    related.upsert_related(tmp_path, RelatedEntry(name="x", delegate="agent-bridge"))
    e = related.get_related(tmp_path, "x")
    assert e.role == "tooling"          # preserved
    assert e.summary == "first"         # preserved
    assert e.delegate == "agent-bridge"  # added


def test_upsert_merges_locus_fields_not_atomic(tmp_path: Path):
    # Seed a full locus (preferred + machines).
    related.upsert_related(tmp_path, RelatedEntry(
        name="ce", role="tooling",
        locus=Locus(preferred="machine:dev6", machines=["dev6"])))
    # Partial update: only --machines -> must preserve preferred (#128).
    related.upsert_related(tmp_path, RelatedEntry(
        name="ce", locus=Locus(machines=["dev6", "cloud1"])))
    e = related.get_related(tmp_path, "ce")
    assert e.locus.preferred == "machine:dev6"       # preserved
    assert e.locus.machines == ["dev6", "cloud1"]    # updated
    assert e.role == "tooling"                        # preserved

    # Partial update: only --locus preferred -> must preserve machines.
    related.upsert_related(tmp_path, RelatedEntry(
        name="ce", locus=Locus(preferred="local")))
    e = related.get_related(tmp_path, "ce")
    assert e.locus.preferred == "local"               # updated
    assert e.locus.machines == ["dev6", "cloud1"]    # preserved


def test_list_related_filter_by_role(tmp_path: Path):
    related.upsert_related(tmp_path, RelatedEntry(name="b", role="tooling"))
    related.upsert_related(tmp_path, RelatedEntry(name="a", role="product"))
    related.upsert_related(tmp_path, RelatedEntry(name="c", role="tooling"))

    names = [e.name for e in related.list_related(tmp_path)]
    assert names == ["a", "b", "c"]     # name-sorted

    tooling = [e.name for e in related.list_related(tmp_path, role="tooling")]
    assert tooling == ["b", "c"]


def test_remove_clears_primary_when_pointed_here(tmp_path: Path):
    related.upsert_related(tmp_path, RelatedEntry(name="x"))
    related.set_primary(tmp_path, "x")
    assert related.remove_related(tmp_path, "x") is True
    assert related.get_related(tmp_path, "x") is None
    assert related.get_primary(tmp_path) == ""        # cleared
    # removing a non-existent entry returns False
    assert related.remove_related(tmp_path, "nope") is False


def test_remove_keeps_unrelated_primary(tmp_path: Path):
    related.upsert_related(tmp_path, RelatedEntry(name="x"))
    related.upsert_related(tmp_path, RelatedEntry(name="y"))
    related.set_primary(tmp_path, "y")
    related.remove_related(tmp_path, "x")
    assert related.get_primary(tmp_path) == "y"       # untouched


# ---------------------------------------------------------------------------
# doc scaffolding
# ---------------------------------------------------------------------------

def test_scaffold_doc_creates_then_preserves(tmp_path: Path):
    e = RelatedEntry(name="example-web", role="product", summary="The product.")
    path, created = related.scaffold_doc(tmp_path, e)
    assert created is True
    assert path == related.doc_abs_path(tmp_path, e)
    text = path.read_text(encoding="utf-8")
    assert "# example-web — related repo" in text
    assert "product" in text
    assert "repos find example-web" in text          # the no-hardcoded-path rule

    # second call leaves the file untouched
    path2, created2 = related.scaffold_doc(tmp_path, e)
    assert created2 is False
    assert path2 == path


# ---------------------------------------------------------------------------
# CLI dispatch (thin layer over the operations above)
# ---------------------------------------------------------------------------

def test_cli_add_list_show_remove(tmp_path: Path, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run

    rc = run(["add", "foo", "--repo", str(tmp_path), "--role", "tooling",
              "--locus", "machine:dev6", "--no-scaffold"])
    assert rc == 0
    e = related.get_related(tmp_path, "foo")
    assert e is not None and e.role == "tooling"
    assert e.locus.preferred == "machine:dev6"

    capfd.readouterr()
    assert run(["list", "--repo", str(tmp_path), "--json"]) == 0
    assert "foo" in capfd.readouterr().out

    assert run(["show", "foo", "--repo", str(tmp_path)]) == 0
    assert run(["remove", "foo", "--repo", str(tmp_path)]) == 0
    assert related.get_related(tmp_path, "foo") is None


def test_cli_primary_set_and_get(tmp_path: Path, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run

    run(["add", "p", "--repo", str(tmp_path), "--no-scaffold"])
    assert run(["primary", "p", "--repo", str(tmp_path)]) == 0
    capfd.readouterr()
    run(["primary", "--repo", str(tmp_path)])
    assert "p" in capfd.readouterr().out


def test_cli_errors(tmp_path: Path):
    from agent_worktrees.__main__ import cmd_related_dispatch as run

    assert run(["bogus", "--repo", str(tmp_path)]) == 1          # unknown subcommand
    assert run(["show", "nope", "--repo", str(tmp_path)]) == 1   # not a related repo
    assert run(["remove", "nope", "--repo", str(tmp_path)]) == 1
    assert run(["primary", "nope", "--repo", str(tmp_path)]) == 1  # link first


# ---------------------------------------------------------------------------
# locus resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,current,expected", [
    ("dev6", "host-dev6", True),
    ("dev6", "dev6", True),
    ("dev6", "DEV6", True),
    ("cloud1", "host-dev6", False),
    ("dev6", "host-dev6-wsl", False),   # last segment is 'wsl'
    ("", "host-dev6", False),
])
def test_machine_matches(key, current, expected):
    assert related.machine_matches(key, current) is expected


def test_resolve_local_worktree_adopted(tmp_path: Path):
    e = RelatedEntry(name="ce", locus=Locus(preferred="local"))
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path="D:/Src/ce", adopted=True,
    )
    assert r.locus_kind == "local"
    assert r.available_here is True
    assert r.editing_model == "worktree"
    # Agents must be pointed at the programmatic create, never interactive --new.
    assert any("ce create --json" in s for s in r.steps)
    assert any("Never `ce --new`" in s for s in r.steps)


def test_resolve_worktree_base_repo_edits_anchor(tmp_path: Path):
    # A worktree-class repo adopted as a base_repo (enlistment) is edited in
    # place in the anchor, never via `--new` worktree isolation (#143).
    e = RelatedEntry(name="SPO.Core", locus=Locus(preferred="local"))
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path="D:/Enlist/SPO", adopted=True, base_repo=True,
    )
    assert r.editing_model == "anchor"
    assert not any("SPO.Core --new" in s for s in r.steps)
    assert not any("Create an isolated worktree" in s for s in r.steps)
    assert any("anchor checkout directly" in s for s in r.steps)
    assert any("base_repo enlistment" in s for s in r.steps)


def test_resolve_worktree_unadopted_suggests_register(tmp_path: Path):
    e = RelatedEntry(name="aih")
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path=None, adopted=False,
    )
    assert r.editing_model == "worktree-unadopted"
    assert any("register aih" in s for s in r.steps)


def test_resolve_reference_is_read_only(tmp_path: Path):
    e = RelatedEntry(name="wiki")
    r = related.build_resolution(
        e, current_machine="m", repo_class="reference",
        repo_path="/x", adopted=False,
    )
    assert r.editing_model == "read-only"
    assert any("Read-only" in s for s in r.steps)


def test_resolve_machine_elsewhere_delegates(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(preferred="machine:cloud1"),
                     delegate="agent-bridge")
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path=None, adopted=True,
    )
    assert r.locus_kind == "machine"
    assert r.target_machine == "cloud1"
    assert r.available_here is False
    assert any("agent-bridge send cloud1" in s for s in r.steps)


def test_resolve_machine_here_is_local(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(preferred="machine:dev6"))
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="singleton",
        repo_path="D:/Git/x", adopted=False,
    )
    assert r.available_here is True
    assert r.editing_model == "anchor"
    assert any("anchor checkout directly" in s for s in r.steps)


def test_resolve_codespace(tmp_path: Path):
    e = RelatedEntry(
        name="example-web", delegate="agent-codespaces",
        locus=Locus(preferred="codespace",
                    codespace={"repo": "org/example-web-codespaces",
                               "machine": "largePremiumLinux256gb",
                               "location": "EastUs"}),
    )
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "codespace"
    assert r.available_here is True
    assert any("agent-codespaces create org/example-web-codespaces" in s for s in r.steps)
    assert any("agent-bridge send codespace:" in s for s in r.steps)
    # Non-local locus carries a read/explore nudge distinct from the change Plan.
    assert r.explore
    assert any("EXPLORE/READ" in e for e in r.explore)
    assert any("agent-codespaces ssh example-web" in e for e in r.explore)


def test_resolve_codespace_explore_uses_workspace_folder(tmp_path: Path):
    e = RelatedEntry(
        name="example-web", delegate="agent-codespaces",
        locus=Locus(preferred="codespace",
                    codespace={"repo": "org/example-web-codespaces",
                               "workspace_folder": "/workspaces/example-web"}),
    )
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert any("/workspaces/example-web" in e for e in r.explore)


def test_resolve_container_here_explore_prefers_local_checkout(tmp_path: Path):
    e = RelatedEntry(
        name="example-web", delegate="agent-containers",
        locus=Locus(preferred="container",
                    container={"repo": "org/example-web-codespaces",
                               "workspace_folder": "/workspaces/example-web",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "container"
    assert r.available_here is True
    assert r.explore
    assert any("agent-containers up example-web" in e for e in r.explore)
    assert any("reuse" in e.lower() for e in r.explore)


def test_resolve_container_elsewhere_explore_delegates(tmp_path: Path):
    e = RelatedEntry(
        name="example-web", delegate="agent-bridge",
        locus=Locus(preferred="container",
                    container={"repo": "org/example-web-codespaces",
                               "machines": ["cloud1"]}),
    )
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.available_here is False
    assert r.explore
    assert any("cloud1" in e for e in r.explore)
    assert any("agent-bridge send" in e for e in r.explore)


def test_resolve_machine_elsewhere_has_explore_hint(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(preferred="machine:cloud1"),
                     delegate="agent-bridge")
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path=None, adopted=True,
    )
    assert r.explore
    assert any("agent-bridge send cloud1" in e for e in r.explore)


def test_resolve_local_here_has_no_explore_hint(tmp_path: Path):
    # A checkout on THIS machine needs no nudge -- just grep/read it directly.
    e = RelatedEntry(name="ce", locus=Locus(preferred="local"))
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path="D:/Src/ce", adopted=True,
    )
    assert r.explore == []


def test_resolve_local_elsewhere_explore_delegates(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(machines=["cloud1", "book2"]),
                     delegate="agent-bridge")
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path=None, adopted=False,
    )
    assert r.available_here is False
    assert r.explore
    assert any("cloud1" in e for e in r.explore)


def test_resolve_local_unavailable_on_this_machine(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(machines=["cloud1", "book2"]),
                     delegate="agent-bridge")
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="worktree",
        repo_path=None, adopted=False,
    )
    assert r.available_here is False
    assert any("cloud1" in n for n in r.notes)


def test_cli_resolve_uses_primary_when_no_name(tmp_path: Path, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run

    run(["add", "ce", "--repo", str(tmp_path), "--locus", "local", "--no-scaffold"])
    run(["primary", "ce", "--repo", str(tmp_path)])
    capfd.readouterr()
    assert run(["resolve", "--repo", str(tmp_path)]) == 0   # no name -> primary
    out = capfd.readouterr().out
    assert "ce" in out and "Plan" in out


def test_cli_add_codespace_flags(tmp_path: Path):
    from agent_worktrees.__main__ import cmd_related_dispatch as run

    rc = run(["add", "example-web", "--repo", str(tmp_path), "--locus", "codespace",
              "--cs-repo", "org/example-web-codespaces", "--cs-machine", "big",
              "--cs-location", "EastUs", "--no-scaffold"])
    assert rc == 0
    e = related.get_related(tmp_path, "example-web")
    assert e.locus.preferred == "codespace"
    assert e.locus.codespace == {"repo": "org/example-web-codespaces",
                                 "machine": "big", "location": "EastUs"}


# ---------------------------------------------------------------------------
# container venue (local Docker fleet, machine-restricted)
# ---------------------------------------------------------------------------

def test_container_venue_roundtrip(tmp_path: Path):
    cfg = RelatedConfig(
        primary="example-web",
        related={
            "example-web": RelatedEntry(
                name="example-web", role="product", delegate="agent-codespaces",
                locus=Locus(
                    preferred="codespace",
                    codespace={"repo": "org/example-web-codespaces",
                               "workspace_folder": "/workspaces/example-web"},
                    container={"repo": "org/example-web-codespaces",
                               "workspace_folder": "/workspaces/example-web",
                               "machines": ["dev6"]},
                ),
            ),
        },
    )
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path).related["example-web"]
    assert got.locus.codespace["workspace_folder"] == "/workspaces/example-web"
    assert got.locus.container["repo"] == "org/example-web-codespaces"
    assert got.locus.container["machines"] == ["dev6"]   # list preserved


def test_container_emitted_yaml_is_valid(tmp_path: Path):
    cfg = RelatedConfig(related={
        "x": RelatedEntry(name="x", locus=Locus(
            preferred="container",
            container={"repo": "org/x-codespaces", "machines": ["dev6", "cloud1"]},
        )),
    })
    related.write_related(tmp_path, cfg)
    data = yaml.safe_load(related.related_path(tmp_path).read_text(encoding="utf-8"))
    ct = data["related"]["x"]["locus"]["container"]
    assert ct["repo"] == "org/x-codespaces"
    assert ct["machines"] == ["dev6", "cloud1"]


def test_cli_add_container_flags(tmp_path: Path):
    from agent_worktrees.__main__ import cmd_related_dispatch as run

    rc = run(["add", "example-web", "--repo", str(tmp_path), "--locus", "codespace",
              "--cs-repo", "org/example-web-codespaces",
              "--cs-workspace", "/workspaces/example-web",
              "--container-repo", "org/example-web-codespaces",
              "--container-workspace", "/workspaces/example-web",
              "--container-machines", "dev6", "--no-scaffold"])
    assert rc == 0
    e = related.get_related(tmp_path, "example-web")
    assert e.locus.codespace["workspace_folder"] == "/workspaces/example-web"
    assert e.locus.container == {"repo": "org/example-web-codespaces",
                                 "workspace_folder": "/workspaces/example-web",
                                 "machines": ["dev6"]}


def test_resolve_container_available_here(tmp_path: Path):
    e = RelatedEntry(
        name="example-web", delegate="agent-containers",
        locus=Locus(preferred="container",
                    container={"repo": "org/example-web-codespaces",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "container"
    assert r.available_here is True
    assert any("agent-containers up example-web" in s for s in r.steps)
    assert any("agent-bridge send container:" in s for s in r.steps)


def test_resolve_container_unavailable_elsewhere_falls_back(tmp_path: Path):
    e = RelatedEntry(
        name="example-web",
        locus=Locus(preferred="container",
                    codespace={"repo": "org/example-web-codespaces"},
                    container={"repo": "org/example-web-codespaces",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="host-cloud1", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.available_here is False
    assert any("only available on: dev6" in n for n in r.notes)
    # CodeSpace is offered as the machine-agnostic fallback
    assert any("agent-codespaces create org/example-web-codespaces" in n for n in r.notes)


def test_resolve_codespace_notes_container_alternative_here(tmp_path: Path):
    e = RelatedEntry(
        name="example-web", delegate="agent-codespaces",
        locus=Locus(preferred="codespace",
                    codespace={"repo": "org/example-web-codespaces",
                               "workspace_folder": "/workspaces/example-web"},
                    container={"repo": "org/example-web-codespaces",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="host-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "codespace"
    assert any("/workspaces/example-web" in n for n in r.notes)
    assert any("container fleet is also available here" in n for n in r.notes)


# ---------------------------------------------------------------------------
# Related-repo plugins (side-loaded by agent-bridge)
# ---------------------------------------------------------------------------

def test_plugins_roundtrip(tmp_path: Path):
    cfg = RelatedConfig(related={
        "example-web": RelatedEntry(
            name="example-web",
            plugins=[
                {"source": "example-web-codespace@example-marketplace", "enable": True},
                {"source": "extra@example-marketplace", "enable": False},
            ],
        ),
    })
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path).related["example-web"]
    assert got.plugins == [
        {"source": "example-web-codespace@example-marketplace", "enable": True},
        {"source": "extra@example-marketplace", "enable": False},
    ]


def test_plugins_parse_shorthand_dedup_and_invalid(tmp_path: Path):
    (tmp_path / ".agent-worktrees").mkdir()
    related.related_path(tmp_path).write_text(
        "related:\n"
        "  x:\n"
        "    plugins:\n"
        "      - bare@mkt\n"                       # bare string -> enable true
        "      - { source: withflag@mkt, enable: false }\n"
        "      - { source: bare@mkt }\n"           # duplicate of first (last wins)
        "      - { enable: true }\n"               # no source -> skipped
        "      - 42\n",                            # non-str/dict -> skipped
        encoding="utf-8",
    )
    got = related.read_related(tmp_path).related["x"].plugins
    assert got == [
        {"source": "bare@mkt", "enable": True},
        {"source": "withflag@mkt", "enable": False},
    ]


def test_plugins_emitted_yaml_is_valid(tmp_path: Path):
    cfg = RelatedConfig(related={
        "x": RelatedEntry(name="x", plugins=[{"source": "p@m", "enable": True}]),
    })
    related.write_related(tmp_path, cfg)
    data = yaml.safe_load(related.related_path(tmp_path).read_text(encoding="utf-8"))
    assert data["related"]["x"]["plugins"] == [{"source": "p@m"}]


def test_no_plugins_emits_nothing(tmp_path: Path):
    cfg = RelatedConfig(related={"x": RelatedEntry(name="x", role="product")})
    related.write_related(tmp_path, cfg)
    text = related.related_path(tmp_path).read_text(encoding="utf-8")
    assert "plugins" not in text


# ---------------------------------------------------------------------------
# Per-entry ``pr:`` block (control-plane-driven foreign-repo PR workflow)
# ---------------------------------------------------------------------------

def test_pr_roundtrip(tmp_path: Path):
    cfg = RelatedConfig(related={
        "spark-transpile": RelatedEntry(
            name="spark-transpile",
            role="tooling",
            pr={
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
                "api_base": "https://your-org.visualstudio.com",
                "approval_required": True,
                "squash": True,
                "delete_source_branch": True,
            },
        ),
    })
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path).related["spark-transpile"]
    assert got.pr == {
        "enabled": True,
        "required": True,
        "provider": "azure-devops",
        "api_base": "https://your-org.visualstudio.com",
        "approval_required": True,
        "squash": True,
        "delete_source_branch": True,
    }


def test_pr_parse_nonmapping_is_empty(tmp_path: Path):
    (tmp_path / ".agent-worktrees").mkdir()
    related.related_path(tmp_path).write_text(
        "related:\n"
        "  x:\n"
        "    pr: not-a-mapping\n"
        "  y:\n"
        "    role: tooling\n",           # no pr at all
        encoding="utf-8",
    )
    rc = related.read_related(tmp_path)
    assert rc.related["x"].pr == {}
    assert rc.related["y"].pr == {}


def test_pr_emitted_yaml_is_valid_with_native_bools(tmp_path: Path):
    cfg = RelatedConfig(related={
        "x": RelatedEntry(name="x", pr={
            "enabled": True,
            "provider": "azure-devops",
            "api_base": "https://your-org.visualstudio.com",
            "approval_required": False,
        }),
    })
    related.write_related(tmp_path, cfg)
    data = yaml.safe_load(related.related_path(tmp_path).read_text(encoding="utf-8"))
    assert data["related"]["x"]["pr"] == {
        "enabled": True,                      # a real bool, not the string "True"
        "provider": "azure-devops",
        "api_base": "https://your-org.visualstudio.com",
        "approval_required": False,
    }


def test_no_pr_emits_nothing(tmp_path: Path):
    cfg = RelatedConfig(related={"x": RelatedEntry(name="x", role="tooling")})
    related.write_related(tmp_path, cfg)
    data = yaml.safe_load(related.related_path(tmp_path).read_text(encoding="utf-8"))
    assert "pr" not in data["related"]["x"]


# ── control-plane fallback (resolve/show/doc from a non-control-plane cwd) ────

def _cp_paths(p):
    s = str(p)
    return {"windows": s, "linux": s, "wsl": s}


def test_control_plane_project_mapping_form(tmp_path: Path):
    (tmp_path / "machines.yaml").write_text(
        "control_plane:\n  project: dotfiles\nmachines: {}\n", encoding="utf-8")
    assert related._control_plane_project(tmp_path) == "dotfiles"


def test_control_plane_project_bare_form(tmp_path: Path):
    (tmp_path / "machines.yaml").write_text(
        "control_plane: dotfiles\nmachines: {}\n", encoding="utf-8")
    assert related._control_plane_project(tmp_path) == "dotfiles"


def test_control_plane_project_absent_or_undeclared(tmp_path: Path):
    assert related._control_plane_project(tmp_path) is None  # no machines.yaml
    (tmp_path / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    assert related._control_plane_project(tmp_path) is None  # no control_plane


def test_find_control_plane_anchor(tmp_path: Path, monkeypatch):
    from agent_worktrees import repos
    cp = tmp_path / "dotfiles"; cp.mkdir()
    other = tmp_path / "other"; other.mkdir()
    (cp / "machines.yaml").write_text(
        "control_plane:\n  project: dotfiles\nmachines: {}\n", encoding="utf-8")
    entries = [
        repos.RepoEntry(name="other", paths=_cp_paths(other)),
        repos.RepoEntry(name="dotfiles", repo_class="worktree",
                        paths=_cp_paths(cp)),
    ]
    monkeypatch.setattr(repos, "list_repos", lambda class_filter=None: entries)
    assert related.find_control_plane_anchor() == str(cp)


def test_find_control_plane_anchor_none_when_undeclared(tmp_path: Path, monkeypatch):
    from agent_worktrees import repos
    other = tmp_path / "other"; other.mkdir()
    monkeypatch.setattr(
        repos, "list_repos",
        lambda class_filter=None: [
            repos.RepoEntry(name="other", paths=_cp_paths(other))])
    assert related.find_control_plane_anchor() is None


def test_related_lookup_anchors_falls_back_to_control_plane(monkeypatch):
    from agent_worktrees import __main__ as cli
    monkeypatch.setattr(cli, "_related_config_source_anchors", lambda base: [base])
    monkeypatch.setattr(
        related, "get_related_grafted",
        lambda anchors, name: object()
        if any(str(a).endswith("cp") for a in anchors) else None)
    monkeypatch.setattr(related, "find_control_plane_anchor",
                        lambda: "/tmp/cp")
    anchors, via = cli._related_lookup_anchors([], "/tmp/cwd", "copilot-extensions")
    assert via is True
    assert any(str(a).endswith("cp") for a in anchors)


def test_related_lookup_anchors_local_hit_skips_fallback(monkeypatch):
    from agent_worktrees import __main__ as cli
    monkeypatch.setattr(cli, "_related_config_source_anchors", lambda base: [base])
    monkeypatch.setattr(related, "get_related_grafted",
                        lambda anchors, name: object())

    def _boom():
        raise AssertionError("control-plane lookup must not run on a local hit")

    monkeypatch.setattr(related, "find_control_plane_anchor", _boom)
    anchors, via = cli._related_lookup_anchors([], "/tmp/cwd", "x")
    assert anchors == ["/tmp/cwd"] and via is False


def test_related_lookup_anchors_respects_explicit_repo(monkeypatch):
    from agent_worktrees import __main__ as cli
    monkeypatch.setattr(cli, "_related_config_source_anchors", lambda base: [base])
    calls = {"cp": 0}

    def _fcp():
        calls["cp"] += 1
        return "/tmp/cp"

    monkeypatch.setattr(related, "find_control_plane_anchor", _fcp)
    anchors, via = cli._related_lookup_anchors(["--repo", "/tmp/cwd"], "/tmp/cwd", "x")
    assert anchors == ["/tmp/cwd"] and via is False
    assert calls["cp"] == 0  # explicit --repo pins the anchor, no fallback


# ---------------------------------------------------------------------------
# related doctor -- diagnose_related (validate related.yaml against reality)
# ---------------------------------------------------------------------------

def _cfg(**entries: RelatedEntry) -> RelatedConfig:
    return RelatedConfig(related=dict(entries))


def _diag(cfg, *, current="dev6", known=lambda k: True, mavail=True,
          has=lambda n: False, remote=lambda n: ""):
    return related.diagnose_related(
        cfg, current_machine=current, machine_known=known,
        machines_known_available=mavail, registry_has=has,
        registry_remote=remote,
    )


def _only(findings, kind):
    return [f for f in findings if f.kind == kind]


def test_doctor_local_unregistered_here_with_remote():
    """Entry claims a local checkout on THIS machine but repos.yaml lacks it ->
    the headline warning, with a clone action because a remote is known."""
    e = RelatedEntry(name="spark", locus=Locus(preferred="local", machines=["dev6"]))
    findings = _diag(_cfg(spark=e), has=lambda n: False,
                     remote=lambda n: "https://example/spark.git")
    f = _only(findings, "local_repo_unregistered")
    assert len(f) == 1 and f[0].severity == related.SEV_WARNING
    assert any("clone" in a for a in f[0].suggested_actions)
    assert any("approval" in a for a in f[0].suggested_actions)  # never auto-remove


def test_doctor_local_unregistered_here_no_remote():
    """No known remote -> the action asks the user to provide a URL."""
    e = RelatedEntry(name="spark", locus=Locus(preferred="local", machines=["dev6"]))
    findings = _diag(_cfg(spark=e), remote=lambda n: "")
    f = _only(findings, "local_repo_unregistered")
    assert len(f) == 1
    assert any("provide a remote URL" in a for a in f[0].suggested_actions)


def test_doctor_registered_here_is_clean():
    """Registered on this machine -> no local_repo_unregistered finding."""
    e = RelatedEntry(name="spark", locus=Locus(preferred="local", machines=["dev6"]))
    findings = _diag(_cfg(spark=e), has=lambda n: True)
    assert _only(findings, "local_repo_unregistered") == []


def test_doctor_codespace_missing_registry_is_not_flagged():
    """A codespace locus is provisioned from its venue, not the local registry,
    so a missing repos.yaml entry there is NOT a defect."""
    e = RelatedEntry(name="web", locus=Locus(
        preferred="codespace", codespace={"repo": "org/web-codespaces"}))
    findings = _diag(_cfg(web=e), has=lambda n: False)
    assert _only(findings, "local_repo_unregistered") == []


def test_doctor_codespace_missing_repo_is_error():
    e = RelatedEntry(name="web", locus=Locus(preferred="codespace",
                                             codespace={"machine": "big"}))
    findings = _diag(_cfg(web=e))
    f = _only(findings, "codespace_missing_repo")
    assert len(f) == 1 and f[0].severity == related.SEV_ERROR


def test_doctor_container_missing_repo_is_error():
    e = RelatedEntry(name="web", locus=Locus(preferred="container",
                                             container={"machines": ["dev6"]}))
    findings = _diag(_cfg(web=e))
    assert len(_only(findings, "container_missing_repo")) == 1


def test_doctor_unknown_machine_flagged():
    e = RelatedEntry(name="x", locus=Locus(preferred="local",
                                           machines=["dev6", "bogus"]))
    findings = _diag(_cfg(x=e), known=lambda k: k.lower() == "dev6",
                     has=lambda n: True)
    f = _only(findings, "unknown_machine")
    assert len(f) == 1 and "bogus" in f[0].detail


def test_doctor_unknown_machine_skipped_when_machines_yaml_unavailable():
    e = RelatedEntry(name="x", locus=Locus(preferred="local", machines=["bogus"]))
    findings = _diag(_cfg(x=e), mavail=False, known=lambda k: False,
                     has=lambda n: True)
    assert _only(findings, "unknown_machine") == []


def test_doctor_crossmachine_unverifiable_is_info():
    """Local entry targeting only other machines -> info, not an error."""
    e = RelatedEntry(name="x", locus=Locus(preferred="local", machines=["cloud1"]))
    findings = _diag(_cfg(x=e), current="dev6", has=lambda n: False)
    assert _only(findings, "local_repo_unregistered") == []
    f = _only(findings, "crossmachine_unverifiable")
    assert len(f) == 1 and f[0].severity == related.SEV_INFO


def test_doctor_container_machines_validated():
    """container.machines keys are validated against machines.yaml too."""
    e = RelatedEntry(name="x", locus=Locus(
        preferred="container", container={"repo": "o/x", "machines": ["ghost"]}))
    findings = _diag(_cfg(x=e), known=lambda k: False)
    assert len(_only(findings, "unknown_machine")) == 1


def test_doctor_empty_locus_is_info():
    e = RelatedEntry(name="x", locus=Locus())
    findings = _diag(_cfg(x=e))
    f = _only(findings, "empty_locus")
    assert len(f) == 1 and f[0].severity == related.SEV_INFO


def test_doctor_local_no_machines_is_available_here():
    """A bare `preferred: local` (no machines) means available on every machine,
    so an unregistered repo here is flagged."""
    e = RelatedEntry(name="x", locus=Locus(preferred="local"))
    findings = _diag(_cfg(x=e), has=lambda n: False)
    assert len(_only(findings, "local_repo_unregistered")) == 1


# ---------------------------------------------------------------------------
# Ownership: schema round-trip, normalization, derivation, query, backfill
# ---------------------------------------------------------------------------

def test_ownership_roundtrips(tmp_path: Path):
    cfg = RelatedConfig(related={
        "mine": RelatedEntry(name="mine", ownership="owned", owner="me"),
        "org": RelatedEntry(name="org", ownership="internal"),
    })
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path)
    assert got.related["mine"].ownership == "owned"
    assert got.related["mine"].owner == "me"
    assert got.related["org"].ownership == "internal"
    # Emitted YAML is valid + carries the fields.
    data = yaml.safe_load(related.related_path(tmp_path).read_text(encoding="utf-8"))
    assert data["related"]["mine"]["ownership"] == "owned"
    assert data["related"]["mine"]["owner"] == "me"


def test_normalize_ownership_drops_unknown():
    assert related.normalize_ownership("OWNED") == "owned"
    assert related.normalize_ownership("  internal ") == "internal"
    assert related.normalize_ownership("public") == ""   # not in VALID_OWNERSHIP
    assert related.normalize_ownership(None) == ""


def test_read_drops_bogus_ownership(tmp_path: Path):
    related.related_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    related.related_path(tmp_path).write_text(
        "related:\n  x:\n    ownership: bogus\n    owner: me\n", encoding="utf-8")
    e = related.read_related(tmp_path).related["x"]
    assert e.ownership == ""       # bogus dropped
    assert e.owner == "me"         # owner preserved


def test_no_ownership_emits_nothing(tmp_path: Path):
    cfg = RelatedConfig(related={"x": RelatedEntry(name="x", role="tooling")})
    related.write_related(tmp_path, cfg)
    data = yaml.safe_load(related.related_path(tmp_path).read_text(encoding="utf-8"))
    assert "ownership" not in data["related"]["x"]
    assert "owner" not in data["related"]["x"]


def test_upsert_merges_ownership(tmp_path: Path):
    related.upsert_related(tmp_path, RelatedEntry(name="x", role="tooling"))
    related.upsert_related(tmp_path, RelatedEntry(name="x", ownership="owned",
                                                  owner="me"))
    e = related.get_related(tmp_path, "x")
    assert e.role == "tooling"     # preserved
    assert e.ownership == "owned"  # added
    assert e.owner == "me"


def _patch_registry(monkeypatch, remotes: dict[str, str], logins=("me",)):
    """Stub repos.find_repo/github_owner-driving state + operator logins."""
    from agent_worktrees import repos

    def _find(name):
        if name not in remotes:
            return None
        return repos.RepoEntry(name=name, remote=remotes[name])

    monkeypatch.setattr(repos, "find_repo", _find)
    monkeypatch.setattr(related, "_operator_logins",
                        lambda: {l.casefold() for l in logins})


def test_classify_owned_from_operator_login(monkeypatch):
    _patch_registry(monkeypatch, {
        "dotfiles": "https://github.com/me/dotfiles.git"}, logins=("me",))
    assert related.classify_ownership("dotfiles") == ("owned", "me")


def test_classify_ado_is_internal(monkeypatch):
    _patch_registry(monkeypatch, {
        "ado-tools": "https://example.visualstudio.com/Team/_git/ado-tools"})
    own, _owner = related.classify_ownership("ado-tools")
    assert own == "internal"


def test_classify_dev_azure_is_internal(monkeypatch):
    _patch_registry(monkeypatch, {
        "x": "https://dev.azure.com/org/proj/_git/x"})
    assert related.classify_ownership("x")[0] == "internal"


def test_classify_public_github_org_unclassified(monkeypatch):
    _patch_registry(monkeypatch, {
        "web": "https://github.com/some-org/web.git"}, logins=("me",))
    assert related.classify_ownership("web")[0] == ""   # operator curates


def test_classify_unknown_repo_unclassified(monkeypatch):
    _patch_registry(monkeypatch, {}, logins=("me",))
    assert related.classify_ownership("ghost") == ("", "")


def test_effective_ownership_explicit_wins(monkeypatch):
    # ADO repo derives 'internal', but an explicit 'owned' overrides.
    _patch_registry(monkeypatch, {
        "ado-tools": "https://example.visualstudio.com/Team/_git/ado-tools"})
    e_owned = RelatedEntry(name="ado-tools", ownership="owned")
    e_unset = RelatedEntry(name="ado-tools")
    assert related.effective_ownership(e_owned) == "owned"
    assert related.effective_ownership(e_unset) == "internal"


def test_owned_targets_lists_owned_only(tmp_path: Path, monkeypatch):
    _patch_registry(monkeypatch, {
        "dotfiles": "https://github.com/me/dotfiles.git",
        "ado-tools": "https://example.visualstudio.com/Team/_git/ado-tools",
        "web": "https://github.com/some-org/web.git",
    }, logins=("me",))
    cfg = RelatedConfig(related={
        "dotfiles": RelatedEntry(name="dotfiles"),                 # derived owned
        "ado-tools": RelatedEntry(name="ado-tools", ownership="owned"),  # explicit
        "web": RelatedEntry(name="web"),                          # unclassified
    })
    related.write_related(tmp_path, cfg)
    owned = {t["name"] for t in related.owned_targets(tmp_path)}
    assert owned == {"dotfiles", "ado-tools"}
    # slug is owner/name for github; empty for ADO.
    by_name = {t["name"]: t for t in related.owned_targets(tmp_path)}
    assert by_name["dotfiles"]["slug"] == "me/dotfiles"
    assert by_name["ado-tools"]["slug"] == ""


def test_owned_targets_grafted_overlay_demotes(tmp_path: Path, monkeypatch):
    """A later (overlay) anchor that reclassifies an ``owned`` repo to
    ``internal`` must drop it from the grafted owned set (overlay precedence)."""
    _patch_registry(monkeypatch, {
        "shared": "https://github.com/me/shared.git"}, logins=("me",))
    base = tmp_path / "base"; overlay = tmp_path / "overlay"
    base.mkdir(); overlay.mkdir()
    related.write_related(base, RelatedConfig(related={
        "shared": RelatedEntry(name="shared", ownership="owned")}))
    # Single-anchor view: owned.
    assert [t["name"] for t in related.owned_targets(base)] == ["shared"]
    # Overlay demotes it -> grafted view drops it.
    related.write_related(overlay, RelatedConfig(related={
        "shared": RelatedEntry(name="shared", ownership="internal")}))
    grafted = related.owned_targets_grafted([base, overlay])
    assert grafted == []


def test_classify_all_fills_unset_only(tmp_path: Path, monkeypatch):
    _patch_registry(monkeypatch, {
        "dotfiles": "https://github.com/me/dotfiles.git",
        "ado-tools": "https://example.visualstudio.com/Team/_git/ado-tools",
    }, logins=("me",))
    cfg = RelatedConfig(related={
        "dotfiles": RelatedEntry(name="dotfiles"),
        "ado-tools": RelatedEntry(name="ado-tools", ownership="owned"),  # curated
    })
    related.write_related(tmp_path, cfg)
    changed = related.classify_all(tmp_path)
    names = {c["name"] for c in changed}
    assert names == {"dotfiles"}   # only the unset one
    got = related.read_related(tmp_path)
    assert got.related["dotfiles"].ownership == "owned"
    assert got.related["ado-tools"].ownership == "owned"  # untouched curation


def test_classify_all_overwrite_redoes_explicit(tmp_path: Path, monkeypatch):
    _patch_registry(monkeypatch, {
        "ado-tools": "https://example.visualstudio.com/Team/_git/ado-tools",
    }, logins=("me",))
    # Explicitly (mis)marked owned; --overwrite re-derives to internal.
    cfg = RelatedConfig(related={
        "ado-tools": RelatedEntry(name="ado-tools", ownership="owned")})
    related.write_related(tmp_path, cfg)
    changed = related.classify_all(tmp_path, overwrite=True)
    assert {c["name"] for c in changed} == {"ado-tools"}
    assert related.read_related(tmp_path).related["ado-tools"].ownership == "internal"


def test_cli_owners_is_global_via_control_plane(tmp_path: Path, monkeypatch):
    """`related owners` reads the CONTROL-PLANE index regardless of cwd (so an
    ambient consumer gets the owned set from anywhere), never raising the
    cwd-anchor guard."""
    from agent_worktrees import __main__ as cli
    cp = tmp_path / "control-plane"; cp.mkdir()
    _patch_registry(monkeypatch, {
        "mine": "https://github.com/me/mine.git",
        "org": "https://github.com/some-org/org.git",
    }, logins=("me",))
    related.write_related(cp, RelatedConfig(related={
        "mine": RelatedEntry(name="mine", ownership="owned"),
        "org": RelatedEntry(name="org", ownership="internal"),
    }))
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: str(cp))
    # No --repo, and _related_anchor would not resolve a project here: the
    # control-plane path must still answer.
    monkeypatch.setattr(cli, "_related_anchor", lambda rest: None)
    captured: dict = {}
    monkeypatch.setattr(cli, "_json_output", lambda payload: captured.update(payload))
    rc = cli.cmd_related_dispatch(["owners", "--json"])
    assert rc == 0
    assert captured["source"] == "control-plane"
    assert [t["name"] for t in captured["owned"]] == ["mine"]


# ---------------------------------------------------------------------------
# Current-machine detection for `related list` / `related resolve`
# ---------------------------------------------------------------------------

class TestRelatedCurrentMachine:
    """`_related_current_machine` must find the anchor holding machines.yaml.

    Given an anchor without a registry, `detect_machine` silently falls back to
    the raw hostname -- which does not match the registry *key* a locus lists,
    so a locally-checked-out repo reports "Not checked out on '<host>'".
    """

    @staticmethod
    def _write_registry(anchor: Path, key: str, hostname: str) -> None:
        anchor.mkdir(parents=True, exist_ok=True)
        (anchor / "machines.yaml").write_text(
            f"machines:\n  {key}:\n    hostname: {hostname}\n", encoding="utf-8"
        )

    def test_plugin_graft_anchor_does_not_mask_the_registry(self, tmp_path, monkeypatch):
        # `_related_config_source_anchors` puts installed-plugin graft anchors
        # FIRST (lowest precedence). A plugin ships related.yaml but no
        # machines.yaml, so anchors[0] has no registry.
        from agent_worktrees import __main__ as m
        from agent_worktrees import config as cfg

        base = tmp_path / "harness"
        plugin = tmp_path / "plugin"
        plugin.mkdir(parents=True)
        self._write_registry(base, "host", "host.local")
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "host.local")

        assert m._related_current_machine([str(plugin), str(base)], str(base)) == "host"

    def test_falls_back_to_a_later_anchor(self, tmp_path, monkeypatch):
        # Control-plane fallback: the base anchor has no registry, a later
        # anchor does.
        from agent_worktrees import __main__ as m
        from agent_worktrees import config as cfg

        base = tmp_path / "product"
        base.mkdir(parents=True)
        cp = tmp_path / "control-plane"
        self._write_registry(cp, "host", "host.local")
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "host.local")

        assert m._related_current_machine([str(cp)], str(base)) == "host"

    def test_no_registry_anywhere_falls_back_to_hostname(self, tmp_path, monkeypatch):
        from agent_worktrees import __main__ as m
        from agent_worktrees import config as cfg

        base = tmp_path / "bare"
        base.mkdir(parents=True)
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "host.local")

        assert m._related_current_machine([str(base)], str(base)) == "host.local"
