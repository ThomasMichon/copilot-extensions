"""Regression tests for the changefile-presence guard (the changefile-based
replacement for check-version-bump.py's manual bump requirement).

Drives the real ``tools/check-changefile-presence.py`` as a subprocess inside
a throwaway git repo (mirroring ``test_check_version_bump.py``), copying its
real dependencies (``check-version-bump.py`` for plugin-diff detection plus
ITS OWN two imports, and ``changefile.py`` for reading pending changefiles)
alongside it.

Run:  python -m pytest tools/test_check_changefile_presence.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent
SCRIPT = TOOLS_DIR / "check-changefile-presence.py"
# check-version-bump.py itself imports uv_editable_ref and installer_engine_ref
# -- omitting either produced a silent ModuleNotFoundError that failed EVERY
# test in this file (and was never caught, because this suite isn't wired
# into CI at all; see the ci.yml note added alongside this fix).
DEP_SCRIPTS = ["check-version-bump.py", "changefile.py", "uv_editable_ref.py",
               "installer_engine_ref.py"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _plugin(repo: Path, name: str, version: str = "1.0.0") -> None:
    _write(repo, f"plugins/{name}/plugin.json",
           json.dumps({"name": name, "version": version}) + "\n")
    _write(repo, f"plugins/{name}/src/{name.replace('-', '_')}/__init__.py", "x = 1\n")


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo, capture_output=True, text=True, check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    (r / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    for dep in DEP_SCRIPTS:
        (r / "tools" / dep).write_bytes((TOOLS_DIR / dep).read_bytes())

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "checkout", "-q", "-b", "dev")

    _plugin(r, "alpha")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(r, "update-ref", "refs/remotes/origin/main", base_sha)
    # Also simulate `origin/dev` at the same point -- this tool's own CLI
    # default base is `origin/dev` (this repo's real contribution trunk),
    # so a bare `_run(repo)` with no explicit `--base` needs this ref to
    # resolve. A SYMBOLIC ref (not a plain `update-ref`) means `origin/dev`
    # always tracks wherever `origin/main` currently points, in case a
    # future test advances it mid-run.
    _git(r, "symbolic-ref", "refs/remotes/origin/dev", "refs/remotes/origin/main")
    return r


def test_no_changes_passes(repo: Path):
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_touched_plugin_without_changefile_fails(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no changefile")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr
    assert "no pending changefile" in result.stderr


def test_base_sharing_no_merge_base_degrades_to_soft_skip(repo: Path):
    """A `base` that RESOLVES but shares no common ancestor with HEAD at
    all (the confirmed fallout of a deliberate `main` history rewrite --
    see docs/pipelines.md's "If main's history is force-rewritten") must
    degrade to a soft no-op (no plugin misreported as touched), never
    silently diff raw `base` directly and misreport every plugin that
    merely differs between `base`'s snapshot and HEAD as missing a
    changefile."""
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no changefile")

    _git(repo, "checkout", "-q", "--orphan", "rewritten-main")
    _write(repo, "unrelated.txt", "rewritten history\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated root (simulates a rewritten main)")
    rewritten_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", rewritten_sha)
    _git(repo, "checkout", "-q", "dev")

    result = _run(repo, "--base", "origin/main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "alpha" not in result.stdout + result.stderr
    assert "shares no common history" in result.stdout + result.stderr


def test_touched_plugin_with_changefile_passes(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/fix.json",
           json.dumps({"comment": "fix", "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature + changefile")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_changefile_for_a_different_plugin_does_not_satisfy(repo: Path):
    _plugin(repo, "beta")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add beta")
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/fix.json",
           json.dumps({"comment": "fix", "changes": [{"plugin": "beta", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, changefile names beta instead")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr


def test_unrelated_pending_changefile_does_not_satisfy(repo: Path):
    """The regression this test exists for (PR #4942 review): `dev` is a
    rolling pre-release branch, so some OTHER, already-merged-but-not-yet-
    promoted PR routinely leaves its own still-pending changefile for a
    plugin THIS PR also happens to touch. That coincidence must not count --
    only a changefile THIS diff itself adds may satisfy the requirement, or
    a later promotion that consumes the unrelated changefile first would
    ship this PR's content with no bump at all."""
    _write(repo, ".changefiles/unrelated-already-pending.json",
           json.dumps({"comment": "unrelated", "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated PR already merged, still pending promotion")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no changefile of its own")
    result = _run(repo)
    assert result.returncode == 1, (
        "alpha must still be charged even though an unrelated, already-"
        "pending changefile from a different PR already names it: "
        + result.stdout + result.stderr
    )
    assert "alpha" in result.stderr


def test_nested_added_changefile_does_not_satisfy_via_basename_collision(repo: Path):
    """The regression this test exists for (PR #4954 review): a changefile
    added at a NESTED path (e.g. ``.changefiles/archive/pending.json``) must
    not satisfy the requirement by basename alone just because an existing,
    unrelated TOP-LEVEL file of the same name (``.changefiles/pending.json``)
    already happens to name the SAME plugin this diff touches --
    ``changefile.read_changefiles()`` only ever globs ``.changefiles/*.json``
    non-recursively, so the nested file this PR adds is never actually
    consumed at all. The existing top-level file must name `alpha` (not an
    unrelated plugin) for this to actually exercise the basename collision:
    naming a different plugin would make `alpha` still get flagged missing
    for an unrelated reason, passing even without the top-level-path
    restriction this test exists to prove (PR #4954 review)."""
    _write(repo, ".changefiles/pending.json",
           json.dumps({"comment": "unrelated top-level file, same basename as the nested one below",
                       "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated top-level pending.json, already merged")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/archive/pending.json",
           json.dumps({"comment": "archived, not consumable",
                       "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, changefile only added at a nested path")
    result = _run(repo)
    assert result.returncode == 1, (
        "alpha must still be charged -- the added changefile is nested, not "
        "top-level, so read_changefiles() never actually sees it: "
        + result.stdout + result.stderr
    )
    assert "alpha" in result.stderr


def test_untouched_plugin_is_not_charged(repo: Path):
    _plugin(repo, "beta")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add beta, untouched afterward")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/fix.json",
           json.dumps({"comment": "fix", "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature + changefile")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "beta" not in result.stdout + result.stderr
