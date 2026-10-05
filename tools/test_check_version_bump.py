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
UV_EDITABLE_REF = Path(__file__).resolve().parent / "uv_editable_ref.py"
INSTALLER_ENGINE_REF = Path(__file__).resolve().parent / "installer_engine_ref.py"


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


def _plugin_with_editable_ref(repo: Path, name: str, version: str, lib: str) -> None:
    """Materialize a minimal plugin whose ``pyproject.toml`` references
    ``lib`` via a `uv`-editable canonical pointer (vendor-pointer-
    generalization effort, Phase 1) -- no local copy under its own
    ``libs/`` at all."""
    _write(repo, f"plugins/{name}/plugin.json",
           json.dumps({"name": name, "version": version}) + "\n")
    _write(
        repo, f"plugins/{name}/pyproject.toml",
        f'[project]\nname = "{name}"\nversion = "{version}"\n\n'
        f'[tool.uv.sources]\n'
        f'{lib} = {{ path = "../../libs/{lib}", editable = true }}\n',
    )
    _write(repo, f"plugins/{name}/src/{name.replace('-', '_')}/__init__.py", "x = 1\n")


def _out_of_plugin_consumer_with_editable_ref(
    repo: Path, name: str, version: str, lib: str
) -> None:
    """Materialize a minimal top-level, out-of-plugin consumer (mirroring
    ``worktree-manager``: no ``plugin.json`` at all -- its own release
    version lives directly in ``[project].version``) whose
    ``pyproject.toml`` references ``lib`` via a `uv`-editable canonical
    pointer. ``uv_editable_ref.iter_consumer_dirs()`` must discover it
    without any ``_EXTRA_CONSUMER_DIRS`` fixture wiring here -- it already
    hardcodes ``worktree-manager`` by that exact name."""
    assert name == "worktree-manager", "only the real extra-consumer name is discoverable"
    _write(
        repo, f"{name}/pyproject.toml",
        f'[project]\nname = "{name}"\nversion = "{version}"\n\n'
        f'[tool.uv.sources]\n'
        f'{lib} = {{ path = "../libs/{lib}", editable = true }}\n',
    )
    _write(repo, f"{name}/src/{name.replace('-', '_')}/__init__.py", "x = 1\n")


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
    (r / "tools" / UV_EDITABLE_REF.name).write_bytes(UV_EDITABLE_REF.read_bytes())
    (r / "tools" / INSTALLER_ENGINE_REF.name).write_bytes(INSTALLER_ENGINE_REF.read_bytes())

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


def test_base_sharing_no_merge_base_degrades_to_soft_skip(repo: Path):
    """A `base` that RESOLVES (to some commit) but shares no common
    ancestor with HEAD at all -- the confirmed, concrete fallout of a
    deliberate `main` history rewrite (docs/pipelines.md's "If main's
    history is force-rewritten") -- must degrade to the same soft
    "skipping" no-op as an unresolvable base, never silently diff raw
    `base` directly (which previously produced a misleading false-positive
    "alpha needs a bump" even though this branch never touched alpha)."""
    _git(repo, "checkout", "-q", "--orphan", "rewritten-main")
    _write(repo, "unrelated.txt", "rewritten history\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated root (simulates a rewritten main)")
    rewritten_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", rewritten_sha)
    _git(repo, "checkout", "-q", "main")

    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "alpha" not in result.stdout + result.stderr
    assert "shares no common history" in result.stdout + result.stderr


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


def test_installer_engine_change_charges_registered_adopters(repo: Path):
    """A canonical installer-engine change must charge every registered adopter
    even when the diff touches only `libs/installer-engine/*`."""
    # The registered adopter (installer_engine_ref.ADOPTERS) must actually
    # exist as a plugin in this diff's base, or check-version-bump.py's
    # `(PLUGINS_DIR / plugin).is_dir()` filter correctly treats it as
    # nonexistent and silently excludes it -- it does not get created by
    # the shared `repo` fixture, which only knows about alpha/beta.
    _plugin(repo, "agent-pull-requests", "1.0.0-dev1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add agent-pull-requests, the registered installer-engine adopter")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "libs/installer-engine/installer-engine.sh", "echo shared\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "installer engine change, no adopter bumps")
    result = _run(repo)
    assert result.returncode == 1
    assert "agent-pull-requests" in result.stderr


def test_unreadable_manifest_fails_closed_instead_of_dropping_consumer(repo: Path):
    """A plugin whose `pyproject.toml` EXISTS but is malformed must never be
    silently dropped from the consumer map (PR #4465 review): that would let
    a shared-lib change ship without charging a real consumer. The guard
    must refuse (nonzero exit) rather than continue past it."""
    _write(repo, "plugins/gamma/plugin.json",
           json.dumps({"name": "gamma", "version": "3.0.0-dev1"}) + "\n")
    _write(repo, "plugins/gamma/pyproject.toml",
           '[project]\nname = "gamma"\n\n[tool.uv]\nsources = "not-a-table"\n')
    _write(repo, "plugins/gamma/src/gamma/__init__.py", "x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add gamma with a malformed pyproject.toml")
    result = _run(repo, "--list")
    assert result.returncode != 0
    assert "gamma" in result.stderr


def test_shared_lib_change_charges_uv_editable_pointer_consumer_too(repo: Path):
    """A plugin with NO local copy at all -- only a `uv`-editable canonical
    pointer in its own `pyproject.toml` -- must still be charged for a
    shared-lib change (PR #4465 review): `materialize_main.py` promotes
    that same canonical payload into it at release time, so skipping it
    here would let its promoted payload change under an unbumped version."""
    _plugin_with_editable_ref(repo, "gamma", "3.0.0-dev1", "shared-lib")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add gamma (uv-editable shared-lib consumer)")
    # Move the simulated origin/main ref forward past gamma's own addition,
    # so the actual test diff below sees gamma as a PRE-EXISTING consumer
    # (a brand-new plugin is exempt from the bump obligation by design --
    # see test_new_plugin_is_not_charged -- which would otherwise mask the
    # very detection this test exists to prove).
    gamma_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", gamma_sha)

    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change, no bumps")
    result = _run(repo)
    assert result.returncode == 1
    assert "gamma" in result.stderr


def test_shared_lib_change_charges_out_of_plugin_editable_ref_consumer(repo: Path):
    """A top-level, out-of-plugin consumer (mirroring worktree-manager: no
    `plugin.json`, its own `[project].version`) referencing a shared lib
    only via a `uv`-editable pointer must still be charged for a shared-lib
    change (PR #4465 review): `_vendored_consumers()` previously only
    scanned `plugins/*`, so this class of consumer's version-gated,
    materialized payload could change with no bump obligation at all."""
    _out_of_plugin_consumer_with_editable_ref(repo, "worktree-manager", "9.0.0-dev1", "shared-lib")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add worktree-manager (uv-editable shared-lib consumer)")
    wtm_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", wtm_sha)

    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change, no bumps")
    result = _run(repo)
    assert result.returncode == 1
    assert "worktree-manager" in result.stderr


def test_shared_lib_change_charges_payload_only_plugin_with_real_copy(repo: Path):
    """A plugin with NO root `pyproject.toml` at all (mirroring
    `customizing-copilot`: payload-only, no `[tool.uv.sources]` possible)
    but a REAL vendored copy under its own `libs/` must still be charged
    for a shared-lib change (PR #4465 review): restricting the real-copy
    scan to `uv_editable_ref.iter_consumer_dirs()` (which requires a root
    `pyproject.toml`) would silently drop this class of consumer, exactly
    the regression `customizing-copilot` itself would have hit."""
    _write(repo, "plugins/gamma/plugin.json",
           json.dumps({"name": "gamma", "version": "3.0.0-dev1"}) + "\n")
    _write(repo, "plugins/gamma/libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add payload-only gamma with a real vendored copy")
    gamma_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", gamma_sha)

    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change, no bumps")
    result = _run(repo)
    assert result.returncode == 1
    assert "gamma" in result.stderr


def test_shared_lib_change_passes_when_out_of_plugin_consumer_bumps_its_own_version(
    repo: Path,
):
    """The out-of-plugin consumer's OWN `[project].version` (not a
    `plugin.json` it doesn't have) is what the guard must compare."""
    _out_of_plugin_consumer_with_editable_ref(repo, "worktree-manager", "9.0.0-dev1", "shared-lib")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add worktree-manager (uv-editable shared-lib consumer)")
    wtm_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", wtm_sha)

    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _set_plugin_version(repo, "alpha", "1.0.0-dev2")
    _set_plugin_version(repo, "beta", "2.0.0-dev2")
    _write(
        repo, "worktree-manager/pyproject.toml",
        '[project]\nname = "worktree-manager"\nversion = "9.0.0-dev2"\n\n'
        '[tool.uv.sources]\n'
        'shared-lib = { path = "../libs/shared-lib", editable = true }\n',
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change + worktree-manager bump")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_direct_change_to_standalone_consumer_needs_bump(repo: Path):
    """A direct payload change under a recognized standalone consumer's OWN
    top-level tree (mirroring `worktree-manager`, no shared-lib involved at
    all) must be detected the same way `plugins/<p>/` is -- previously
    `_plugins_needing_bump()` only recognized `plugins/*` and top-level
    `libs/*`, so `worktree-manager/src/worktree_manager/app.py` produced an
    empty `needing` map and both this guard and
    `check-changefile-presence.py`/`compute_from_diff()` (which share this
    map) missed the change entirely (PR #4514 review)."""
    _write(repo, "worktree-manager/pyproject.toml",
           '[project]\nname = "worktree-manager"\nversion = "9.0.0-dev1"\n')
    _write(repo, "worktree-manager/src/worktree_manager/__init__.py", "x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add worktree-manager")
    wtm_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", wtm_sha)

    _write(repo, "worktree-manager/src/worktree_manager/app.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "worktree-manager feature, no bump")
    result = _run(repo)
    assert result.returncode == 1
    assert "worktree-manager" in result.stderr


def test_shared_lib_change_charges_out_of_plugin_real_copy_consumer(repo: Path):
    """A top-level, out-of-plugin consumer (mirroring `worktree-manager`)
    with a REAL vendored copy under its own top-level `libs/` (not a
    `uv`-editable pointer) must still be charged for a shared-lib change
    (PR #4465/#4514 review): the real-copy scan was restricted to
    `plugins/*` directly, so `worktree-manager/libs/<lib>` was invisible to
    it even though `worktree-manager` itself remains a real consumer for
    libs it hasn't converted (e.g. `zdd`, per PR #4465's own effort
    journal)."""
    _write(repo, "worktree-manager/pyproject.toml",
           '[project]\nname = "worktree-manager"\nversion = "9.0.0-dev1"\n')
    _write(repo, "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add worktree-manager with a real vendored copy")
    wtm_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", wtm_sha)

    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change, no bumps")
    result = _run(repo)
    assert result.returncode == 1
    assert "worktree-manager" in result.stderr


def test_violation_message_for_standalone_consumer_omits_impossible_fix(repo: Path):
    """A standalone, out-of-plugin consumer (no `plugin.json`, no
    marketplace entry) must get bump guidance it can actually follow --
    the diagnostic previously always prescribed "plugin.json + pyproject.toml
    + marketplace.json" universally, which is impossible for a consumer with
    neither of the first two surfaces (PR #4514 review)."""
    _write(repo, "worktree-manager/pyproject.toml",
           '[project]\nname = "worktree-manager"\nversion = "9.0.0-dev1"\n')
    _write(repo, "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add worktree-manager with a real vendored copy")
    wtm_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", wtm_sha)

    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "shared lib change, no bumps")
    result = _run(repo)
    assert result.returncode == 1
    wtm_lines = [ln for ln in result.stderr.splitlines() if "worktree-manager:" in ln]
    assert len(wtm_lines) == 1, result.stderr
    assert "plugin.json" not in wtm_lines[0]
    assert "marketplace.json" not in wtm_lines[0]
    assert "changefile" in wtm_lines[0]
    assert "pyproject.toml" in wtm_lines[0]


def test_violation_message_for_payload_only_plugin_omits_nonexistent_pyproject(repo: Path):
    """A payload-only plugin (e.g. copilot-extensions-harness: no root
    `pyproject.toml` at all) must not be told to bump a file it doesn't
    have -- the direct-fix guidance must name only plugin.json +
    marketplace.json for this class of plugin."""
    _write(repo, "plugins/gamma/plugin.json",
           json.dumps({"name": "gamma", "version": "3.0.0-dev1"}) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add payload-only gamma, no pyproject.toml")
    gamma_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", gamma_sha)

    _write(repo, "plugins/gamma/skills/example/SKILL.md", "# Example\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gamma content change, no bump")
    result = _run(repo)
    assert result.returncode == 1
    gamma_lines = [ln for ln in result.stderr.splitlines() if "gamma:" in ln]
    assert len(gamma_lines) == 1, result.stderr
    assert "changefile" in gamma_lines[0]
    assert "pyproject.toml" not in gamma_lines[0]
    assert "plugin.json" in gamma_lines[0]
    assert "marketplace.json" in gamma_lines[0]


def test_symlinked_pyproject_fails_closed_instead_of_dropping_consumer(repo: Path):
    """A plugin whose `pyproject.toml` is a SYMLINK must never be silently
    dropped from the consumer map either (PR #4465 review):
    `find_uv_editable_refs()` deliberately returns `[]` for a symlinked
    manifest (its own explicit refusal), so a caller must reject that case
    itself rather than treating the empty result as "no pointers here"."""
    _write(repo, "plugins/gamma/plugin.json",
           json.dumps({"name": "gamma", "version": "3.0.0-dev1"}) + "\n")
    _write(repo, "plugins/gamma/src/gamma/__init__.py", "x = 1\n")
    real = repo / "plugins" / "gamma" / "real-pyproject.toml"
    real.write_text(
        '[project]\nname = "gamma"\nversion = "3.0.0-dev1"\n\n'
        '[tool.uv.sources]\n'
        'shared-lib = { path = "../../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    (repo / "plugins" / "gamma" / "pyproject.toml").symlink_to(real)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add gamma with a symlinked pyproject.toml")
    result = _run(repo, "--list")
    assert result.returncode != 0
    assert "gamma" in result.stderr
    assert "symlink" in result.stderr.lower()


def test_dangling_symlinked_pyproject_fails_closed_instead_of_being_filtered_out(repo: Path):
    """A symlinked `pyproject.toml` whose target does NOT exist (or resolves
    to a directory) must still be rejected -- `iter_consumer_dirs()` filters
    candidates by `pyproject.toml.is_file()`, which returns `False` for a
    dangling symlink (or one pointing at a directory), silently vanishing
    the whole consumer from its results BEFORE the loop's own symlink check
    ever runs, contradicting the "any symlinked manifest is rejected"
    guarantee (PR #4514 review). The pre-filter scan below must catch this
    class regardless of where the link points."""
    _write(repo, "plugins/gamma/plugin.json",
           json.dumps({"name": "gamma", "version": "3.0.0-dev1"}) + "\n")
    _write(repo, "plugins/gamma/src/gamma/__init__.py", "x = 1\n")
    (repo / "plugins" / "gamma" / "pyproject.toml").symlink_to(
        repo / "plugins" / "gamma" / "does-not-exist.toml"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add gamma with a dangling symlinked pyproject.toml")
    result = _run(repo, "--list")
    assert result.returncode != 0
    assert "gamma" in result.stderr
    assert "symlink" in result.stderr.lower()


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
