"""Regression tests for the plugin version-bump guard (touch a plugin -> bump).

Drives the real ``tools/check-version-bump.py`` as a subprocess inside a
throwaway git repo (mirroring ``test_check_no_internal_identifiers.py``) so the
git-diff scoping, the plugin-dir rule, and the vendored-lib cross-consumer rule
are exercised end-to-end.

Run:  python -m pytest tools/test_check_version_bump.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-version-bump.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _plugin(repo: Path, name: str, version: str, *, vendors: list[str] | None = None) -> None:
    """Materialize a minimal plugin: plugin.json + pyproject + a src file, plus
    optional vendored lib copies under its libs/."""
    _write(repo, f"plugins/{name}/plugin.json",
           json.dumps({"name": name, "version": version}) + "\n")
    _write(repo, f"plugins/{name}/pyproject.toml",
           f'[project]\nname = "{name}"\nversion = "{version}"\n')
    _write(repo, f"plugins/{name}/src/{name.replace('-', '_')}/__init__.py", "x = 1\n")
    for lib in (vendors or []):
        _write(repo, f"plugins/{name}/libs/{lib}/src/{lib.replace('-', '_')}/__init__.py",
               "shared = 1\n")


def _set_plugin_version(repo: Path, name: str, version: str) -> None:
    _write(repo, f"plugins/{name}/plugin.json",
           json.dumps({"name": name, "version": version}) + "\n")


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo, capture_output=True, text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo with a simulated ``origin/main`` base carrying two plugins
    (alpha, beta) that both vendor a shared lib, plus a repo-root docs file."""
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    (r / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "checkout", "-q", "-b", "main")

    _plugin(r, "alpha", "1.0.0-dev1", vendors=["shared-lib"])
    _plugin(r, "beta", "2.0.0-dev1", vendors=["shared-lib"])
    _write(r, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _write(r, "docs/root-doc.md", "repo-root doc\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(r, "update-ref", "refs/remotes/origin/main", base_sha)
    return r


def test_plugin_src_change_without_bump_fails(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no bump")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr
    assert "beta" not in result.stderr  # untouched plugin is not charged


def test_plugin_change_with_bump_passes(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _set_plugin_version(repo, "alpha", "1.0.0-dev2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature + bump")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plugin_docs_change_needs_bump(repo: Path):
    # CONTRIBUTING: a plugin's OWN docs ship in its payload -> bump required.
    _write(repo, "plugins/beta/docs/guide.md", "new guide\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "beta docs, no bump")
    result = _run(repo)
    assert result.returncode == 1
    assert "beta" in result.stderr


def test_repo_root_change_needs_no_bump(repo: Path):
    # Repo-root docs/tools are not vendored into any plugin -> no bump.
    _write(repo, "docs/root-doc.md", "edited repo-root doc\n")
    _write(repo, "tools/helper.py", "print('hi')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "repo-root only")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_shared_lib_change_charges_all_consumers(repo: Path):
    # A top-level shared lib change must bump EVERY plugin that vendors it.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change, no bumps")
    result = _run(repo)
    assert result.returncode == 1
    assert "alpha" in result.stderr and "beta" in result.stderr


def test_shared_lib_change_passes_when_all_consumers_bump(repo: Path):
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _set_plugin_version(repo, "alpha", "1.0.0-dev2")
    _set_plugin_version(repo, "beta", "2.0.0-dev2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib + both bumps")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_vendored_copy_change_charges_only_its_plugin(repo: Path):
    # Editing a plugin's OWN vendored copy charges that plugin (the plugin-dir
    # rule); the sync guard separately forces the sibling copies to match.
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "shared = 9\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha vendored copy, no bump")
    result = _run(repo)
    assert result.returncode == 1
    assert "alpha" in result.stderr


def test_build_artifacts_are_ignored(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/__pycache__/mod.cpython-312.pyc", "bytecode\n")
    _write(repo, "plugins/alpha/build/out.txt", "artifact\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "only build artifacts")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_gitignore_and_test_venvs_are_ignored(repo: Path):
    # A per-plugin .gitignore and throwaway test-venv artifacts are dev-hygiene,
    # not runtime payload, so touching them must not demand a version bump.
    _write(repo, "plugins/alpha/.gitignore", ".venv-test/\n__pycache__/\n")
    _write(repo, "plugins/alpha/.venv-test/Lib/site-packages/x/_c.pyd", "binary\n")
    _write(repo, "plugins/beta/.gitignore", ".venv-test/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add per-plugin .gitignore + a test-venv artifact")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_plugin_is_not_charged(repo: Path):
    # A brand-new plugin has no base version to bump from -> skipped.
    _plugin(repo, "gamma", "0.1.0-dev1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add gamma")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
