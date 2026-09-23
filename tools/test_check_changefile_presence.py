"""Regression tests for the changefile-presence guard (the changefile-based
replacement for check-version-bump.py's manual bump requirement).

Drives the real ``tools/check-changefile-presence.py`` as a subprocess inside
a throwaway git repo (mirroring ``test_check_version_bump.py``), copying its
two real dependencies (``check-version-bump.py`` for plugin-diff detection,
``changefile.py`` for reading pending changefiles) alongside it.

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
DEP_SCRIPTS = ["check-version-bump.py", "changefile.py"]


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
