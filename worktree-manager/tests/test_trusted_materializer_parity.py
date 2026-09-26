"""Parity guard between the two pointer-materializer implementations.

``worktree_manager._trusted_pointer_materializer`` is a deliberate,
hand-maintained COPY of ``tools/materialize_main.py``'s pointer-expansion
core (see that module's own docstring for why it must be a real duplicate
rather than a DRY import: the trusted-vs-untrusted execution boundary it
protects is the opposite concern from this repo's normal vendor-pointer
DRY mechanism). Being hand-maintained means the two can silently diverge --
a future hardening fix landed in one without the other would leave either
promotion (`tools/materialize_main.py`) or self-update installs (the
trusted module) on stale/weaker behavior with no automated signal.

This test runs the SAME battery of scenarios (a clean expansion plus every
symlink-refusal shape the two modules are known to guard against) through
both implementations' shared ``materialize_libs_dir()``/
``find_pointers_in_libs_dir()`` API and asserts they produce equivalent
outcomes (both succeed, or both refuse for the same reason category).
A behavior drift between the two -- one accepting what the other refuses,
or vice versa -- fails this test immediately, standing in for the
"parity/contract guard" a reviewer asked for rather than requiring the two
modules be literally merged (which would reintroduce the trust boundary
the duplication exists to avoid).

Skips (rather than failing) when ``tools/materialize_main.py`` isn't
reachable at the expected monorepo-relative location -- this test only
makes sense run from within the full monorepo checkout, never from a
standalone worktree-manager payload (which never ships tools/ at all).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from worktree_manager import _trusted_pointer_materializer as trusted

_TOOLS_MATERIALIZE_MAIN = Path(__file__).resolve().parents[2] / "tools" / "materialize_main.py"


def _load_canonical_materialize_main():
    if not _TOOLS_MATERIALIZE_MAIN.is_file():
        pytest.skip("tools/materialize_main.py not reachable (not a full monorepo checkout)")
    spec = importlib.util.spec_from_file_location(
        "materialize_main_parity_reference", _TOOLS_MATERIALIZE_MAIN
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def canonical():
    return _load_canonical_materialize_main()


def _canonical_lib(root: Path, lib: str, *, version: str, content: str) -> Path:
    pkg = lib.replace("-", "_")
    d = root / "libs" / lib
    (d / "src" / pkg).mkdir(parents=True)
    (d / "src" / pkg / "__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    return d


def _pointer(libs_dir: Path, lib: str) -> Path:
    d = libs_dir / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / trusted.POINTER_NAME).write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": f"libs/{lib}"}) + "\n",
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n',
                                       encoding="utf-8")
    return d


def _outcome(log: list[str]) -> str:
    """Reduce a materialize_libs_dir() log to a coarse outcome category:
    'ok' (expanded successfully), or the leading clause of a SKIP reason
    (enough to compare like-for-like without demanding byte-identical
    wording between the two hand-maintained copies)."""
    assert len(log) == 1, log
    line = log[0]
    if line.startswith("OK"):
        return "ok"
    assert line.startswith("SKIP"), line
    return "skip: is a symlink" if "is a symlink" in line else f"skip: {line.split(':', 1)[1].strip()}"


def _run_scenario(tmp_path: Path, canonical_mod, *, name: str, build) -> tuple[str, str]:
    """``build(root, libs_dir)`` populates one scenario's canonical lib +
    pointer copy under a fresh root/libs_dir pair; returns the (trusted,
    canonical) outcome categories for identical inputs."""
    trusted_root = tmp_path / f"{name}-trusted" / "repo"
    trusted_libs = tmp_path / f"{name}-trusted" / "slot" / "libs"
    build(trusted_root, trusted_libs)
    trusted_log = trusted.materialize_libs_dir(trusted_libs, canonical_root=trusted_root)

    canonical_root = tmp_path / f"{name}-canonical" / "repo"
    canonical_libs = tmp_path / f"{name}-canonical" / "slot" / "libs"
    build(canonical_root, canonical_libs)
    canonical_log = canonical_mod.materialize_libs_dir(canonical_libs, canonical_root=canonical_root)

    return _outcome(trusted_log), _outcome(canonical_log)


def test_parity_expands_a_clean_pointer_from_canonical(tmp_path: Path, canonical):
    def build(root: Path, libs_dir: Path) -> None:
        _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
        _pointer(libs_dir, "zdd")

    got_trusted, got_canonical = _run_scenario(tmp_path, canonical, name="clean", build=build)
    assert got_trusted == got_canonical == "ok"


def test_parity_refuses_a_symlinked_canonical_src(tmp_path: Path, canonical):
    def build(root: Path, libs_dir: Path) -> None:
        canon = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
        outside = root.parent / "outside-target"
        (outside / "zdd").mkdir(parents=True)
        (outside / "zdd" / "__init__.py").write_text("smuggled = True\n", encoding="utf-8")
        shutil.rmtree(canon / "src")
        (canon / "src").symlink_to(outside, target_is_directory=True)
        _pointer(libs_dir, "zdd")

    got_trusted, got_canonical = _run_scenario(tmp_path, canonical, name="symlinked-src", build=build)
    assert got_trusted == got_canonical
    assert got_trusted == "skip: is a symlink"


def test_parity_refuses_a_canonical_lib_with_no_src(tmp_path: Path, canonical):
    def build(root: Path, libs_dir: Path) -> None:
        (root / "libs" / "zdd").mkdir(parents=True)  # no src/ subdirectory
        (root / "libs" / "zdd" / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
        )
        pointer_dir = _pointer(libs_dir, "zdd")
        (pointer_dir / "src" / "zdd").mkdir(parents=True)
        (pointer_dir / "src" / "zdd" / "__init__.py").write_text("stub\n", encoding="utf-8")

    got_trusted, got_canonical = _run_scenario(tmp_path, canonical, name="no-src", build=build)
    assert got_trusted == got_canonical
    assert got_trusted == "skip: canonical libs/zdd/src not found"


def test_parity_refuses_a_symlinked_pointer_copy_directory(tmp_path: Path, canonical):
    def build(root: Path, libs_dir: Path) -> None:
        _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
        target = libs_dir.parent.parent / "real-copy" / "zdd"
        target.mkdir(parents=True)
        (target / trusted.POINTER_NAME).write_text(
            json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                        "source": "libs/zdd"}) + "\n",
            encoding="utf-8",
        )
        libs_dir.mkdir(parents=True)
        (libs_dir / "zdd").symlink_to(target, target_is_directory=True)

    got_trusted, got_canonical = _run_scenario(tmp_path, canonical, name="symlinked-copy", build=build)
    assert got_trusted == got_canonical
    assert got_trusted == "skip: is a symlink"


def test_parity_refuses_a_stray_symlink_anywhere_in_the_pointer_copy(tmp_path: Path, canonical):
    def build(root: Path, libs_dir: Path) -> None:
        _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
        pointer_dir = _pointer(libs_dir, "zdd")
        outside = root.parent / "outside-stray-target"
        outside.mkdir(parents=True)
        (pointer_dir / "docs").mkdir()
        (pointer_dir / "docs" / "link").symlink_to(outside, target_is_directory=True)

    got_trusted, got_canonical = _run_scenario(tmp_path, canonical, name="stray-symlink", build=build)
    assert got_trusted == got_canonical
    assert got_trusted == "skip: is a symlink"


def test_parity_refreshes_a_stale_canonical_tests_directory(tmp_path: Path, canonical):
    def build(root: Path, libs_dir: Path) -> None:
        canon = _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
        (canon / "tests").mkdir(parents=True)
        (canon / "tests" / "test_thing.py").write_text(
            "def test_it():\n    pass\n", encoding="utf-8"
        )
        pointer_dir = _pointer(libs_dir, "zdd")
        (pointer_dir / "tests").mkdir(parents=True)
        (pointer_dir / "tests" / "test_thing.py").write_text(
            "def test_it():\n    assert False  # stale\n", encoding="utf-8"
        )

    got_trusted, got_canonical = _run_scenario(tmp_path, canonical, name="stale-tests", build=build)
    assert got_trusted == got_canonical == "ok"

