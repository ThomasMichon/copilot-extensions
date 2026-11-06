"""Tests for ``agent-worktrees python-cache prune``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_worktrees import python_cache_prune as prune

OLD = time.time() - 3 * 3600


def _bytecode_dir(root: Path, name: str, *, mtime: float = OLD) -> Path:
    target = root / name
    leaf = target / "C" / "pkg" / "__pycache__"
    leaf.mkdir(parents=True)
    (leaf / "mod.cpython-313.pyc").write_bytes(b"x" * 100)
    (leaf / "other.cpython-313.opt-1.pyc").write_bytes(b"y" * 20)
    for path in [*target.rglob("*"), target]:
        os.utime(path, (mtime, mtime))
    return target


def _statuses(report: prune.PruneReport) -> dict[str, tuple[str, str]]:
    return {Path(c.path).name: (c.status, c.reason) for c in report.candidates}


def test_dry_run_reports_without_removing(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "a" * 32)

    report = prune.prune_unsealed(tmp_path)

    assert _statuses(report) == {target.name: ("eligible", "")}
    assert report.summary()["bytes"] == 120
    assert target.is_dir()


def test_apply_removes_only_unsealed_bytecode(tmp_path: Path) -> None:
    unsealed = _bytecode_dir(tmp_path, "unsealed-" + "b" * 32)
    installed = _bytecode_dir(tmp_path, "installed-" + "c" * 64)
    other = _bytecode_dir(tmp_path, "unrelated")

    report = prune.prune_unsealed(tmp_path, apply=True)

    assert _statuses(report) == {unsealed.name: ("removed", "")}
    assert not unsealed.exists()
    assert installed.is_dir() and other.is_dir()


def test_non_bytecode_content_is_preserved(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "d" * 32)
    stray = target / "C" / "notes.txt"
    stray.write_text("keep", encoding="utf-8")
    os.utime(stray, (OLD, OLD))

    report = prune.prune_unsealed(tmp_path, apply=True)

    assert _statuses(report) == {target.name: ("skipped", "non-bytecode-content")}
    assert stray.is_file()


def test_young_directory_is_preserved(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "e" * 32)
    fresh = target / "C" / "pkg" / "__pycache__" / "mod.cpython-313.pyc"
    os.utime(fresh, None)

    report = prune.prune_unsealed(tmp_path, apply=True)

    assert _statuses(report) == {target.name: ("skipped", "younger-than-grace-period")}
    assert fresh.is_file()


def _link_directory(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("nested", [False, True])
def test_reparse_points_are_never_followed(tmp_path: Path, nested: bool) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = _bytecode_dir(tmp_path, "outside")
    if nested:
        holder = _bytecode_dir(cache, "unsealed-" + "f" * 32)
        _link_directory(holder / "C" / "link", outside)
        name = holder.name
    else:
        name = "unsealed-" + "9" * 32
        _link_directory(cache / name, outside)

    report = prune.prune_unsealed(cache, apply=True, now=time.time() + 7200)

    assert _statuses(report) == {name: ("skipped", "reparse-point")}
    assert (outside / "C" / "pkg" / "__pycache__" / "mod.cpython-313.pyc").is_file()
    assert (cache / name).exists()


def test_removal_error_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "1" * 32)

    def deny(path: str) -> None:
        raise PermissionError(13, "denied", path)

    monkeypatch.setattr(prune.os, "unlink", deny)
    report = prune.prune_unsealed(tmp_path, apply=True)

    status, reason = _statuses(report)[target.name]
    assert status == "error" and reason.startswith("PermissionError")
    assert target.is_dir()


def _args(root: Path, *extra: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    prune.add_parsers(parser.add_subparsers(dest="command"))
    return parser.parse_args(["python-cache", "prune", "--root", str(root), *extra])


def test_cli_json_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _bytecode_dir(tmp_path / "cache" / "python", "unsealed-" + "2" * 32)

    assert prune.cmd_python_cache(_args(tmp_path, "--json")) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["apply"] is False
    assert payload["roots"][0]["summary"]["eligible"] == 1
    assert target.is_dir()


def test_cli_apply_requires_grace_period(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _bytecode_dir(tmp_path / "cache" / "python", "unsealed-" + "3" * 32)

    assert prune.cmd_python_cache(_args(tmp_path, "--apply", "--min-age-hours", "0.5")) == 2
    assert target.is_dir()
    assert "--min-age-hours" in capsys.readouterr().err
