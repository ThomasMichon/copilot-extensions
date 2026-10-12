"""Objective primary-path navigation contracts, independent of the live tree."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "docs_consistency", Path(__file__).with_name("check-docs-consistency.py"))
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_local_paths_and_anchors(tmp_path):
    (tmp_path / "README.md").write_text(
        "[guide](guide.md#current-use)\n![asset](image.svg)\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Current Use\n", encoding="utf-8")
    (tmp_path / "image.svg").write_text("<svg/>", encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_missing_page(tmp_path):
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_missing_path_and_anchor(tmp_path):
    (tmp_path / "README.md").write_text(
        "[missing](absent.md)\n[stale](guide.md#old)\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Current\n", encoding="utf-8")
    problems = mod.check_adopter_navigation(tmp_path, ("README.md",), {})
    assert len(problems) == 2
    assert "absent.md" in problems[0]
    assert "guide.md#old" in problems[1]


def test_missing_reading_edge(tmp_path):
    (tmp_path / "README.md").write_text("# Front door\n", encoding="utf-8")
    assert mod.check_adopter_navigation(
        tmp_path, ("README.md",), {"README.md": ("docs/README.md",)})


def test_exact_case_even_on_windows(tmp_path):
    (tmp_path / "README.md").write_text("[guide](GUIDE.md)\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_fences_comments_and_external_links_are_not_local_navigation(tmp_path):
    (tmp_path / "README.md").write_text(
        '```md\n[example](missing.md)\n```\n'
        '<!-- [comment](missing.md) -->\n'
        '`[faux](missing.md)`\n'
        '[remote](https://example.invalid/a)\n', encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_duplicate_headings_and_explicit_anchors():
    assert mod.heading_anchors(
        '# Use\n# Use\n# Use-1\n<a id="legacy"></a>\n'
        '# Deploying: one command — `<repo> update`\n'
    ) == {"use", "use-1", "use-1-1", "legacy", "deploying-one-command--repo-update"}


def test_reference_links_html_and_encoded_paths(tmp_path):
    (tmp_path / "README.md").write_text(
        '[guide][owner]\n[owner]: guide%20one.md#ready\n'
        '<a href="guide%20one.md#ready">owner</a>\n'
        '<img src="image.svg">\n', encoding="utf-8")
    (tmp_path / "guide one.md").write_text("# Ready\n", encoding="utf-8")
    (tmp_path / "image.svg").write_text("<svg/>", encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_outside_repo_is_rejected_before_read(tmp_path):
    (tmp_path / "README.md").write_text('[outside](../outside.md)\n', encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})
