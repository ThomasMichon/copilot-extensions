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
