"""Producer observers see final materialized bytes before slot publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from worktree_manager.self_install_payload import _copy_payload


@pytest.mark.parametrize("legacy", [False, True])
def test_observer_covers_materialization_rewrites_and_completion(
    tmp_path: Path, legacy: bool,
) -> None:
    source = tmp_path / "repo"
    payload = source / "worktree-manager"
    package = payload / "src/worktree_manager"
    package.mkdir(parents=True)
    for name in ("__init__.py", "__main__.py"):
        (package / name).write_text("value = 1\n")
    canonical = source / "libs/first"
    (canonical / "src/first").mkdir(parents=True)
    (canonical / "src/first/__init__.py").write_text("first = 1\n")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "first"\nversion = "2"\n'
        '[tool.uv.sources]\nsecond = { path = "../second", editable = true }\n',
    )
    second = source / "libs/second"
    (second / "src/second").mkdir(parents=True)
    (second / "src/second/__init__.py").write_text("second = 2\n")
    (second / "pyproject.toml").write_text('[project]\nname = "second"\nversion = "1"\n')
    if legacy:
        pointer = payload / "libs/first"
        (pointer / "src").mkdir(parents=True)
        (pointer / "src/stale.py").write_text("stale = True\n")
        (pointer / "pyproject.toml").write_text('[project]\nversion = "1"\n')
        (pointer / "VENDOR_POINTER.json").write_text(
            json.dumps({"source": "libs/first"}),
        )
        (payload / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    else:
        (payload / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\n'
            '[tool.uv.sources]\nfirst = { path = "../libs/first", editable = true }\n'
            'alias = { path = "../libs/first", editable = true }\n',
        )
    slot = tmp_path / "slot"
    observed: dict[str, str] = {}
    writes: list[str] = []

    def produced(relative: Path, staged: Path) -> None:
        assert not slot.exists()
        observed[relative.as_posix()] = hashlib.sha256(staged.read_bytes()).hexdigest()
        writes.append(relative.as_posix())

    _copy_payload(payload, slot, on_file=produced)
    actual = {
        path.relative_to(slot).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in slot.rglob("*") if path.is_file()
    }
    assert actual == {name: observed[name] for name in actual}
    assert writes[-1] == ".install-complete"
    if legacy:
        assert "libs/first/src/stale.py" not in actual
        assert "libs/first/VENDOR_POINTER.json" not in actual
        assert writes.count("libs/first/pyproject.toml") == 2
    else:
        assert writes.count("pyproject.toml") == 3
        assert writes.count("libs/first/pyproject.toml") == 2
        assert "libs/second/src/second/__init__.py" in actual


def test_observer_failure_does_not_replace_existing_slot(tmp_path: Path) -> None:
    payload, slot = tmp_path / "payload", tmp_path / "slot"
    payload.mkdir()
    (payload / "file").write_text("incoming")
    slot.mkdir()
    (slot / "old").write_text("retained")

    def refuse(relative: Path, staged: Path) -> None:
        raise RuntimeError("observation failed")

    with pytest.raises(RuntimeError, match="observation failed"):
        _copy_payload(payload, slot, on_file=refuse)
    assert [path.name for path in slot.iterdir()] == ["old"]
    assert (slot / "old").read_text() == "retained"
    assert not list(tmp_path.glob("slot.staging.*"))
