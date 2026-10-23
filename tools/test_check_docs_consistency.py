"""Objective primary-path navigation contracts, independent of the live tree."""
from __future__ import annotations

import importlib.util
import os
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
        '# Use\n# Use\n# Use-1\n<a id="legacy"></a>\n\n'
        '# Deploying: one command — `<repo> update`\n'
    ) == {"use", "use-1", "use-1-1", "legacy", "deploying-one-command--repo-update"}


def test_reference_links_html_and_encoded_paths(tmp_path):
    (tmp_path / "README.md").write_text(
        '[guide][owner]\n\n[owner]: guide%20one.md#ready\n\n'
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
        "[Label][owner]\n[Inline](guide.md)\n\n"
        "[Label]: missing.md\n[Inline]: missing.md\n"
        "[unused]: missing.md\n[owner]: guide.md\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_collapsed_and_shortcut_reference_labels_share_normalization(tmp_path):
    text = "[My  Guide][]\n[my guide]\n\n[My Guide]: guide.md#ready\n"
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
        "![preview][owner]\n\n[owner]: guide.md",
        "![preview][]\n\n[preview]: guide.md",
        "![preview]\n\n[preview]: guide.md",
    ):
        (tmp_path / "README.md").write_text(text, encoding="utf-8")
        assert mod.local_links(text) == ["guide.md"]
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


def test_commented_fences_do_not_hide_rendered_links(tmp_path):
    (tmp_path / "README.md").write_text(
        "<!--\n```\n-->\n[broken](missing.md)\n", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_comment_openers_inside_real_fences_remain_literal(tmp_path):
    (tmp_path / "README.md").write_text(
        "```md\n<!--\n```\n[broken](missing.md)\n", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_inline_code_comment_openers_do_not_hide_navigation(tmp_path):
    (tmp_path / "README.md").write_text(
        "`<!--` [broken](missing.md)\n", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_angle_destinations_preserve_spaces_inline_and_reference(tmp_path):
    (tmp_path / "README.md").write_text(
        '[guide](<guide one.md#ready> "Title")\n'
        "[guide][owner]\n[shortcut]\n\n"
        '[owner]: <guide one.md#ready> "Owner"\n'
        "[shortcut]: <guide one.md#ready>\n", encoding="utf-8")
    (tmp_path / "guide one.md").write_text("# Ready\n", encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_first_normalized_reference_definition_wins(tmp_path):
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    for first, second, expected_problem in (
        ("[Guide]: missing.md", "[Guide]: guide.md", True),
        ("[My Guide]: missing.md", "[MY  GUIDE]: guide.md", True),
        ("[My Guide]: guide.md", "[my guide]: missing.md", False),
    ):
        label = "Guide" if first.startswith("[Guide]") else "My Guide"
        text = f"[{label}]\n\n{first}\n{second}\n"
        (tmp_path / "README.md").write_text(text, encoding="utf-8")
        assert bool(mod.check_adopter_navigation(tmp_path, ("README.md",), {})) == expected_problem


def test_multiline_code_span_comments_cannot_hide_rendered_navigation(tmp_path):
    (tmp_path / "README.md").write_text(
        "`<!--\n` [broken](missing.md)\n", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_all_sequential_waypoints_are_required(tmp_path):
    sequence = {
        "docs/installation.md": "docs/capabilities.md",
        "docs/capabilities.md": "docs/adoption.md",
        "docs/adoption.md": "docs/configuration.md",
        "docs/configuration.md": "docs/architecture.md",
        "docs/architecture.md": "docs/harness-runbook.md",
    }
    (tmp_path / "docs").mkdir()
    for source, target in sequence.items():
        assert target in mod.READING_EDGES[source]
        (tmp_path / target).write_text("# Next\n", encoding="utf-8")
        page = tmp_path / source
        page.write_text(f"[next]({Path(target).name})\n", encoding="utf-8")
        assert not mod.check_adopter_navigation(
            tmp_path, (source,), {source: (target,)})
        page.write_text("# No next link\n", encoding="utf-8")
        assert mod.check_adopter_navigation(
            tmp_path, (source,), {source: (target,)})


def test_html_attributes_use_real_names_spacing_and_first_value(tmp_path):
    text = (
        '<a data-href="missing.md" href = "guide.md" href="missing.md">guide</a>\n'
        '<img data-src="missing.svg" src = "image.svg">\n'
        '<a data-id="fake" id = "actual"></a>\n'
    )
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "image.svg").write_text("<svg/>", encoding="utf-8")
    assert mod.local_links(text) == ["guide.md", "image.svg"]
    assert "actual" in mod.heading_anchors(text)
    assert "fake" not in mod.heading_anchors(text)
    assert not mod.check_adopter_navigation(
        tmp_path, ("README.md",), {"README.md": ("guide.md",)})


def test_html_data_attributes_are_not_edges_and_spaced_real_targets_are_checked(tmp_path):
    page = tmp_path / "README.md"
    page.write_text('<a data-href="guide.md">not a link</a>', encoding="utf-8")
    assert not mod.check_adopter_navigation(tmp_path, ("README.md",), {})
    assert mod.check_adopter_navigation(
        tmp_path, ("README.md",), {"README.md": ("guide.md",)})
    page.write_text(
        '<a href = "missing.md">broken</a><img src = "missing.svg">',
        encoding="utf-8")
    assert len(mod.check_adopter_navigation(tmp_path, ("README.md",), {})) == 2


def test_primary_page_directory_is_reported_without_reading(tmp_path):
    (tmp_path / "README.md").mkdir()
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_directory_readme_anchor_target_is_not_read(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "README.md").mkdir()
    (tmp_path / "README.md").write_text("[guide](guide/#ready)", encoding="utf-8")
    assert mod.check_adopter_navigation(tmp_path, ("README.md",), {})


def test_legacy_readers_also_report_markdown_directories(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    readme.mkdir()
    plugins = tmp_path / "plugins"
    (plugins / "example" / "README.md").mkdir(parents=True)
    monkeypatch.setattr(mod, "README", readme)
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "PLUGINS_DIR", plugins)
    assert mod.check_readme_completeness([])
    assert mod.check_plugin_skill_lists(["example"])
    assert mod.check_counts({"total": 0, "runtime": 0, "payload": 0, "cc_skills": 0})


COUNT_PHRASES = [
    ("{n} plugins, one marketplace", "total"),
    ("How the {n} copilot-extensions plugins", "total"),
    ("all {n} copilot-extensions plugins", "total"),
    ("{n}-plugin suite", "total"),
    ("{n}-plugin marketplace", "total"),
    ("{n} ship a runtime", "runtime"),
    ("{n} runtime plugins", "runtime"),
    ("{n} are payload-only", "payload"),
    ("{n} is payload-only", "payload"),
    ("bundles {n} focused skills", "cc_skills"),
]


@pytest.mark.parametrize("phrase,kind", COUNT_PHRASES)
@pytest.mark.parametrize("token", ["two", "2", "unknown"])
@pytest.mark.parametrize("style", ["plain", "emphasis", "unicode-case"])
def test_count_prefilters_preserve_grammar(tmp_path, monkeypatch, phrase, kind, token, style):
    text = phrase.format(n=token)
    if style == "emphasis":
        text = text.replace(token, f"**`{token}`**").replace(" ", "\t\n")
    elif style == "unicode-case":
        text = text.upper().replace("I", "\u0131").replace("S", "\u017f")
    (tmp_path / "README.md").write_text("intro\n" + text, encoding="utf-8")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    problems = mod.check_counts(dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1))
    assert len(problems) == (0 if token == "unknown" else 1)
    if problems:
        assert f"README.md:2: says {token.upper() if style == 'unicode-case' else token!r}" in problems[0]
        assert f"for {kind} (expected 1/one)" in problems[0]


def test_counts_keep_file_pattern_match_order_and_line_numbers(tmp_path, monkeypatch):
    text = (
        "two runtime plugins\n"
        "three plugins, one marketplace\n"
        "four runtime plugins\n"
        "five plugins, one marketplace\n"
    )
    for name in ("b.md", "a.md"):
        (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    problems = mod.check_counts(dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1))
    assert problems == [
        f"  {name}:{line}: says {token!r} for {kind} (expected 1/one): \"{phrase}\""
        for name in ("a.md", "b.md")
        for line, token, kind, phrase in (
            (2, "three", "total", "three plugins, one marketplace"),
            (4, "five", "total", "five plugins, one marketplace"),
            (1, "two", "runtime", "two runtime plugins"),
            (3, "four", "runtime", "four runtime plugins"),
        )
    ]


def test_count_prefilters_skip_only_impossible_pattern_scans(tmp_path, monkeypatch):
    class UnexpectedScan:
        def finditer(self, text):
            pytest.fail("The absent required suffix must prevent the full pattern scan")

    (tmp_path / "README.md").write_text("unrelated words " * 100, encoding="utf-8")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "COUNT_PATTERNS", [
        (UnexpectedScan(), kind, suffix) for _, kind, suffix in mod.COUNT_PATTERNS
    ])
    assert mod.check_counts({}) == []


def test_count_prefilters_do_not_hide_markdown_read_errors(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_bytes(b"\xff")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    with pytest.raises(UnicodeDecodeError):
        mod.check_counts({})


def _legacy_paths(repo: Path) -> list[Path]:
    return [
        path for path in sorted(repo.glob("**/*.md"))
        if ".worktrees" not in path.parts and "node_modules" not in path.parts
    ]


def _legacy_counts(repo: Path, expected: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for md in _legacy_paths(repo):
        if not md.is_file():
            problems.append(f"  {md.relative_to(repo)}: expected a Markdown file")
            continue
        raw = md.read_text(encoding="utf-8")
        text = raw.replace("*", "").replace("`", "")
        rel = md.relative_to(repo)
        for rx, kind, _suffix in mod.COUNT_PATTERNS:
            for match in rx.finditer(text):
                got = mod._num(match.group(1))
                want = expected[kind]
                if got is not None and got != want:
                    line = text[:match.start()].count("\n") + 1
                    problems.append(
                        f"  {rel}:{line}: says {match.group(1)!r} for {kind} "
                        f"(expected {want}/{mod.NUM2WORD.get(want, want)}): "
                        f'"{match.group(0).strip()}"'
                    )
    return problems


def _write(repo: Path, relative: str, text: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_whole_tree_candidates_and_diagnostics_equal_legacy(tmp_path, monkeypatch):
    text = (
        "header\n"
        "**two** plugins, one marketplace; `three` runtime plugins\n"
        "How the FOUR copilot-extensions plugins fit together\n"
        "all five copilot-extensions plugins; six-plugin suite\n"
        "seven ship a runtime; eight are payload-only\n"
        "bundles nine focused skills; unknown runtime plugins\n"
        "41-plugin marketplace; 40 runtime plugins; one is payload-only\n"
        "one plugins, one marketplace; bundles one focused skills\n"
    )
    relatives = [
        "z.md", "a.md", "README.MD", ".hidden/nested.md",
        "plugins/example/skills/example/SKILL.md", "docs/deep/counts.md",
        ".test-venvs/example/README.md", ".venv/pkg/README.md",
        ".git/example.md", ".worktrees-copy/README.md",
        "node_modules-copy/README.md", "NODE_MODULES/README.md",
        "nested/.worktrees/deep/ignored.md",
        "nested/node_modules/deep/ignored.md",
        ".worktrees/ignored.md", "node_modules/ignored.md",
    ]
    for relative in relatives:
        _write(tmp_path, relative, text)
    _write(tmp_path, "docs/not-markdown.txt", text)
    monkeypatch.setattr(mod, "REPO", tmp_path)
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    legacy_paths = _legacy_paths(tmp_path)
    legacy_problems = _legacy_counts(tmp_path, expected)

    assert sorted(mod._markdown_paths()) == legacy_paths
    assert mod.check_counts(expected) == legacy_problems
    assert len(legacy_problems) == len(legacy_paths) * 10


def test_excluded_subtrees_are_not_enumerated(tmp_path, monkeypatch):
    for relative in (
        ".worktrees/deep/ignored.md", "node_modules/deep/ignored.md",
        "docs/.worktrees/deep/ignored.md", "docs/node_modules/deep/ignored.md",
        ".test-venvs/pkg/README.md", ".hidden/deep/README.md",
        "docs/deep/README.md", "node_modules-copy/deep/README.md",
    ):
        _write(tmp_path, relative, "two plugins, one marketplace")
    legacy_paths = _legacy_paths(tmp_path)
    real_scandir = os.scandir
    visited: set[Path] = set()

    def checked_scandir(path):
        directory = Path(path)
        assert not {".worktrees", "node_modules"}.intersection(directory.parts)
        visited.add(directory)
        return real_scandir(path)

    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(os, "scandir", checked_scandir)

    assert sorted(mod._markdown_paths()) == legacy_paths
    assert tmp_path / ".test-venvs" / "pkg" in visited
    assert tmp_path / ".hidden" / "deep" in visited
    assert tmp_path / "docs" / "deep" in visited
    assert tmp_path / "node_modules-copy" / "deep" in visited


def test_unreadable_markdown_subtree_fails_closed(tmp_path, monkeypatch):
    blocked = tmp_path / "docs"
    blocked.mkdir()
    real_scandir = os.scandir

    def denied_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("cannot enumerate Markdown subtree")
        return real_scandir(path)

    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(os, "scandir", denied_scandir)
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    with pytest.raises(PermissionError, match="cannot enumerate Markdown subtree"):
        mod.check_counts(expected)


@pytest.mark.parametrize("excluded", [".worktrees", "node_modules"])
def test_excluded_ancestor_still_excludes_entire_tree(tmp_path, monkeypatch, excluded):
    repo = tmp_path / excluded / "repo"
    _write(repo, "README.md", "two plugins, one marketplace")
    assert _legacy_paths(repo) == []
    monkeypatch.setattr(mod, "REPO", repo)

    def unexpected_scandir(path):
        pytest.fail(f"excluded root was enumerated: {path}")

    monkeypatch.setattr(os, "scandir", unexpected_scandir)
    assert list(mod._markdown_paths()) == []


@pytest.mark.parametrize("invalid", ["directory", "encoding", "read"])
def test_markdown_read_errors_match_legacy(tmp_path, monkeypatch, invalid):
    path = tmp_path / "a.md"
    if invalid == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    _write(tmp_path, "z.md", "two plugins, one marketplace")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    assert sorted(mod._markdown_paths()) == _legacy_paths(tmp_path)

    if invalid == "read":
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == tmp_path / "z.md":
                raise PermissionError("cannot read Markdown file")
            return original(path, *args, **kwargs)

        path.write_text("two plugins, one marketplace", encoding="utf-8")
        monkeypatch.setattr(Path, "read_text", read)

    if invalid == "directory":
        assert mod.check_counts(expected) == _legacy_counts(tmp_path, expected)
        assert "a.md: expected a Markdown file" in mod.check_counts(expected)[0]
        return
    with pytest.raises((OSError, UnicodeError)) as legacy:
        _legacy_counts(tmp_path, expected)
    with pytest.raises(type(legacy.value)) as current:
        mod.check_counts(expected)
    assert str(current.value) == str(legacy.value)


def test_symlink_candidates_equal_legacy_without_following_directories(tmp_path, monkeypatch):
    target = _write(tmp_path, "target/nested.md", "two plugins, one marketplace")
    try:
        (tmp_path / "linked").symlink_to(target.parent, target_is_directory=True)
        (tmp_path / "linked.md").symlink_to(target)
        (tmp_path / "missing.md").symlink_to(tmp_path / "missing")
        (tmp_path / "directory.md").symlink_to(target.parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    monkeypatch.setattr(mod, "REPO", tmp_path)

    paths = sorted(mod._markdown_paths())
    assert paths == _legacy_paths(tmp_path)
    assert tmp_path / "linked" / "nested.md" not in paths
    assert tmp_path / "linked.md" in paths
    assert tmp_path / "missing.md" in paths
    assert tmp_path / "directory.md" in paths
    expected = dict.fromkeys(("total", "runtime", "payload", "cc_skills"), 1)
    assert mod.check_counts(expected) == _legacy_counts(tmp_path, expected)
