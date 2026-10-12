"""Objective primary-path navigation contracts, independent of the live tree."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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


def test_shortcut_reference_checks_its_actual_destination(tmp_path):
    (tmp_path / "README.md").write_text(
        "[Guide]\n\n[Guide]: missing.md\n", encoding="utf-8")
    problems = mod.check_adopter_navigation(tmp_path, ("README.md",), {})
    assert len(problems) == 1
    assert "missing.md" in problems[0]


def test_unused_definitions_and_full_inline_labels_are_not_shortcuts(tmp_path):
    (tmp_path / "README.md").write_text(
        "[Label][owner]\n[Inline](guide.md)\n"
        "[Label]: missing.md\n[Inline]: missing.md\n"
        "[unused]: missing.md\n[owner]: guide.md\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_collapsed_and_shortcut_reference_labels_share_normalization(tmp_path):
    text = "[My  Guide][]\n[my guide]\n[My Guide]: guide.md#ready\n"
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Ready\n", encoding="utf-8")
    assert mod.local_links(text) == ["guide.md#ready", "guide.md#ready"]
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_four_space_literals_do_not_open_a_fence(tmp_path):
    (tmp_path / "README.md").write_text(
        "    ```\n\n[broken](missing.md)\n\n    ```\n", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_fence_info_cannot_close_an_open_fence(tmp_path):
    (tmp_path / "README.md").write_text(
        "```md\n``` not a closer\n[example](missing.md)\n```\n", encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_images_do_not_satisfy_reading_edges(tmp_path):
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    for text in (
        "![preview](guide.md)", '<img src="guide.md">',
        "![preview][owner]\n[owner]: guide.md",
        "![preview][]\n[preview]: guide.md",
        "![preview]\n[preview]: guide.md",
    ):
        (tmp_path / "README.md").write_text(text, encoding="utf-8")
        assert mod.check_adopter_navigation(
            tmp_path, ("README.md",), {"README.md": ("guide.md",)})


def test_resolved_escape_is_rejected_without_reading(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    escape = repo / "escape.md"
    escape.write_text("# Placeholder\n", encoding="utf-8")
    (repo / "README.md").write_text("[outside](escape.md#outside)\n", encoding="utf-8")
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == escape:
            return tmp_path / "outside.md"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert mod.check_adopter_navigation(repo, ("README.md",), {})


def test_real_symlink_and_directory_readme_escape(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    guide = repo / "guide"
    guide.mkdir()
    try:
        (repo / "escape.md").symlink_to(outside)
        (guide / "README.md").symlink_to(outside)
    except OSError:
        pytest.skip("This platform/account cannot create a symlink")
    original = Path.read_text

    def read(path, *args, **kwargs):
        assert path.resolve() != outside, "External Markdown must not be read"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    for link in ("escape.md#outside", "guide/#outside"):
        (repo / "README.md").write_text(f"[outside]({link})\n", encoding="utf-8")
        assert mod.check_adopter_navigation(repo, ("README.md",), {})
