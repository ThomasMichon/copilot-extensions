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


# ── `uv`-editable canonical-reference parity ─────────────────────────────
#
# Mirrors the directory-pointer scenarios above, but exercises
# ``materialize_uv_editable_ref_into()`` -- the mechanism that superseded
# the directory-pointer form (vendor-pointer-generalization effort, second
# course correction) and is what self_install.py's own
# ``_materialize_payload_pointers`` now relies on for a consumer whose
# vendored dependency is a `uv`-editable reference, not a
# ``VENDOR_POINTER.json`` copy.


def _uv_editable_consumer(root: Path, *, lib: str, raw_path: str) -> Path:
    """A single-consumer tree at ``root / "consumer"`` whose
    ``pyproject.toml`` declares an escaping `uv`-editable
    ``[tool.uv.sources]`` entry for ``lib`` via ``raw_path``."""
    consumer_dir = root / "consumer"
    consumer_dir.mkdir(parents=True)
    (consumer_dir / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "0.0.0"\n'
        "\n[tool.uv.sources]\n"
        f'agent-{lib} = {{ path = "{raw_path}", editable = true }}\n',
        encoding="utf-8",
    )
    return consumer_dir


def _run_uv_editable_scenario(tmp_path: Path, canonical_mod, *, name: str, build) -> tuple[str, str]:
    """``build(root)`` populates one scenario's canonical lib + consumer
    manifest under a fresh root; returns the (trusted, canonical) outcome
    categories for identical inputs, both run through
    ``materialize_uv_editable_ref_into`` with a fresh ``dest`` copy of the
    consumer (mirroring self_install.py's own payload/slot split)."""
    trusted_root = tmp_path / f"{name}-trusted" / "repo"
    build(trusted_root)
    trusted_dest = tmp_path / f"{name}-trusted" / "slot"
    shutil.copytree(trusted_root / "consumer", trusted_dest)
    trusted_log = trusted.materialize_uv_editable_ref_into(
        source_consumer_dir=trusted_root / "consumer", dest_consumer_dir=trusted_dest,
        canonical_root=trusted_root, dest_root=trusted_dest,
    )

    canonical_root = tmp_path / f"{name}-canonical" / "repo"
    build(canonical_root)
    canonical_dest = tmp_path / f"{name}-canonical" / "slot"
    shutil.copytree(canonical_root / "consumer", canonical_dest)
    canonical_log = canonical_mod.materialize_uv_editable_ref_into(
        source_consumer_dir=canonical_root / "consumer", dest_consumer_dir=canonical_dest,
        canonical_root=canonical_root, dest_root=canonical_dest,
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
    assert got_trusted == got_canonical
    assert got_trusted == "skip: is a symlink"


def test_uv_editable_parity_fixes_up_a_nested_canonical_dependency(tmp_path: Path, canonical):
    """A canonical lib (e.g. `ssh-manager`) can itself depend on another
    canonical lib (e.g. `agent-procutil`) via its own escaping
    `uv`-editable entry -- found in review (PR #4372). Both implementations
    must fix up that nested entry (materializing the dependency as a
    sibling of the copied lib, then dropping `editable = true` from the
    nested entry) alongside the consumer's own top-level rewrite."""
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
        source_consumer_dir=trusted_consumer, dest_consumer_dir=trusted_consumer,
        canonical_root=trusted_root, dest_root=trusted_consumer,
    )

    canonical_root = tmp_path / "nested-canonical" / "repo"
    canonical_consumer = _build(canonical_root)
    canonical_log = canonical.materialize_uv_editable_ref_into(
        source_consumer_dir=canonical_consumer, dest_consumer_dir=canonical_consumer,
        canonical_root=canonical_root, dest_root=canonical_consumer,
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


def test_lib_tree_matches_parity_ignores_only_relative_build_dir_names(
    tmp_path: Path, canonical,
):
    """Review finding (PR #4383): both `_lib_tree_matches` (trusted) and
    `lib_tree_matches` (canonical `uv_editable_ref.py`, imported here as
    `canonical.uer`) must scope their ignored-directory-name check to the
    path RELATIVE to each tree's own root, never the full absolute path --
    otherwise a checkout merely *located* under an ancestor directory
    happening to be named e.g. `build` would have every file's `.parts`
    match that ancestor, silently emptying the comparison and making two
    genuinely DIFFERENT trees compare as falsely equal."""
    build_root = tmp_path / "build" / "checkout"
    a = _canonical_lib(build_root, "zdd", version="0.1.0", content="x = 1\n")
    b = build_root / "libs" / "zdd-b"
    (b / "src" / "zdd").mkdir(parents=True)
    (b / "src" / "zdd" / "__init__.py").write_text("x = 2\n", encoding="utf-8")  # genuinely differs
    (b / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")

    assert trusted._lib_tree_matches(a, b) is False
    assert canonical.uer.lib_tree_matches(a, b) is False

    # A real build/ SUBDIRECTORY inside the tree is still correctly ignored.
    c = build_root / "libs" / "zdd-c"
    (c / "src" / "zdd").mkdir(parents=True)
    (c / "src" / "zdd" / "__init__.py").write_text("x = 1\n", encoding="utf-8")  # matches a
    (c / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    (a / "build").mkdir()
    (a / "build" / "stray.txt").write_text("stray build artifact\n", encoding="utf-8")
    assert trusted._lib_tree_matches(a, c) is True
    assert canonical.uer.lib_tree_matches(a, c) is True


def test_uv_editable_parity_ignores_egg_info_in_comparison_and_materialization(
    tmp_path: Path, canonical,
):
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
