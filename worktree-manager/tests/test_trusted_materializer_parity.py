"""Parity guard for the still-live trusted UV materializer paths.

`worktree_manager._trusted_pointer_materializer` is a deliberate,
hand-maintained copy of the `uv`-editable canonical-reference materializer
logic that still lives in `tools/materialize_main.py` / `tools/uv_editable_ref.py`.
Because the trusted copy cannot import those repo `tools/` modules directly,
security and correctness fixes can silently diverge unless a test runs the same
scenarios through both implementations.

This file keeps only the parity surface that is still live after retiring the
old directory-pointer form repo-wide: `materialize_uv_editable_ref_into()` and
its supporting tree-comparison behavior. Legacy directory-pointer coverage stays
in `test_trusted_pointer_materializer.py`.
"""
from __future__ import annotations

import importlib.util
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
    assert spec.loader is not None
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


def _uv_editable_consumer(root: Path, *, lib: str, raw_path: str) -> Path:
    consumer_dir = root / "consumer"
    consumer_dir.mkdir(parents=True)
    (consumer_dir / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "0.0.0"\n'
        "\n[tool.uv.sources]\n"
        f'agent-{lib} = {{ path = "{raw_path}", editable = true }}\n',
        encoding="utf-8",
    )
    return consumer_dir


def _outcome(log: list[str]) -> str:
    assert len(log) == 1, log
    line = log[0]
    if line.startswith("OK"):
        return "ok"
    assert line.startswith("SKIP"), line
    if "is a symlink" in line:
        return "skip: is a symlink"
    # Split on ": " (colon-SPACE), not a bare ":" -- every SKIP message is
    # built as f"SKIP {path}: {reason}", and on Windows {path} starts with
    # a drive letter ("D:\...") whose colon has no following space.
    # Splitting on a bare ":" lands on THAT colon instead of the real
    # "path: reason" separator, leaving the rest of the (platform- and
    # scenario-specific -- trusted vs canonical builds under different tmp
    # subdirectories) absolute path IN the compared string and making two
    # otherwise-identical reasons compare unequal on Windows only.
    return f"skip: {line.split(': ', 1)[1].strip()}"


def _run_uv_editable_scenario(tmp_path: Path, canonical_mod, *, name: str, build) -> tuple[str, str]:
    trusted_root = tmp_path / f"{name}-trusted" / "repo"
    build(trusted_root)
    trusted_dest = tmp_path / f"{name}-trusted" / "slot"
    shutil.copytree(trusted_root / "consumer", trusted_dest)
    trusted_log = trusted.materialize_uv_editable_ref_into(
        source_consumer_dir=trusted_root / "consumer",
        dest_consumer_dir=trusted_dest,
        canonical_root=trusted_root,
        dest_root=trusted_dest,
    )

    canonical_root = tmp_path / f"{name}-canonical" / "repo"
    build(canonical_root)
    canonical_dest = tmp_path / f"{name}-canonical" / "slot"
    shutil.copytree(canonical_root / "consumer", canonical_dest)
    canonical_log = canonical_mod.materialize_uv_editable_ref_into(
        source_consumer_dir=canonical_root / "consumer",
        dest_consumer_dir=canonical_dest,
        canonical_root=canonical_root,
        dest_root=canonical_dest,
    )

    return _outcome(trusted_log), _outcome(canonical_log)


def test_uv_editable_parity_expands_a_clean_reference_from_canonical(tmp_path: Path, canonical):
    def build(root: Path) -> None:
        _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
        _uv_editable_consumer(root, lib="zdd", raw_path="../libs/zdd")

    got_trusted, got_canonical = _run_uv_editable_scenario(
        tmp_path, canonical, name="uv-clean", build=build
    )
    assert got_trusted == got_canonical == "ok"


def test_uv_editable_parity_refuses_a_reference_missing_editable_true(tmp_path: Path, canonical):
    def build(root: Path) -> None:
        _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
        consumer_dir = root / "consumer"
        consumer_dir.mkdir(parents=True)
        (consumer_dir / "pyproject.toml").write_text(
            '[project]\nname = "consumer"\nversion = "0.0.0"\n'
            "\n[tool.uv.sources]\n"
            'agent-zdd = { path = "../libs/zdd" }\n',
            encoding="utf-8",
        )

    got_trusted, got_canonical = _run_uv_editable_scenario(
        tmp_path, canonical, name="uv-missing-editable", build=build
    )
    assert got_trusted == got_canonical
    assert "missing editable" in got_trusted


def test_uv_editable_parity_refuses_a_symlinked_canonical(tmp_path: Path, canonical):
    def build(root: Path) -> None:
        canon = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
        outside = root.parent / "uv-outside-target"
        (outside / "zdd").mkdir(parents=True)
        (outside / "zdd" / "__init__.py").write_text("smuggled = True\n", encoding="utf-8")
        shutil.rmtree(canon)
        canon.symlink_to(outside / "zdd", target_is_directory=True)
        _uv_editable_consumer(root, lib="zdd", raw_path="../libs/zdd")

    got_trusted, got_canonical = _run_uv_editable_scenario(
        tmp_path, canonical, name="uv-symlinked-canonical", build=build
    )
    assert got_trusted == got_canonical == "skip: is a symlink"


def test_uv_editable_parity_fixes_up_a_nested_canonical_dependency(tmp_path: Path, canonical):
    def _build(root: Path) -> Path:
        procutil = _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
        egg = procutil / "src" / "agent_procutil.egg-info"
        egg.mkdir(parents=True)
        (egg / "PKG-INFO").write_text("generated metadata\n", encoding="utf-8")
        venv = procutil / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "marker.txt").write_text("generated venv\n", encoding="utf-8")
        dep_dir = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
        (dep_dir / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0.1.0-dev1"\n'
            'dependencies = ["agent-procutil"]\n'
            "\n[tool.uv.sources]\n"
            'agent-procutil = { path = "../agent-procutil", editable = true }\n',
            encoding="utf-8",
        )
        consumer_dir = root / "consumer"
        consumer_dir.mkdir(parents=True)
        (consumer_dir / "pyproject.toml").write_text(
            '[project]\nname = "consumer"\nversion = "0.0.0"\n'
            "\n[tool.uv.sources]\n"
            'agent-zdd = { path = "../libs/zdd", editable = true }\n',
            encoding="utf-8",
        )
        return consumer_dir

    trusted_root = tmp_path / "nested-trusted" / "repo"
    trusted_consumer = _build(trusted_root)
    trusted_log = trusted.materialize_uv_editable_ref_into(
        source_consumer_dir=trusted_consumer,
        dest_consumer_dir=trusted_consumer,
        canonical_root=trusted_root,
        dest_root=trusted_consumer,
    )

    canonical_root = tmp_path / "nested-canonical" / "repo"
    canonical_consumer = _build(canonical_root)
    canonical_log = canonical.materialize_uv_editable_ref_into(
        source_consumer_dir=canonical_consumer,
        dest_consumer_dir=canonical_consumer,
        canonical_root=canonical_root,
        dest_root=canonical_consumer,
    )

    for log in (trusted_log, canonical_log):
        assert all(not line.startswith("SKIP") for line in log), log

    for consumer in (trusted_consumer, canonical_consumer):
        nested_pp = (consumer / "libs/zdd/pyproject.toml").read_text()
        assert 'agent-procutil = { path = "../agent-procutil" }' in nested_pp
        assert "editable" not in nested_pp
        assert (consumer / "libs/agent-procutil/src/agent_procutil/__init__.py").read_text() == (
            "real = True\n"
        )
        assert not (consumer / "libs/agent-procutil/src/agent_procutil.egg-info").exists()
        assert not (consumer / "libs/agent-procutil/.venv").exists()


def test_lib_tree_matches_parity_ignores_only_relative_build_dir_names(tmp_path: Path, canonical):
    build_root = tmp_path / "build" / "checkout"
    a = _canonical_lib(build_root, "zdd", version="0.1.0", content="x = 1\n")
    b = build_root / "libs" / "zdd-b"
    (b / "src" / "zdd").mkdir(parents=True)
    (b / "src" / "zdd" / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    (b / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")

    assert trusted._lib_tree_matches(a, b) is False
    assert canonical.uer.lib_tree_matches(a, b) is False

    c = build_root / "libs" / "zdd-c"
    (c / "src" / "zdd").mkdir(parents=True)
    (c / "src" / "zdd" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (c / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    (a / "build").mkdir()
    (a / "build" / "stray.txt").write_text("stray build artifact\n", encoding="utf-8")
    assert trusted._lib_tree_matches(a, c) is True
    assert canonical.uer.lib_tree_matches(a, c) is True


def test_uv_editable_parity_ignores_egg_info_in_comparison_and_materialization(tmp_path: Path, canonical):
    def build(root: Path) -> None:
        canon = _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
        egg = canon / "src" / "agent_zdd.egg-info"
        egg.mkdir(parents=True)
        (egg / "PKG-INFO").write_text("generated metadata\n", encoding="utf-8")
        venv = canon / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "marker.txt").write_text("generated venv\n", encoding="utf-8")
        _uv_editable_consumer(root, lib="zdd", raw_path="../libs/zdd")

    got_trusted, got_canonical = _run_uv_editable_scenario(
        tmp_path, canonical, name="uv-egg-info", build=build
    )
    assert got_trusted == got_canonical == "ok"

    for dest in (
        tmp_path / "uv-egg-info-trusted" / "slot",
        tmp_path / "uv-egg-info-canonical" / "slot",
    ):
        assert not (dest / "libs/zdd/src/agent_zdd.egg-info").exists()
        assert not (dest / "libs/zdd/.venv").exists()
