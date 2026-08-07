"""Tests for scan-customizations.py -- the loaded-set purview + external-plugin
collision remediation (the reviewing-customizations enhancement).

Stdlib + pytest only. The script has a hyphenated filename, so it is imported
from its path via importlib.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).with_name("scan-customizations.py")
_spec = importlib.util.spec_from_file_location("scan_customizations", _SCRIPT)
scan = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = scan          # dataclasses introspection needs this
_spec.loader.exec_module(scan)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _skill(dir_: Path, name: str, *, triggers: list[str] | None = None,
           folder: str | None = None, desc: str = "A test skill.") -> None:
    folder = folder or name
    d = dir_ / folder
    d.mkdir(parents=True, exist_ok=True)
    trig = ""
    if triggers:
        trig = "\n  Trigger phrases include:\n" + "\n".join(
            f"  - '{t}'" for t in triggers)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: >\n  {desc}{trig}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _settings(repo: Path, enabled: dict, marketplaces: dict) -> None:
    p = repo / ".github" / "copilot" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "enabledPlugins": enabled, "extraKnownMarketplaces": marketplaces,
    }), encoding="utf-8")


def _installed_plugin(root: Path, mkt: str, name: str, *,
                      triggers: list[str] | None = None,
                      repository: str | None = None) -> None:
    pdir = root / mkt / name
    (pdir / "skills").mkdir(parents=True, exist_ok=True)
    _skill(pdir / "skills", name, triggers=triggers)
    if repository:
        (pdir / "plugin.json").write_text(
            json.dumps({"name": name, "repository": repository}), encoding="utf-8")


# ---------------------------------------------------------------------------
# assemble_enabled_plugins
# ---------------------------------------------------------------------------

def test_assemble_directory_marketplace_is_controlled(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".ai" / "cap" / "skills").mkdir(parents=True)
    _skill(repo / ".ai" / "cap" / "skills", "cap")
    _settings(repo, {"cap@repo-plugins": True},
              {"repo-plugins": {"source": {"source": "directory", "path": "./.ai"}}})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=tmp_path / "none")
    assert len(srcs) == 1
    assert srcs[0].controlled is True
    assert srcs[0].source == ""             # in-repo -> fixable here
    assert srcs[0].origin == "repo-plugins/cap"


def test_assemble_github_marketplace_is_external_with_source(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mymarket", "ext", triggers=["do a thing"])
    _settings(repo, {"ext@mymarket": True},
              {"mymarket": {"source": {"source": "github", "repo": "owner/mrepo"}}})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)
    assert len(srcs) == 1
    assert srcs[0].controlled is False
    assert srcs[0].source == "https://github.com/owner/mrepo"


def test_assemble_source_falls_back_to_plugin_manifest(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    # No marketplace source entry -> read repository from the plugin manifest.
    _installed_plugin(installed, "mkt", "ext", repository="https://github.com/o/r")
    _settings(repo, {"ext@mkt": True}, {})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)
    assert len(srcs) == 1 and srcs[0].source == "https://github.com/o/r"


def test_assemble_skips_disabled_and_missing_footprint(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mkt", "present")
    _settings(repo, {"present@mkt": True, "absent@mkt": True, "off@mkt": False},
              {"mkt": {"source": {"source": "github", "repo": "o/r"}}})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)
    assert [s.origin for s in srcs] == ["mkt/present"]  # absent (no footprint) + off skipped


# ---------------------------------------------------------------------------
# collision annotation for external plugins
# ---------------------------------------------------------------------------

def _run_with_sources(repo: Path, sources: list) -> scan.Report:
    return scan.run(repo, sources)


def test_local_vs_external_collision_is_annotated(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".github" / "skills").mkdir(parents=True)
    _skill(repo / ".github" / "skills", "mine", triggers=["shared phrase"])
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mkt", "ext", triggers=["shared phrase"],
                      repository="https://github.com/o/r")
    ext = scan.PluginSource(
        skills_root=installed / "mkt" / "ext" / "skills",
        origin="mkt/ext", controlled=False, source="https://github.com/o/r")
    report = _run_with_sources(repo, [ext])
    coll = [f for f in report.findings if f.check == "trigger-collision"]
    assert len(coll) == 1
    m = coll[0].message
    assert "shared phrase" in m
    assert "OUTSIDE this repo's control" in m
    assert "https://github.com/o/r" in m
    assert "contributing-to-copilot-extensions" in m  # the bridge pointer


def test_controlled_plugin_gets_full_checks(tmp_path: Path):
    """An in-repo (controlled) plugin is checked like an owned skill -- a
    name/folder mismatch is a BLOCKING finding, not reference-only silence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ai = tmp_path / "ai"
    (ai / "cap" / "skills").mkdir(parents=True)
    _skill(ai / "cap" / "skills", "wrongname", folder="cap")  # name != folder
    ctrl = scan.PluginSource(skills_root=ai / "cap" / "skills",
                             origin="repo-plugins/cap", controlled=True)
    report = _run_with_sources(repo, [ctrl])
    assert any(f.check == "name-folder-match" and f.severity == scan.BLOCKING
               for f in report.findings)


def test_external_plugin_is_reference_only(tmp_path: Path):
    """An external plugin's own frontmatter problems are NOT flagged (we don't
    own it) -- only its triggers participate in collision detection."""
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    # name != folder in the external plugin -> must NOT raise a finding.
    pdir = installed / "mkt" / "ext"
    (pdir / "skills" / "cap").mkdir(parents=True)
    (pdir / "skills" / "cap" / "SKILL.md").write_text(
        "---\nname: mismatch\ndescription: x\n---\n", encoding="utf-8")
    ext = scan.PluginSource(skills_root=pdir / "skills", origin="mkt/ext",
                            controlled=False, source="")
    report = _run_with_sources(repo, [ext])
    assert not any(f.check == "name-folder-match" for f in report.findings)


def test_purely_local_collision_has_no_external_annotation(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".github" / "skills").mkdir(parents=True)
    _skill(repo / ".github" / "skills", "a", triggers=["dup"])
    _skill(repo / ".github" / "skills", "b", triggers=["dup"])
    report = _run_with_sources(repo, [])
    coll = [f for f in report.findings if f.check == "trigger-collision"]
    assert len(coll) == 1
    assert "OUTSIDE this repo's control" not in coll[0].message
