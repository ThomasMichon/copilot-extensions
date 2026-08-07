"""Tests for the harness-knowledge bind_knowledge configurator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "skills" / "binding-knowledge" / "scripts" / "bind_knowledge.py"
_spec = importlib.util.spec_from_file_location("bind_knowledge", _MOD)
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


# --- set_top_yaml_key ---------------------------------------------------------

def test_insert_after_comment_header():
    text = "# header comment\n# more\nrepo_name: h\n"
    out = bk.set_top_yaml_key(text, "knowledge_repo", "k")
    lines = out.splitlines()
    # inserted after the leading comment block, before repo_name
    assert lines[0].startswith("#") and lines[1].startswith("#")
    assert "knowledge_repo: k" in lines
    assert out.index("knowledge_repo") < out.index("repo_name")
    assert out.endswith("\n")


def test_replace_existing_key():
    text = "repo_name: h\nknowledge_repo: old\nother: 1\n"
    out = bk.set_top_yaml_key(text, "knowledge_repo", "new")
    assert "knowledge_repo: new" in out
    assert "old" not in out
    assert out.count("knowledge_repo:") == 1
    assert "other: 1" in out  # rest preserved


def test_replace_preserves_comments():
    text = "# c1\nknowledge_repo: old  # inline\nrepo_name: h\n"
    out = bk.set_top_yaml_key(text, "knowledge_repo", "new")
    assert "# c1" in out
    assert "knowledge_repo: new" in out
    assert "old" not in out


# --- render_instructions ------------------------------------------------------

def test_render_labels_paths_and_marker():
    md = bk.render_instructions("citadel-harness", "C:/h", "citadel-knowledge", "C:/k",
                                [("odsp-web", "C:/o")])
    assert bk.MANAGED_MARKER in md
    assert "citadel-harness" in md and "C:/h" in md
    assert "citadel-knowledge" in md and "C:/k" in md
    assert "odsp-web" in md and "C:/o" in md
    assert "state-root" in md  # routing pointer present


# --- bind (end to end, machine-local) -----------------------------------------

def test_bind_writes_pointer_and_fragment(tmp_path: Path):
    home = tmp_path / "home"
    summary = bk.bind("citadel-harness", "citadel-knowledge", "C:/k",
                      home=home, harness_path="C:/h")
    cfg = home / ".citadel-harness" / "config.yaml"
    frag = home / ".citadel-harness" / ".github" / "instructions" / "knowledge-binding.instructions.md"
    assert cfg.exists() and frag.exists()
    assert "knowledge_repo: citadel-knowledge" in cfg.read_text()
    assert "repo_name: citadel-harness" in cfg.read_text()  # seeded
    assert bk.MANAGED_MARKER in frag.read_text()
    assert summary["knowledge_repo"] == "citadel-knowledge"


def test_bind_preserves_existing_config(tmp_path: Path):
    home = tmp_path / "home"
    base = home / ".citadel-harness"
    base.mkdir(parents=True)
    (base / "config.yaml").write_text(
        "# my config\nrepo_name: citadel-harness\nrepos:\n  citadel-harness:\n    anchor: C:/h\n",
        encoding="utf-8",
    )
    bk.bind("citadel-harness", "kn", "C:/k", home=home)
    text = (base / "config.yaml").read_text()
    assert "# my config" in text
    assert "anchor: C:/h" in text  # existing structure preserved
    assert "knowledge_repo: kn" in text


def test_bind_idempotent_repoint(tmp_path: Path):
    home = tmp_path / "home"
    bk.bind("h", "k1", "C:/k1", home=home)
    bk.bind("h", "k2", "C:/k2", home=home)  # re-point
    text = (home / ".h" / "config.yaml").read_text()
    assert text.count("knowledge_repo:") == 1
    assert "knowledge_repo: k2" in text
    assert "k1" not in text


# --- bind assembles the personal-plugin overlay (#955) ------------------------

def test_bind_assembles_plugins_when_paths_known(tmp_path: Path):
    import json as _json

    home = tmp_path / "home"
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    (knowledge / ".ai").mkdir(parents=True)
    (knowledge / ".github" / "copilot").mkdir(parents=True)
    (knowledge / ".github" / "copilot" / "settings.json").write_text(_json.dumps({
        "extraKnownMarketplaces": {"kn": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"skill@kn": True},
    }), encoding="utf-8")
    summary = bk.bind("citadel-harness", "kn-repo", str(knowledge),
                      home=home, harness_path=str(harness))
    # The overlay was written into the harness checkout.
    overlay = harness / ".github" / "copilot" / "settings.local.json"
    assert overlay.exists()
    out = _json.loads(overlay.read_text())
    assert out["enabledPlugins"] == {"skill@kn": True}
    assert summary["plugins"]["count"] == 1


def test_bind_skips_assembly_without_harness_path(tmp_path: Path):
    home = tmp_path / "home"
    summary = bk.bind("h", "k", "C:/k", home=home)  # no harness_path
    assert "plugins" not in summary

