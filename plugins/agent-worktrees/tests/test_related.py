"""Tests for the per-project "related repos" layer (related.yaml)."""

from __future__ import annotations

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
    assert related.default_doc_rel("odsp-web") == "related/odsp-web.md"


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
        primary="odsp-web",
        related={
            "odsp-web": RelatedEntry(
                name="odsp-web",
                role="product",
                summary="Primary product monorepo.",
                doc="related/odsp-web.md",
                locus=Locus(
                    preferred="codespace",
                    codespace={"repo": "org/odsp-web-codespaces",
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

    assert got.primary == "odsp-web"
    assert set(got.related) == {"odsp-web", "copilot-extensions"}

    ow = got.related["odsp-web"]
    assert ow.role == "product"
    assert ow.summary == "Primary product monorepo."
    assert ow.doc == "related/odsp-web.md"
    assert ow.locus.preferred == "codespace"
    assert ow.locus.codespace["repo"] == "org/odsp-web-codespaces"
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
    related.set_primary(tmp_path, "odsp-web")
    assert related.get_primary(tmp_path) == "odsp-web"


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
    e = RelatedEntry(name="odsp-web", role="product", summary="The product.")
    path, created = related.scaffold_doc(tmp_path, e)
    assert created is True
    assert path == related.doc_abs_path(tmp_path, e)
    text = path.read_text(encoding="utf-8")
    assert "# odsp-web — related repo" in text
    assert "product" in text
    assert "repos find odsp-web" in text          # the no-hardcoded-path rule

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
    ("dev6", "tmichon-dev6", True),
    ("dev6", "dev6", True),
    ("dev6", "DEV6", True),
    ("cloud1", "tmichon-dev6", False),
    ("dev6", "tmichon-dev6-wsl", False),   # last segment is 'wsl'
    ("", "tmichon-dev6", False),
])
def test_machine_matches(key, current, expected):
    assert related.machine_matches(key, current) is expected


def test_resolve_local_worktree_adopted(tmp_path: Path):
    e = RelatedEntry(name="ce", locus=Locus(preferred="local"))
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="worktree",
        repo_path="D:/Src/ce", adopted=True,
    )
    assert r.locus_kind == "local"
    assert r.available_here is True
    assert r.editing_model == "worktree"
    assert any("ce --new" in s for s in r.steps)


def test_resolve_worktree_unadopted_suggests_register(tmp_path: Path):
    e = RelatedEntry(name="aih")
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="worktree",
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
        e, current_machine="tmichon-dev6", repo_class="worktree",
        repo_path=None, adopted=True,
    )
    assert r.locus_kind == "machine"
    assert r.target_machine == "cloud1"
    assert r.available_here is False
    assert any("agent-bridge send cloud1" in s for s in r.steps)


def test_resolve_machine_here_is_local(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(preferred="machine:dev6"))
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="singleton",
        repo_path="D:/Git/x", adopted=False,
    )
    assert r.available_here is True
    assert r.editing_model == "anchor"
    assert any("anchor checkout directly" in s for s in r.steps)


def test_resolve_codespace(tmp_path: Path):
    e = RelatedEntry(
        name="odsp-web", delegate="agent-codespaces",
        locus=Locus(preferred="codespace",
                    codespace={"repo": "org/odsp-web-codespaces",
                               "machine": "largePremiumLinux256gb",
                               "location": "EastUs"}),
    )
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "codespace"
    assert r.available_here is True
    assert any("gh cs create -R org/odsp-web-codespaces" in s for s in r.steps)
    assert any("agent-bridge send codespace:" in s for s in r.steps)


def test_resolve_local_unavailable_on_this_machine(tmp_path: Path):
    e = RelatedEntry(name="x", locus=Locus(machines=["cloud1", "book2"]),
                     delegate="agent-bridge")
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="worktree",
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

    rc = run(["add", "odsp-web", "--repo", str(tmp_path), "--locus", "codespace",
              "--cs-repo", "org/odsp-web-codespaces", "--cs-machine", "big",
              "--cs-location", "EastUs", "--no-scaffold"])
    assert rc == 0
    e = related.get_related(tmp_path, "odsp-web")
    assert e.locus.preferred == "codespace"
    assert e.locus.codespace == {"repo": "org/odsp-web-codespaces",
                                 "machine": "big", "location": "EastUs"}


# ---------------------------------------------------------------------------
# container venue (local Docker fleet, machine-restricted)
# ---------------------------------------------------------------------------

def test_container_venue_roundtrip(tmp_path: Path):
    cfg = RelatedConfig(
        primary="odsp-web",
        related={
            "odsp-web": RelatedEntry(
                name="odsp-web", role="product", delegate="agent-codespaces",
                locus=Locus(
                    preferred="codespace",
                    codespace={"repo": "org/odsp-web-codespaces",
                               "workspace_folder": "/workspaces/odsp-web"},
                    container={"repo": "org/odsp-web-codespaces",
                               "workspace_folder": "/workspaces/odsp-web",
                               "machines": ["dev6"]},
                ),
            ),
        },
    )
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path).related["odsp-web"]
    assert got.locus.codespace["workspace_folder"] == "/workspaces/odsp-web"
    assert got.locus.container["repo"] == "org/odsp-web-codespaces"
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

    rc = run(["add", "odsp-web", "--repo", str(tmp_path), "--locus", "codespace",
              "--cs-repo", "org/odsp-web-codespaces",
              "--cs-workspace", "/workspaces/odsp-web",
              "--container-repo", "org/odsp-web-codespaces",
              "--container-workspace", "/workspaces/odsp-web",
              "--container-machines", "dev6", "--no-scaffold"])
    assert rc == 0
    e = related.get_related(tmp_path, "odsp-web")
    assert e.locus.codespace["workspace_folder"] == "/workspaces/odsp-web"
    assert e.locus.container == {"repo": "org/odsp-web-codespaces",
                                 "workspace_folder": "/workspaces/odsp-web",
                                 "machines": ["dev6"]}


def test_resolve_container_available_here(tmp_path: Path):
    e = RelatedEntry(
        name="odsp-web", delegate="agent-containers",
        locus=Locus(preferred="container",
                    container={"repo": "org/odsp-web-codespaces",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "container"
    assert r.available_here is True
    assert any("agent-containers up odsp-web" in s for s in r.steps)
    assert any("agent-bridge send container:" in s for s in r.steps)


def test_resolve_container_unavailable_elsewhere_falls_back(tmp_path: Path):
    e = RelatedEntry(
        name="odsp-web",
        locus=Locus(preferred="container",
                    codespace={"repo": "org/odsp-web-codespaces"},
                    container={"repo": "org/odsp-web-codespaces",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="tmichon-cloud1", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.available_here is False
    assert any("only available on: dev6" in n for n in r.notes)
    # CodeSpace is offered as the machine-agnostic fallback
    assert any("gh cs create -R org/odsp-web-codespaces" in n for n in r.notes)


def test_resolve_codespace_notes_container_alternative_here(tmp_path: Path):
    e = RelatedEntry(
        name="odsp-web", delegate="agent-codespaces",
        locus=Locus(preferred="codespace",
                    codespace={"repo": "org/odsp-web-codespaces",
                               "workspace_folder": "/workspaces/odsp-web"},
                    container={"repo": "org/odsp-web-codespaces",
                               "machines": ["dev6"]}),
    )
    r = related.build_resolution(
        e, current_machine="tmichon-dev6", repo_class="reference",
        repo_path=None, adopted=False,
    )
    assert r.locus_kind == "codespace"
    assert any("/workspaces/odsp-web" in n for n in r.notes)
    assert any("container fleet is also available here" in n for n in r.notes)


# ---------------------------------------------------------------------------
# Related-repo plugins (side-loaded by agent-bridge)
# ---------------------------------------------------------------------------

def test_plugins_roundtrip(tmp_path: Path):
    cfg = RelatedConfig(related={
        "odsp-web": RelatedEntry(
            name="odsp-web",
            plugins=[
                {"source": "odsp-web-codespace@dev-tmichon", "enable": True},
                {"source": "extra@dev-tmichon", "enable": False},
            ],
        ),
    })
    related.write_related(tmp_path, cfg)
    got = related.read_related(tmp_path).related["odsp-web"]
    assert got.plugins == [
        {"source": "odsp-web-codespace@dev-tmichon", "enable": True},
        {"source": "extra@dev-tmichon", "enable": False},
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
