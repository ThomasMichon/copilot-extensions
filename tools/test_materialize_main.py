"""Tests for tools/materialize_main.py -- the whole-repo pointer materializer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import materialize_main as mm


def _pointer(root: Path, plugin: str, lib: str) -> Path:
    d = root / "plugins" / plugin / "libs" / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": f"libs/{lib}"}) + "\n",
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n',
                                       encoding="utf-8")
    return d


def _canonical_lib(root: Path, lib: str, *, version: str, content: str) -> Path:
    d = root / "libs" / lib
    (d / "src" / lib.replace("-", "_")).mkdir(parents=True, exist_ok=True)
    (d / "src" / lib.replace("-", "_") / "__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n',
                                       encoding="utf-8")
    return d


def test_materialize_expands_pointer_from_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    _pointer(root, "agent-bridge", "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any(line.startswith("OK") for line in log)
    copy_src = root / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py"
    assert copy_src.read_text() == "real = True\n"
    assert not (root / "plugins/agent-bridge/libs/zdd/VENDOR_POINTER.json").exists()
    pp = (root / "plugins/agent-bridge/libs/zdd/pyproject.toml").read_text()
    assert '"0.1.0-dev5"' in pp


def test_materialize_skips_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _pointer(root, "agent-bridge", "ghost-lib")  # no libs/ghost-lib/ exists

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "not found" in line for line in log)
    # Pointer must be left in place since nothing was expanded.
    assert (root / "plugins/agent-bridge/libs/ghost-lib/VENDOR_POINTER.json").exists()


def test_materialize_handles_multiple_pointers_for_same_lib(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev9", content="shared\n")
    for plugin in ("agent-bridge", "agent-codespaces", "agent-mcp"):
        _pointer(root, plugin, "zdd")

    log = mm.materialize(root, canonical_root=root)
    assert sum(1 for line in log if line.startswith("OK")) == 3
    for plugin in ("agent-bridge", "agent-codespaces", "agent-mcp"):
        assert (root / f"plugins/{plugin}/libs/zdd/src/zdd/__init__.py").read_text() == "shared\n"


def test_build_snapshots_then_materializes(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="original\n")
    _pointer(source, "agent-bridge", "zdd")
    (source / "README.md").write_text("hello\n", encoding="utf-8")

    dest = tmp_path / "dest"
    log = mm.build(dest, source_root=source)

    assert any(line.startswith("OK") for line in log)
    # The snapshot copied everything else too, untouched.
    assert (dest / "README.md").read_text() == "hello\n"
    assert (dest / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py").read_text() == "original\n"
    # The source checkout itself must never be mutated by build().
    assert (source / "plugins/agent-bridge/libs/zdd/VENDOR_POINTER.json").exists()


def test_build_overwrites_a_stale_existing_dest(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="fresh\n")
    (source / "marker.txt").write_text("v2\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "stale-leftover.txt").write_text("old build\n", encoding="utf-8")

    mm.build(dest, source_root=source)
    assert not (dest / "stale-leftover.txt").exists()
    assert (dest / "marker.txt").read_text() == "v2\n"


def test_find_pointers_returns_sorted_paths(tmp_path: Path):
    root = tmp_path / "repo"
    _pointer(root, "zeta-plugin", "libA")
    _pointer(root, "alpha-plugin", "libB")

    found = mm.find_pointers(root)
    assert [p.parent.parent.parent.name for p in found] == ["alpha-plugin", "zeta-plugin"]


def test_main_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="x\n")
    _pointer(source, "agent-bridge", "zdd")
    monkeypatch.setattr(mm, "REPO", source)

    dest = tmp_path / "dest"
    code = mm.main(["--dest", str(dest)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Materialized 1 pointer(s)" in out
