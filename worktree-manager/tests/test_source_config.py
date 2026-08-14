"""Tests for the user-level source override (repo + ref/branch) and its command.

The self-updater's source is a config file (`<root>/config.toml` `[source]`),
managed with `worktree-manager source` — **not** an env var. These cover the
resolution precedence (config → default), set/reset semantics, atomic/round-trip
persistence, and the CLI surface.
"""

from __future__ import annotations

from pathlib import Path

from worktree_manager import source_config as sc
from worktree_manager.__main__ import main


def test_defaults_when_unset(tmp_path: Path):
    root = tmp_path / "root"
    assert sc.configured_source(root) == (None, None)
    assert sc.resolved_repo(root) == sc.DEFAULT_REPO
    assert sc.resolved_ref(root) == sc.DEFAULT_REF
    assert not sc.config_path(root).exists()


def test_set_repo_and_ref_round_trips(tmp_path: Path):
    root = tmp_path / "root"
    sc.set_source(repo="https://example.invalid/fork.git", ref="canary", root=root)
    assert sc.configured_source(root) == ("https://example.invalid/fork.git", "canary")
    assert sc.resolved_repo(root) == "https://example.invalid/fork.git"
    assert sc.resolved_ref(root) == "canary"
    # Human-editable TOML under a [source] table.
    body = sc.config_path(root).read_text("utf-8")
    assert "[source]" in body
    assert 'repo = "https://example.invalid/fork.git"' in body
    assert 'ref = "canary"' in body


def test_set_ref_only_preserves_repo(tmp_path: Path):
    root = tmp_path / "root"
    sc.set_source(repo="https://example.invalid/fork.git", root=root)
    sc.set_source(ref="canary", root=root)  # only ref provided
    assert sc.configured_source(root) == ("https://example.invalid/fork.git", "canary")


def test_reset_all_removes_file(tmp_path: Path):
    root = tmp_path / "root"
    sc.set_source(repo="https://example.invalid/fork.git", ref="canary", root=root)
    sc.reset_source(root=root)
    assert sc.configured_source(root) == (None, None)
    assert not sc.config_path(root).exists()


def test_reset_one_field_keeps_other(tmp_path: Path):
    root = tmp_path / "root"
    sc.set_source(repo="https://example.invalid/fork.git", ref="canary", root=root)
    sc.reset_source(ref=True, root=root)  # clear ref only
    assert sc.configured_source(root) == ("https://example.invalid/fork.git", None)
    assert sc.resolved_ref(root) == sc.DEFAULT_REF


def test_malformed_config_falls_back_to_defaults(tmp_path: Path):
    root = tmp_path / "root"
    p = sc.config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("this is not toml = = =\n", "utf-8")
    assert sc.configured_source(root) == (None, None)
    assert sc.resolved_repo(root) == sc.DEFAULT_REPO


def test_non_table_source_is_tolerated(tmp_path: Path):
    root = tmp_path / "root"
    p = sc.config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('source = "oops-not-a-table"\n', "utf-8")  # valid TOML, wrong shape
    assert sc.configured_source(root) == (None, None)
    assert sc.resolved_repo(root) == sc.DEFAULT_REPO


def test_control_chars_round_trip(tmp_path: Path):
    root = tmp_path / "root"
    # A stray newline/tab must not corrupt the file; it round-trips via TOML escapes.
    sc.set_source(repo="a\nb\tc", ref="x\ry", root=root)
    assert sc.configured_source(root) == ("a\nb\tc", "x\ry")


# ── CLI: worktree-manager source ──────────────────────────────────────────────

def _isolate_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "root"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    return root


def test_cmd_source_show_defaults(tmp_path, monkeypatch, capsys):
    _isolate_root(monkeypatch, tmp_path)
    rc = main(["source"])
    out = capsys.readouterr().out
    assert rc == 0
    assert sc.DEFAULT_REPO in out
    assert "(default)" in out


def test_cmd_source_set_and_reset(tmp_path, monkeypatch, capsys):
    root = _isolate_root(monkeypatch, tmp_path)
    assert main(["source", "set", "--repo", "https://example.invalid/fork.git",
                 "--ref", "canary"]) == 0
    assert sc.configured_source(root) == ("https://example.invalid/fork.git", "canary")
    out = capsys.readouterr().out
    assert "canary" in out and "example.invalid" in out

    assert main(["source", "reset"]) == 0
    assert sc.configured_source(root) == (None, None)


def test_cmd_source_set_requires_a_field(tmp_path, monkeypatch, capsys):
    _isolate_root(monkeypatch, tmp_path)
    rc = main(["source", "set"])
    assert rc == 2
    assert "provide at least one" in capsys.readouterr().out


def test_cmd_source_set_flag_without_value_does_not_swallow_next_flag(
    tmp_path, monkeypatch, capsys
):
    root = _isolate_root(monkeypatch, tmp_path)
    # `--repo` has no value (followed by another flag); only --ref should be set.
    assert main(["source", "set", "--repo", "--ref", "canary"]) == 0
    assert sc.configured_source(root) == (None, "canary")
