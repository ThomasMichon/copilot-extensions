"""Regression tests for tools/sync-vendored-libs.py (canonical <-> vendored-copy
sync for shared libs under libs/<lib> and plugins/<plugin>/libs/<lib>).

Drives the real script as a subprocess against a throwaway tree (mirroring
test_check_version_bump.py), since the module name has a hyphen. No git is
needed -- the script is purely filesystem-based.

Run:  python -m pytest tools/test_sync_vendored_libs.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "sync-vendored-libs.py"


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _lib_pyproject(repo: Path, rel: str, version: str) -> None:
    _write(repo, rel, f'[project]\nname = "x"\nversion = "{version}"\n')


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
    return r


def _seed_two_copies_in_sync(repo: Path, *, version: str = "0.1.0-dev1") -> None:
    for plugin in ("alpha", "beta"):
        _write(repo, f"plugins/{plugin}/libs/shared-lib/src/shared_lib/__init__.py",
               "shared = 1\n")
        _lib_pyproject(repo, f"plugins/{plugin}/libs/shared-lib/pyproject.toml", version)


def test_check_reports_ok_when_copies_agree_and_no_canonical(repo: Path):
    _seed_two_copies_in_sync(repo)
    result = _run(repo, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "copies agree" in result.stdout
    # No top-level libs/shared-lib/ yet -- advisory drift note, not a failure.
    assert "no top-level canonical" in result.stdout


def test_check_fails_when_copies_disagree(repo: Path):
    _seed_two_copies_in_sync(repo)
    _write(repo, "plugins/beta/libs/shared-lib/src/shared_lib/__init__.py",
           "shared = 2  # drifted\n")
    result = _run(repo, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OUT OF SYNC" in result.stdout


def test_check_reports_canonical_drift_advisory_only(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")
    result = _run(repo, "--check")
    # Copies still agree with each other -> exit 0, even though canonical is stale.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "canonical drift" in result.stdout
    assert "version skew -- canonical=0.1.0-dev12 copies=0.1.0-dev21" in result.stdout


def test_restore_canonical_copies_agreeing_copies_up(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "canonical restored" in result.stdout

    restored = (repo / "libs/shared-lib/src/shared_lib/__init__.py").read_text()
    assert restored == "shared = 1\n"
    restored_pp = (repo / "libs/shared-lib/pyproject.toml").read_text()
    assert '"0.1.0-dev21"' in restored_pp

    # Now a --check should show zero canonical drift.
    check = _run(repo, "--check")
    assert check.returncode == 0
    assert "shared-lib: canonical drift" not in check.stdout


def test_restore_canonical_skips_when_copies_disagree(repo: Path):
    _seed_two_copies_in_sync(repo)
    _write(repo, "plugins/beta/libs/shared-lib/src/shared_lib/__init__.py",
           "shared = 2  # drifted\n")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0  # advisory tool; reports, doesn't fail the run
    assert "SKIPPED" in result.stdout
    # Canonical must be untouched since copies disagreed.
    assert (repo / "libs/shared-lib/src/shared_lib/__init__.py").read_text() == "shared = 1\n"


def test_materialize_refuses_when_canonical_is_drifted(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused to materialize" in result.stderr
    # Copies must be untouched.
    copy = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    assert copy.read_text() == "shared = 1\n"


def test_materialize_after_restore_round_trips_cleanly(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    assert _run(repo, "--restore-canonical").returncode == 0

    # Simulate a real canonical-only change (the DRY workflow this effort wants):
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev22")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr
    for plugin in ("alpha", "beta"):
        copy = repo / f"plugins/{plugin}/libs/shared-lib/src/shared_lib/__init__.py"
        assert copy.read_text() == "shared = 2\n"
        pp = (repo / f"plugins/{plugin}/libs/shared-lib/pyproject.toml").read_text()
        assert '"0.1.0-dev22"' in pp

    check = _run(repo, "--check")
    assert check.returncode == 0
    assert "shared-lib: canonical drift" not in check.stdout


def test_materialize_force_overrides_drift_refusal(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    result = _run(repo, "--materialize", "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    copy = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    assert copy.read_text() == "shared = 999  # stale\n"


# --- DRY vendor pointers -----------------------------------------------

def _pointer(repo: Path, plugin: str, lib: str) -> None:
    _write(repo, f"plugins/{plugin}/libs/{lib}/VENDOR_POINTER.json",
           '{"schema": "copilot-extensions.vendor-pointer", "version": 1, '
           f'"source": "libs/{lib}"}}\n')


def test_restore_canonical_never_wipes_canonical_when_copies_are_pointers(repo: Path):
    """Regression test: converting a lib's copies to DRY pointers and then
    running --restore-canonical must never treat "no content" as truth and
    wipe canonical -- this actually happened while trialing this tool."""
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "real canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    for plugin in ("alpha", "beta"):
        _pointer(repo, plugin, "shared-lib")
        _lib_pyproject(repo, f"plugins/{plugin}/libs/shared-lib/pyproject.toml", "0.1.0-dev5")

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all copies are DRY pointers" in result.stdout

    # Canonical must be completely untouched.
    assert (repo / "libs/shared-lib/src/shared_lib/__init__.py").read_text() == (
        "real canonical content\n"
    )


def test_check_excludes_pointer_copies_from_agreement(repo: Path):
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "real = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    _pointer(repo, "beta", "shared-lib")  # pointer copy carries no src/ at all

    result = _run(repo, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COPIES OUT OF SYNC" not in result.stdout
    assert "1 DRY pointer copy/copies" in result.stdout


def test_materialize_expands_pointer_copy_and_removes_pointer_file(repo: Path):
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    _pointer(repo, "alpha", "shared-lib")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr

    copy_src = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    assert copy_src.read_text() == "canonical content\n"
    assert not (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()


def test_materialize_pointer_copy_is_never_blocked_by_drift_gate(repo: Path):
    """A pointer copy has no version of its own to compare against canonical,
    so there is nothing for the "copies moved ahead" safety gate to block --
    materialize must proceed even though the sibling real copy would be
    considered drifted if compared naively."""
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    _pointer(repo, "alpha", "shared-lib")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Refused to materialize" not in result.stderr


def test_materialize_refreshes_a_pointer_copys_stale_tests_directory(repo: Path):
    # A pointer copy vendors tests/ from canonical at --pointerize time;
    # --materialize must refresh it too, or a canonical test change after
    # pointerizing leaves the copy running (and eventually shipping) a
    # stale tests/ tree.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    pass\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    _pointer(repo, "alpha", "shared-lib")
    _write(repo, "plugins/alpha/libs/shared-lib/tests/test_thing.py",
           "def test_it():\n    assert False  # stale\n")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr

    refreshed = repo / "plugins/alpha/libs/shared-lib/tests/test_thing.py"
    assert refreshed.read_text() == "def test_it():\n    pass\n"


def test_materialize_never_touches_a_real_copys_own_tests_directory(repo: Path):
    # tests/ refresh is scoped to pointer copies only -- a real copy's own
    # tests/ may be independently authored per plugin and must not be
    # silently overwritten.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    pass\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/tests/test_thing.py",
           "def test_it():\n    assert True  # plugin-specific\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr

    untouched = repo / "plugins/alpha/libs/shared-lib/tests/test_thing.py"
    assert untouched.read_text() == "def test_it():\n    assert True  # plugin-specific\n"


def test_materialize_refuses_a_symlink_in_canonical_tests(repo: Path):
    # shutil.copytree's default symlinks=False follows and dereferences a
    # symlink -- a canonical tests/ containing one must not let its target
    # content leak into a vendored copy.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    (repo / "libs/shared-lib/tests").mkdir(parents=True)
    secret = repo.parent / "outside-repo-secret"
    secret.mkdir()
    (secret / "leaked.txt").write_text("do not leak\n", encoding="utf-8")
    (repo / "libs/shared-lib/tests/evil-link").symlink_to(secret, target_is_directory=True)
    _pointer(repo, "alpha", "shared-lib")
    (repo / "plugins/alpha/libs/shared-lib/tests").mkdir(parents=True)
    (repo / "plugins/alpha/libs/shared-lib/tests/placeholder.py").write_text(
        "\n", encoding="utf-8"
    )

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused to materialize" in result.stderr
    assert "is a symlink" in result.stderr
    assert not (repo / "plugins/alpha/libs/shared-lib/tests/evil-link").exists()


def test_materialize_rejection_never_destroys_the_previous_tests_content(repo: Path):
    # _copy_tests() must validate canonical BEFORE deleting the copy's own
    # existing tests/ -- a rejected refresh (canonical has a symlink) must
    # leave the copy's previous, safe tests/ content intact, not destroy it
    # and leave nothing behind.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    (repo / "libs/shared-lib/tests").mkdir(parents=True)
    secret = repo.parent / "outside-repo-secret2"
    secret.mkdir()
    (repo / "libs/shared-lib/tests/evil-link").symlink_to(secret, target_is_directory=True)
    _pointer(repo, "alpha", "shared-lib")
    original = repo / "plugins/alpha/libs/shared-lib/tests"
    original.mkdir(parents=True)
    (original / "test_thing.py").write_text("def test_it():\n    pass\n", encoding="utf-8")

    _run(repo, "--materialize")

    assert (original / "test_thing.py").read_text() == "def test_it():\n    pass\n"


def test_materialize_rejects_a_bad_tests_symlink_before_touching_src(repo: Path):
    # A copy carrying both src/ and tests/ must have BOTH canonical trees
    # validated before either is mutated -- otherwise a rejected tests/
    # refresh (found only after src/ was already replaced) would leave the
    # copy in a mixed state: fresh src/, stale tests/, and the pointer
    # marker still present (materialize would then look "half done" on a
    # retry, or a promotion could snapshot the mismatched pair).
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "fresh canonical\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev2")
    (repo / "libs/shared-lib/tests").mkdir(parents=True)
    secret = repo.parent / "outside-repo-secret3"
    secret.mkdir()
    (repo / "libs/shared-lib/tests/evil-link").symlink_to(secret, target_is_directory=True)
    _pointer(repo, "alpha", "shared-lib")
    copy_dir = repo / "plugins/alpha/libs/shared-lib"
    (copy_dir / "src/shared_lib").mkdir(parents=True)
    (copy_dir / "src/shared_lib/__init__.py").write_text("stale stub\n", encoding="utf-8")
    (copy_dir / "tests").mkdir(parents=True)
    (copy_dir / "tests/test_thing.py").write_text(
        "def test_it():\n    pass\n", encoding="utf-8"
    )

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr

    # src/ was never touched -- the rejection happened before any mutation.
    assert (copy_dir / "src/shared_lib/__init__.py").read_text() == "stale stub\n"
    # tests/ was never touched either.
    assert (copy_dir / "tests/test_thing.py").read_text() == "def test_it():\n    pass\n"
    # The pointer marker is still present -- this copy is still "pending".
    assert (copy_dir / "VENDOR_POINTER.json").exists()


def test_materialize_catches_a_dangling_tests_symlink_at_the_destination(repo: Path):
    # A dangling (or non-directory-target) symlink at copy/tests has
    # is_dir()==False, since is_dir() follows the link to a target that
    # isn't there -- an is_dir()-only "does this copy have tests/" gate
    # would silently ignore it, leaving it untouched in a materialized
    # release.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    pass\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _pointer(repo, "alpha", "shared-lib")
    copy_tests = repo / "plugins/alpha/libs/shared-lib/tests"
    copy_tests.symlink_to(repo.parent / "does-not-exist", target_is_directory=True)

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused to materialize" in result.stderr
    assert "destination" in result.stderr and "is a symlink" in result.stderr


# --- src-passthrough vendor pointers (working real-Python forwarding) ---

def _seed_canonical_lib(repo: Path, lib: str, *, version: str, content: str) -> Path:
    pkg = lib.replace("-", "_")
    _write(repo, f"libs/{lib}/src/{pkg}/__init__.py", content)
    _lib_pyproject(repo, f"libs/{lib}/pyproject.toml", version)
    (repo / f"libs/{lib}/README.md").write_text("# doc\n", encoding="utf-8")
    return repo / "libs" / lib


def test_pointerize_writes_a_src_passthrough_pointer(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev3", content="value = 1\n")
    (repo / "plugins/alpha").mkdir(parents=True)

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pointerized" in result.stdout

    copy_dir = repo / "plugins/alpha/libs/shared-lib"
    pointer = json.loads((copy_dir / "VENDOR_POINTER.json").read_text())
    assert pointer == {
        "schema": "copilot-extensions.vendor-pointer",
        "version": 1,
        "source": "libs/shared-lib",
        "kind": "src-passthrough",
    }
    # A REAL, installable pyproject.toml -- synced from canonical, not a stub.
    assert '"0.1.0-dev3"' in (copy_dir / "pyproject.toml").read_text()
    stub = (copy_dir / "src/shared_lib/__init__.py").read_text()
    assert stub.startswith("# VENDOR_POINTER: source=libs/shared-lib kind=src-passthrough\n")
    assert "spec_from_file_location" in stub


def test_pointerize_refuses_when_canonical_lib_missing(repo: Path):
    (repo / "plugins/alpha").mkdir(parents=True)
    result = _run(repo, "--pointerize", "alpha", "ghost-lib")
    assert result.returncode != 0
    assert "no canonical libs/ghost-lib" in (result.stdout + result.stderr)


def test_pointerize_refuses_a_symlinked_src_root(repo: Path):
    # The canonical src/ ROOT being a symlink (not just a symlink somewhere
    # inside it) must be caught too -- scanning from canon_pkg_dir
    # (canonical/src/<pkg>) instead of canonical/src itself would miss
    # this: canon_pkg_dir is constructed by joining paths, so is_dir()
    # transparently follows the src/ symlink and _find_symlink() only ever
    # sees the (external) target's own contents, letting --pointerize
    # accept an external source tree even though the materializers
    # explicitly reject a symlinked src/ root.
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    external = repo.parent / "outside-repo-src"
    (external / "shared_lib").mkdir(parents=True)
    (external / "shared_lib" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "libs/shared-lib/src").symlink_to(external, target_is_directory=True)
    (repo / "plugins/alpha").mkdir(parents=True)

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "is a symlink" in (result.stdout + result.stderr)
    assert not (repo / "plugins/alpha/libs/shared-lib").exists()


def test_pointerize_refuses_a_symlinked_canonical_lib_root(repo: Path):
    # A `libs/<lib>` link to an external tree must be caught even though
    # canonical/src itself is a real directory -- the check must validate
    # the canonical LIB ROOT itself, not only its src/ subdirectory.
    external = repo.parent / "outside-repo-lib"
    (external / "src" / "shared_lib").mkdir(parents=True)
    (external / "src" / "shared_lib" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (external / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )
    (repo / "libs").mkdir(parents=True)
    (repo / "libs/shared-lib").symlink_to(external, target_is_directory=True)
    (repo / "plugins/alpha").mkdir(parents=True)

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "is a symlink" in (result.stdout + result.stderr)
    assert not (repo / "plugins/alpha/libs/shared-lib").exists()


def test_pointerize_supports_the_worktree_manager_extra_consumer_tree(repo: Path):
    # worktree-manager sits at the repo root, not under plugins/ -- confirm
    # --pointerize resolves it via _consumer_dir() the same way _lib_copies()
    # already does for --check/--materialize.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    (repo / "worktree-manager").mkdir(parents=True)

    result = _run(repo, "--pointerize", "worktree-manager", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr

    copy_dir = repo / "worktree-manager/libs/shared-lib"
    assert (copy_dir / "VENDOR_POINTER.json").exists()
    assert not (repo / "plugins/worktree-manager").exists()


def test_pointerize_refuses_an_unknown_consumer(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    result = _run(repo, "--pointerize", "not-a-real-consumer", "shared-lib")
    assert result.returncode != 0
    assert "not a known consumer" in (result.stdout + result.stderr)


def test_pointerize_overwrites_a_stale_existing_copy(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="x = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "stale\n")
    _write(repo, "plugins/alpha/libs/shared-lib/leftover.txt", "should be gone\n")

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "plugins/alpha/libs/shared-lib/leftover.txt").exists()
    assert (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()


def test_pointerize_vendors_canonicals_tests_directory(repo: Path):
    # A consumer with no `testpaths` override (e.g. worktree-manager) uses
    # pytest's own default recursive discovery, which would run a copy's
    # own libs/<lib>/tests/ directly -- silently dropping it (as an earlier
    # draft of this fix did) would silently drop real test coverage, not
    # just leave a drift-check comparison out of scope.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    assert True\n")
    (repo / "plugins/alpha").mkdir(parents=True)

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr

    vendored_test = repo / "plugins/alpha/libs/shared-lib/tests/test_thing.py"
    assert vendored_test.read_text() == "def test_it():\n    assert True\n"


def test_pointerize_is_a_noop_for_tests_when_canonical_has_none(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    (repo / "plugins/alpha").mkdir(parents=True)

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "plugins/alpha/libs/shared-lib/tests").exists()


def test_repointerize_preserves_a_prior_no_tests_decision(repo: Path):
    # A copy that was FIRST pointerized before canonical grew a tests/
    # directory (or whose --pointerize deliberately chose none) must keep
    # that choice on every later re-pointerize (e.g. to regenerate a stub
    # after a template/wording change) -- it must never silently gain a
    # tests/ it never had just because canonical happens to have one by
    # the time of the re-run. Otherwise a copy matching what `main` ships
    # today would drift the next time its stub is regenerated.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    (repo / "plugins/alpha").mkdir(parents=True)

    # First pointerize: canonical has no tests/ yet.
    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "plugins/alpha/libs/shared-lib/tests").exists()

    # Canonical grows a tests/ directory afterward.
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    assert True\n")

    # Re-pointerizing the SAME already-pointer copy must not introduce it.
    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "plugins/alpha/libs/shared-lib/tests").exists()


def test_repointerize_preserves_a_prior_has_tests_decision(repo: Path):
    # The mirror case: a copy that already vendored tests/ keeps getting a
    # refreshed tests/ across a re-pointerize, rather than losing it.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    assert True\n")
    (repo / "plugins/alpha").mkdir(parents=True)

    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    vendored_test = repo / "plugins/alpha/libs/shared-lib/tests/test_thing.py"
    assert vendored_test.read_text() == "def test_it():\n    assert True\n"

    # Canonical's test content changes; re-pointerizing should refresh it.
    _write(repo, "libs/shared-lib/tests/test_thing.py", "def test_it():\n    assert False\n")
    result = _run(repo, "--pointerize", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert vendored_test.read_text() == "def test_it():\n    assert False\n"


def test_pointerized_copy_forwards_imports_to_canonical_end_to_end(repo: Path):
    """The real, load-bearing guarantee: a fresh interpreter that imports the
    pointerized package gets canonical's actual live content, not a stale
    snapshot -- proven by mutating canonical AFTER pointerizing and re-
    importing in a brand-new subprocess (no import caching across runs)."""
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    (repo / "plugins/alpha").mkdir(parents=True)
    _run(repo, "--pointerize", "alpha", "shared-lib")

    stub = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    probe = (
        "import sys; sys.path.insert(0, r'" + str(stub.parent.parent) + "'); "
        "import shared_lib; print(shared_lib.value)"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=repo,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "1"

    # Mutate canonical directly (no re-pointerize) -- the stub must reflect
    # the new content on the NEXT fresh interpreter, proving there is
    # nothing cached/copied to keep in sync.
    (repo / "libs/shared-lib/src/shared_lib/__init__.py").write_text("value = 2\n", encoding="utf-8")
    result2 = subprocess.run([sys.executable, "-c", probe], cwd=repo,
                             capture_output=True, text=True, check=False)
    assert result2.returncode == 0, result2.stdout + result2.stderr
    assert result2.stdout.strip() == "2"


def test_pointerized_copy_from_import_also_resolves_to_canonical(repo: Path):
    """``from pkg import name`` must resolve through the same self-replacing-
    module swap as a plain ``import pkg`` -- exercised separately since it is
    a different import bytecode path."""
    _seed_canonical_lib(
        repo, "shared-lib", version="0.1.0-dev1",
        content="def greet():\n    return 'hi'\n",
    )
    (repo / "plugins/alpha").mkdir(parents=True)
    _run(repo, "--pointerize", "alpha", "shared-lib")

    stub = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    probe = (
        "import sys; sys.path.insert(0, r'" + str(stub.parent.parent) + "'); "
        "from shared_lib import greet; print(greet())"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=repo,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "hi"


def test_check_excludes_src_passthrough_copy_from_agreement(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="x = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "real = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    (repo / "plugins/beta").mkdir(parents=True)
    _run(repo, "--pointerize", "beta", "shared-lib")

    result = _run(repo, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COPIES OUT OF SYNC" not in result.stdout
    assert "1 DRY pointer copy/copies" in result.stdout


def test_materialize_expands_a_src_passthrough_copy_into_a_real_copy(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev5", content="real content\n")
    (repo / "plugins/alpha").mkdir(parents=True)
    _run(repo, "--pointerize", "alpha", "shared-lib")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr

    copy_src = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    assert copy_src.read_text() == "real content\n"
    assert not (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()
