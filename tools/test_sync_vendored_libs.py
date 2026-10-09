"""Regression tests for tools/sync-vendored-libs.py (canonical <-> vendored-copy
sync for shared libs under libs/<lib> and plugins/<plugin>/libs/<lib>).

Drives the real script as a subprocess against a throwaway tree (mirroring
test_check_version_bump.py), since the module name has a hyphen. No git is
needed -- the script is purely filesystem-based.

Run:  python -m pytest tools/test_sync_vendored_libs.py
"""
from __future__ import annotations

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
    # sync-vendored-libs.py imports this sibling module (split out
    # purely to keep it under this repo's per-module line-count cap) --
    # an isolated tree carrying only the script itself would otherwise
    # fail every subprocess invocation with ModuleNotFoundError.
    for sibling in ("uv_editable_ref.py", "standalone_consumers.py"):
        src = SCRIPT.parent / sibling
        (r / "tools" / sibling).write_bytes(src.read_bytes())
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


def test_restore_canonical_creates_missing_top_level_lib_and_copies_full_tree(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    for plugin in ("alpha", "beta"):
        _write(repo, f"plugins/{plugin}/libs/shared-lib/README.md", "# shared-lib\n")
        _write(
            repo,
            f"plugins/{plugin}/libs/shared-lib/tests/test_shared.py",
            "def test_ok():\n    assert True\n",
        )

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "canonical restored" in result.stdout

    canonical = repo / "libs/shared-lib"
    assert (canonical / "src/shared_lib/__init__.py").read_text() == "shared = 1\n"
    assert (canonical / "pyproject.toml").read_text(encoding="utf-8").count("0.1.0-dev21") == 1
    assert (canonical / "README.md").read_text(encoding="utf-8") == "# shared-lib\n"
    assert (canonical / "tests/test_shared.py").read_text(encoding="utf-8") == (
        "def test_ok():\n    assert True\n"
    )


def test_restore_canonical_skips_when_full_tree_differs_even_if_src_and_version_match(repo: Path):
    _seed_two_copies_in_sync(repo)
    _write(repo, "plugins/alpha/libs/shared-lib/README.md", "# alpha\n")
    _write(repo, "plugins/beta/libs/shared-lib/README.md", "# beta\n")

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" in result.stdout
    assert "complete filtered tree differs" in result.stdout
    assert not (repo / "libs/shared-lib").exists()


def test_restore_canonical_refuses_a_symlinked_copy_ancestor(repo: Path):
    real = repo.parent / "shared-lib-real"
    _write(real, "src/shared_lib/__init__.py", "shared = 1\n")
    _lib_pyproject(real, "pyproject.toml", "0.1.0-dev1")
    target = repo / "plugins" / "alpha" / "libs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(real.parent, target_is_directory=True)

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "is a symlink -- refusing" in result.stderr
    assert not (repo / "libs/shared-lib").exists()


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


def test_materialize_refuses_a_stray_symlink_anywhere_in_the_pointer_copy(repo: Path):
    # The src/tests/pyproject.toml checks validate the pieces this
    # function itself knows about, but a pointer copy directory can carry
    # other, unrelated entries too (e.g. a stray docs/link) -- without a
    # final blanket scan, _copy_src()/pointer.unlink() would proceed and
    # leave such a symlink sitting untouched in the materialized copy,
    # making it not self-contained. Mirrors materialize_main.py's own
    # final blanket scan.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    _pointer(repo, "alpha", "shared-lib")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    outside = repo.parent / "outside-stray-target-cmd-materialize"
    outside.mkdir()
    copy_docs = repo / "plugins/alpha/libs/shared-lib/docs"
    copy_docs.mkdir()
    (copy_docs / "link").symlink_to(outside, target_is_directory=True)

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "is a symlink" in result.stderr
    # Nothing was mutated -- pointer marker still present, stray symlink
    # untouched.
    assert (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()
    assert (copy_docs / "link").is_symlink()


def test_materialize_refuses_and_preserves_the_pointer_for_a_symlinked_copy_pyproject_toml(
    repo: Path,
):
    # A symlinked destination pyproject.toml is now preflighted BEFORE
    # _copy_src() runs (round-17 review finding): _sync_version()'s own
    # guard only silently returns without writing, which isn't enough on
    # its own -- without the caller-side preflight, this loop would
    # continue on to mutate src/ and unlink the pointer marker as though
    # everything succeeded, leaving a pointer-free copy with an unsafe
    # linked pyproject.toml surviving untouched.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev5")
    _pointer(repo, "alpha", "shared-lib")
    original = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text("original stub\n", encoding="utf-8")
    victim = repo.parent / "outside-victim-copy-pyproject.toml"
    victim.write_text('[project]\nname = "victim"\nversion = "1.2.3"\n', encoding="utf-8")
    (repo / "plugins/alpha/libs/shared-lib/pyproject.toml").symlink_to(victim)

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "symlink" in result.stderr

    # Nothing was mutated -- src/ untouched, victim untouched, pointer
    # marker still present (this copy is still "pending", not silently
    # published pointer-free).
    assert original.read_text() == "original stub\n"
    assert victim.read_text() == '[project]\nname = "victim"\nversion = "1.2.3"\n'
    assert (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()


def test_materialize_refuses_a_symlinked_canonical_lib_root(repo: Path):
    # A `libs/<lib>` link to an external tree must be caught even though
    # canonical/src itself is a real directory within that external tree.
    external = repo.parent / "outside-repo-lib-materialize"
    (external / "src" / "shared_lib").mkdir(parents=True)
    (external / "src" / "shared_lib" / "__init__.py").write_text(
        "smuggled = True\n", encoding="utf-8"
    )
    (repo / "libs").mkdir(parents=True)
    (repo / "libs/shared-lib").symlink_to(external, target_is_directory=True)
    _pointer(repo, "alpha", "shared-lib")
    original = (repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py")
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text("original stub\n", encoding="utf-8")

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "symlink" in result.stderr
    # The copy's own content must be untouched -- nothing was expanded.
    assert original.read_text() == "original stub\n"
    assert (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()


def test_materialize_refuses_a_canonical_lib_with_no_src_directory(repo: Path):
    # An incomplete canonical lib with no src/ at all must be refused
    # BEFORE the copy's existing src/ is deleted with nothing to replace
    # it -- otherwise the pointer marker would still get unlinked below as
    # if expansion had succeeded, publishing a broken, source-less copy.
    (repo / "libs/shared-lib").mkdir(parents=True)  # no src/ subdirectory
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _pointer(repo, "alpha", "shared-lib")
    original = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text("original stub\n", encoding="utf-8")

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "src not found" in result.stderr
    assert original.read_text() == "original stub\n"
    assert (repo / "plugins/alpha/libs/shared-lib/VENDOR_POINTER.json").exists()


def test_materialize_refuses_a_symlinked_copy_root(repo: Path):
    # _lib_copies() admits any lib.is_dir(), which follows a symlink, but
    # the per-copy loop never rejected `copy` itself being a symlink (e.g.
    # plugins/alpha/libs/shared-lib -> ../beta/libs/shared-lib) -- without
    # this, _copy_src() would reach real child paths through the link,
    # overwriting the TARGET's own src/version and unlinking the TARGET's
    # pointer marker.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _pointer(repo, "beta", "shared-lib")
    target = repo / "plugins/beta/libs/shared-lib"
    (target / "innocent.txt").write_text("do not touch\n", encoding="utf-8")

    (repo / "plugins/alpha/libs").mkdir(parents=True)
    (repo / "plugins/alpha/libs/shared-lib").symlink_to(target, target_is_directory=True)

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "symlink" in result.stderr
    # The target's own content must be untouched by the attacker's
    # symlinked copy attempt (its own genuine pointer may still
    # legitimately expand independently -- that's expected and fine).
    assert (target / "innocent.txt").read_text() == "do not touch\n"
    assert (repo / "plugins/alpha/libs/shared-lib").is_symlink()


def test_materialize_refuses_a_symlinked_ancestor_of_the_copy_root(repo: Path):
    # _lib_copies() follows symlinks in plugins/<plugin> and libs while
    # discovering `copy` -- checking only the final copy path (round 13's
    # fix) misses a symlinked ANCESTOR (e.g. plugins/<plugin> itself),
    # which could make --materialize write through to another consumer
    # via the same class of attack, just one level higher.
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "canonical content\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _pointer(repo, "beta", "shared-lib")
    target = repo / "plugins/beta"
    (target / "innocent.txt").write_text("do not touch\n", encoding="utf-8")

    (repo / "plugins/alpha").symlink_to(target, target_is_directory=True)

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "symlink" in result.stderr
    assert (target / "innocent.txt").read_text() == "do not touch\n"
    assert (repo / "plugins/alpha").is_symlink()


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


def _seed_canonical_lib(repo: Path, lib: str, *, version: str, content: str) -> Path:
    pkg = lib.replace("-", "_")
    _write(repo, f"libs/{lib}/src/{pkg}/__init__.py", content)
    _lib_pyproject(repo, f"libs/{lib}/pyproject.toml", version)
    (repo / f"libs/{lib}/README.md").write_text("# doc\n", encoding="utf-8")
    return repo / "libs" / lib


def _seed_consumer_pyproject(repo: Path, consumer_dir: str, lib: str, *, dist_name: str = "agent-shared-lib") -> Path:
    """Write a minimal consumer pyproject.toml carrying the ordinary
    in-tree ``[tool.uv.sources]`` entry a real vendored copy has today
    (``{ path = "libs/<lib>" }``) -- the shape ``--uv-editable`` converts
    away from."""
    pp = repo / consumer_dir / "pyproject.toml"
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(
        "[project]\n"
        'name = "consumer"\n'
        'version = "1.0.0"\n'
        f'dependencies = ["{dist_name}"]\n'
        "\n"
        "[tool.uv.sources]\n"
        f'{dist_name} = {{ path = "libs/{lib}" }}\n',
        encoding="utf-8",
    )
    return pp


def test_uv_editable_converts_a_real_copy_to_a_live_reference(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "value = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _write(repo, "plugins/alpha/libs/shared-lib/README.md", "# doc\n")
    pp = _seed_consumer_pyproject(repo, "plugins/alpha", "shared-lib")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "converted" in result.stdout

    # No directory, no stub -- nothing remains at the vendored path.
    assert not (repo / "plugins/alpha/libs/shared-lib").exists()
    text = pp.read_text()
    assert 'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }' in text


def test_uv_editable_converts_a_src_passthrough_pointer_copy(repo: Path):
    # The reverse-direction conversion: an already-src-passthrough copy has
    # nothing to lose (its src/ is never the verified truth), so no drift
    # check should ever block this direction.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _pointer(repo, "alpha", "shared-lib")
    pp = _seed_consumer_pyproject(repo, "plugins/alpha", "shared-lib")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr

    assert not (repo / "plugins/alpha/libs/shared-lib").exists()
    assert 'editable = true' in pp.read_text()


def test_uv_editable_refuses_a_drifted_real_copy(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="canonical\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "locally modified\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    pp = _seed_consumer_pyproject(repo, "plugins/alpha", "shared-lib")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "differs from canonical" in (result.stdout + result.stderr)
    # Nothing was discarded or rewritten.
    assert (repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py").read_text() == (
        "locally modified\n"
    )
    assert 'editable = true' not in pp.read_text()


def test_uv_editable_refuses_when_canonical_lib_missing(repo: Path):
    (repo / "plugins/alpha").mkdir(parents=True)
    _seed_consumer_pyproject(repo, "plugins/alpha", "ghost-lib", dist_name="agent-ghost-lib")
    result = _run(repo, "--uv-editable", "alpha", "ghost-lib")
    assert result.returncode != 0
    assert "no canonical libs/ghost-lib" in (result.stdout + result.stderr)


def test_uv_editable_supports_the_worktree_manager_extra_consumer_tree(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py", "value = 1\n")
    _lib_pyproject(repo, "worktree-manager/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _write(repo, "worktree-manager/libs/shared-lib/README.md", "# doc\n")
    pp = _seed_consumer_pyproject(repo, "worktree-manager", "shared-lib")

    result = _run(repo, "--uv-editable", "worktree-manager", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "worktree-manager/libs/shared-lib").exists()
    assert 'agent-shared-lib = { path = "../libs/shared-lib", editable = true }' in pp.read_text()


def test_check_reports_invalid_uv_editable_entry_missing_editable_flag(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    (repo / "plugins/alpha").mkdir(parents=True)
    _write(
        repo, "plugins/alpha/pyproject.toml",
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../../libs/shared-lib" }\n',
    )
    result = _run(repo, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "INVALID uv-editable canonical reference" in result.stdout
    assert "missing editable = true" in result.stdout


def test_check_reports_invalid_uv_editable_entry_missing_canonical(repo: Path):
    (repo / "plugins/alpha").mkdir(parents=True)
    _write(
        repo, "plugins/alpha/pyproject.toml",
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-ghost-lib = { path = "../../libs/ghost-lib", editable = true }\n',
    )
    result = _run(repo, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "INVALID uv-editable canonical reference" in result.stdout
    assert "does not exist" in result.stdout


def test_check_passes_a_valid_uv_editable_entry(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    (repo / "plugins/alpha").mkdir(parents=True)
    _write(
        repo, "plugins/alpha/pyproject.toml",
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }\n',
    )
    result = _run(repo, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INVALID" not in result.stdout


def test_uv_editable_refuses_a_copy_with_a_locally_edited_readme(repo: Path):
    # The drift check must compare the COMPLETE discardable tree (src/,
    # tests/, README.md, pyproject.toml) -- not just src/. A copy whose
    # source is byte-identical to canonical but whose README.md was
    # independently edited must still be refused, or that edit is silently
    # discarded once the local copy is deleted.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "value = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _write(repo, "plugins/alpha/libs/shared-lib/README.md", "# locally edited doc\n")
    _seed_consumer_pyproject(repo, "plugins/alpha", "shared-lib")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "differs from canonical" in (result.stdout + result.stderr)
    assert (repo / "plugins/alpha/libs/shared-lib/README.md").read_text() == (
        "# locally edited doc\n"
    )


def test_uv_editable_rewrite_scoped_to_uv_sources_table_only(repo: Path):
    # An identical-looking `{ path = "libs/<lib>" }` value in an UNRELATED
    # table must never be rewritten -- only the real [tool.uv.sources]
    # entry.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "value = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    (repo / "plugins/alpha/libs/shared-lib/README.md").write_text("# doc\n", encoding="utf-8")
    pp = repo / "plugins/alpha/pyproject.toml"
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(
        "[project]\n"
        'name = "consumer"\n'
        'version = "1.0.0"\n'
        'dependencies = ["agent-shared-lib"]\n'
        "\n"
        "[tool.some-other-table]\n"
        'unrelated_field = { path = "libs/shared-lib" }\n'
        "\n"
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "libs/shared-lib" }\n',
        encoding="utf-8",
    )

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode == 0, result.stdout + result.stderr

    text = pp.read_text()
    assert 'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }' in text
    # The unrelated table's identical-looking value survives untouched.
    assert 'unrelated_field = { path = "libs/shared-lib" }' in text


def test_uv_editable_refuses_before_deleting_when_no_rewrite_target(repo: Path):
    # If the consumer pyproject has no exact "path = \"libs/<lib>\"" entry
    # to rewrite, the conversion must refuse BEFORE the local copy is
    # touched -- never delete it and then fail the rewrite.
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "value = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _write(repo, "plugins/alpha/libs/shared-lib/README.md", "# doc\n")
    pp = repo / "plugins/alpha/pyproject.toml"
    pp.parent.mkdir(parents=True, exist_ok=True)
    # No [tool.uv.sources] entry at all for shared-lib.
    pp.write_text('[project]\nname = "consumer"\nversion = "1.0.0"\n', encoding="utf-8")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "no [tool.uv.sources] entry" in (result.stdout + result.stderr)
    # The local copy survives -- nothing was deleted before the refusal.
    assert (repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py").exists()


def test_check_rejects_a_symlinked_consumer_pyproject_even_with_no_visible_refs(repo: Path):
    # find_uv_editable_refs() returns [] for a symlinked pyproject.toml
    # (fails closed on read) -- --check must not let that look identical
    # to "nothing to validate": the symlink itself is the problem.
    real = repo.parent / "outside-pyproject.toml"
    real.write_text(
        '[tool.uv.sources]\nagent-x = { path = "../../libs/x", editable = true }\n',
        encoding="utf-8",
    )
    (repo / "plugins/alpha").mkdir(parents=True)
    (repo / "plugins/alpha/pyproject.toml").symlink_to(real)

    result = _run(repo, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "pyproject.toml is a symlink" in result.stdout


def test_check_reports_a_malformed_consumer_manifest(repo: Path):
    (repo / "plugins/alpha").mkdir(parents=True)
    _write(repo, "plugins/alpha/pyproject.toml", "this is not [ valid toml")
    result = _run(repo, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "could not read/parse" in result.stdout


def test_uv_editable_refuses_a_nested_symlink_in_canonical(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    victim = repo.parent / "outside-victim.py"
    victim.write_text("evil = 1\n", encoding="utf-8")
    (repo / "libs/shared-lib/src/shared_lib/sneaky.py").symlink_to(victim)
    (repo / "plugins/alpha").mkdir(parents=True)
    _seed_consumer_pyproject(repo, "plugins/alpha", "shared-lib")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "is a symlink" in (result.stdout + result.stderr)


def test_uv_editable_refuses_a_nested_symlink_in_the_local_copy(repo: Path):
    _seed_canonical_lib(repo, "shared-lib", version="0.1.0-dev1", content="value = 1\n")
    _write(repo, "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py", "value = 1\n")
    _lib_pyproject(repo, "plugins/alpha/libs/shared-lib/pyproject.toml", "0.1.0-dev1")
    _write(repo, "plugins/alpha/libs/shared-lib/README.md", "# doc\n")
    victim = repo.parent / "outside-victim2.py"
    victim.write_text("evil = 1\n", encoding="utf-8")
    (repo / "plugins/alpha/libs/shared-lib/src/shared_lib/sneaky.py").symlink_to(victim)
    _seed_consumer_pyproject(repo, "plugins/alpha", "shared-lib")

    result = _run(repo, "--uv-editable", "alpha", "shared-lib")
    assert result.returncode != 0
    assert "is a symlink" in (result.stdout + result.stderr)
