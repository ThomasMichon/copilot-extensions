"""Manager's default pytest collection owns tests/, not embedded library suites."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import materialize_main as mm


REPO = Path(__file__).resolve().parent.parent
MANAGER = REPO / "worktree-manager"


def _files(root: Path, *, apply_materializer_ignore: bool = False) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and not (
            apply_materializer_ignore
            and any(mm._ignore("", [part]) for part in path.relative_to(root).parts)
        )
    }


def _development_pyproject(consumer: Path) -> str:
    text = (MANAGER / "pyproject.toml").read_text(encoding="utf-8")
    sources = mm.uer.tomllib.loads(text)["tool"]["uv"]["sources"]
    span = mm.uer.uv_sources_table_span(text)
    assert sources and span is not None
    entries = []
    for name, entry in sources.items():
        lib = Path(entry["path"]).name
        assert mm.uer.is_safe_lib_name(lib)
        canonical = REPO / "libs" / lib
        assert (canonical / "pyproject.toml").is_file()
        path = Path(os.path.relpath(canonical, consumer)).as_posix()
        entries.append(f"{json.dumps(name)} = {{ path = {json.dumps(path)}, editable = true }}")
    start, end = span
    return text[:start] + "\n" + "\n".join(entries) + "\n\n" + text[end:]


def test_canonical_bytes_follow_materializer_ignore_at_every_component(tmp_path: Path):
    canonical = tmp_path / "canonical"
    owned = canonical / "src" / "package" / "owned.py"
    owned.parent.mkdir(parents=True)
    owned.write_bytes(b"VALUE = 1\n")
    ignored = (
        "src/package.egg-info/PKG-INFO",
        "src/package/__pycache__/owned.pyc",
        "tests/build/nested/test_generated.py",
        "tests/.pytest_cache/v/cache/nodeids",
    )
    for relative in ignored:
        path = canonical / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"generated metadata\n")
    copied = tmp_path / "copied"
    shutil.copytree(canonical, copied, ignore=mm._ignore)
    assert set(ignored) <= _files(canonical).keys()
    assert _files(copied) == _files(canonical, apply_materializer_ignore=True)
    assert _files(copied) == {"src/package/owned.py": b"VALUE = 1\n"}


def _collect(consumer: Path, libraries: Path) -> tuple[subprocess.CompletedProcess, set[str]]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([
        str(consumer / "src"),
        *[str(lib / "src") for lib in sorted(libraries.iterdir()) if (lib / "src").is_dir()],
    ])
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=consumer,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    ids = {
        line for line in result.stdout.splitlines()
        if line.startswith(("tests/", "libs/")) and "::" in line
    }
    return result, ids


def test_default_collection_keeps_owned_ids_after_real_materialization(tmp_path: Path):
    source = tmp_path / "source" / "worktree-manager"
    source.mkdir(parents=True)
    # Release checkouts already use local libs/ entries. Reconstruct escaping
    # development references so both checkout layouts exercise real expansion.
    (source / "pyproject.toml").write_text(_development_pyproject(source), encoding="utf-8")
    shutil.copytree(MANAGER / "src", source / "src", ignore=mm._ignore)
    (source / "tests").mkdir()
    # Real, dependency-light Manager modules keep this tool regression bounded;
    # the complete Manager portfolio remains the standalone runner's contract.
    for name in ("test_catalog.py", "test_contract_dependency_free.py"):
        shutil.copy2(MANAGER / "tests" / name, source / "tests" / name)

    generated = tmp_path / "generated" / "worktree-manager"
    shutil.copytree(source, generated)
    report = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=source,
        dest_consumer_dir=generated,
        canonical_root=REPO,
    )
    assert report and all(line.startswith("OK") for line in report), report

    embedded = generated / "libs"
    before = _files(embedded)
    # Materialization may rewrite transitive pyproject source tables, but must
    # retain the canonical library code and tests, including the colliding suite.
    for lib in embedded.iterdir():
        for subtree in ("src", "tests"):
            canonical = REPO / "libs" / lib.name / subtree
            if canonical.is_dir():
                assert _files(lib / subtree) == _files(canonical, apply_materializer_ignore=True)
    assert "zdd/tests/test_breadcrumb.py" in before
    assert "zdd/tests/test_claims.py" in before

    source_result, source_ids = _collect(source, REPO / "libs")
    generated_result, generated_ids = _collect(generated, embedded)
    assert source_result.returncode == 0, source_result.stdout + source_result.stderr
    sentinels = {
        "tests/test_catalog.py::test_catalog_loads_and_is_well_formed",
        "tests/test_contract_dependency_free.py::test_worktree_manager_imports_no_plugin_code_statically",
    }
    assert sentinels <= source_ids
    assert sentinels <= generated_ids
    assert not {node for node in generated_ids if node.startswith("libs/")}, (
        generated_result.stdout + generated_result.stderr
    )
    assert generated_result.returncode == 0, generated_result.stdout + generated_result.stderr
    assert generated_ids == source_ids
    assert _files(embedded) == before
