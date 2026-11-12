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
PROVEN = ("process-scan", ())


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

    report = prune.prune_unsealed(tmp_path, liveness=PROVEN)

    assert _statuses(report) == {target.name: ("eligible", "")}
    assert report.summary()["bytes"] == 120
    assert target.is_dir()


def test_apply_removes_only_unsealed_bytecode(tmp_path: Path) -> None:
    unsealed = _bytecode_dir(tmp_path, "unsealed-" + "b" * 32)
    installed = _bytecode_dir(tmp_path, "installed-" + "c" * 64)
    other = _bytecode_dir(tmp_path, "unrelated")

    report = prune.prune_unsealed(tmp_path, apply=True, liveness=PROVEN)

    assert _statuses(report) == {unsealed.name: ("removed", "")}
    assert not unsealed.exists()
    assert installed.is_dir() and other.is_dir()


def test_non_bytecode_content_is_preserved(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "d" * 32)
    stray = target / "C" / "notes.txt"
    stray.write_text("keep", encoding="utf-8")
    os.utime(stray, (OLD, OLD))

    report = prune.prune_unsealed(tmp_path, apply=True, liveness=PROVEN)

    assert _statuses(report) == {target.name: ("skipped", "non-bytecode-content")}
    assert stray.is_file()


def test_young_directory_is_preserved(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "e" * 32)
    fresh = target / "C" / "pkg" / "__pycache__" / "mod.cpython-313.pyc"
    os.utime(fresh, None)

    report = prune.prune_unsealed(tmp_path, apply=True, liveness=PROVEN)

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

    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "denied")

    if prune.os.unlink in prune.os.supports_dir_fd:
        monkeypatch.setattr(prune.os, "supports_dir_fd", prune.os.supports_dir_fd | {deny})
    monkeypatch.setattr(prune.os, "unlink", deny)
    report = prune.prune_unsealed(tmp_path, apply=True, liveness=PROVEN)

    status, reason = _statuses(report)[target.name]
    assert status == "error" and reason.startswith("PermissionError")
    assert target.is_dir()


def _args(root: Path | None, *extra: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    prune.add_parsers(parser.add_subparsers(dest="command"))
    roots = ["--root", str(root)] if root is not None else []
    return parser.parse_args(["python-cache", "prune", *roots, *extra])


def test_cli_json_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prune, "live_unsealed_names", lambda: ("process-scan", set()))
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


def test_non_finite_age_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _bytecode_dir(tmp_path / "cache" / "python", "unsealed-" + "4" * 32)

    for value in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValueError):
            prune.prune_unsealed(target.parent, apply=True, min_age_seconds=value)
    assert prune.cmd_python_cache(_args(tmp_path, "--apply", "--min-age-hours", "nan")) == 2
    assert target.is_dir()
    assert "--min-age-hours" in capsys.readouterr().err


def test_root_scan_error_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(prune.os, "scandir", deny)
    report = prune.prune_unsealed(tmp_path, apply=True, liveness=PROVEN)

    assert [(c.status, c.reason.split(":")[0]) for c in report.candidates] == [("error", "PermissionError")]


@pytest.mark.parametrize("error", [PermissionError(13, "denied"), OSError(5, "I/O failure")])
def test_default_discovery_reports_inaccessible_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    error: OSError,
) -> None:
    cache = tmp_path / ".agent-example" / "cache" / "python"
    target = _bytecode_dir(cache, "unsealed-" + "a" * 32)
    (tmp_path / ".agent-empty").mkdir()
    (tmp_path / ".agent-file").write_text("not a runtime", encoding="utf-8")
    original = os.stat

    def inspect(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(prune._display(os.fspath(path))) == cache:
            raise error
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(prune.os, "stat", inspect)
    monkeypatch.setattr(prune, "live_unsealed_names", lambda: PROVEN)

    assert prune.cmd_python_cache(_args(None, "--apply", "--json")) == 1

    payload = json.loads(capsys.readouterr().out)
    report, = payload["roots"]
    assert Path(report["cache_root"]) == cache
    assert report["summary"]["error"] == 1
    candidate, = report["candidates"]
    assert candidate["reason"].startswith(type(error).__name__)
    assert target.is_dir()


def test_linked_runtime_cache_root_is_skipped(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    target = _bytecode_dir(real / "python", "unsealed-" + "5" * 32)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _link_directory(runtime / "cache", real)

    report = prune.prune_unsealed(runtime / "cache" / "python", apply=True)

    assert [(c.status, c.reason) for c in report.candidates] == [
        ("skipped", "cache-root-linked-or-not-directory")]
    assert target.is_dir()


def test_replacement_during_removal_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    target = _bytecode_dir(cache, "unsealed-" + "6" * 32)
    outside = _bytecode_dir(tmp_path, "outside")
    victim = outside / "C" / "pkg" / "__pycache__" / "mod.cpython-313.pyc"

    def swap(candidate: str) -> None:
        package = Path(candidate) / "C" / "pkg"
        moved = Path(candidate) / "C" / "moved"
        package.rename(moved)
        _link_directory(package, outside / "C" / "pkg")

    monkeypatch.setattr(prune, "_before_remove", swap)
    report = prune.prune_unsealed(cache, apply=True, liveness=PROVEN)

    assert _statuses(report)[target.name][0] == "error"
    assert victim.is_file()


def test_old_but_live_directory_is_preserved(tmp_path: Path) -> None:
    live = _bytecode_dir(tmp_path, "unsealed-" + "7" * 32)
    idle = _bytecode_dir(tmp_path, "unsealed-" + "8" * 32)

    report = prune.prune_unsealed(tmp_path, apply=True, liveness=("process-scan", {live.name}))

    assert _statuses(report) == {
        live.name: ("skipped", "referenced-by-live-process"), idle.name: ("removed", "")}
    assert report.as_dict()["liveness"] == "process-scan"
    assert live.is_dir() and not idle.exists()


def test_unknown_liveness_retains_everything(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "0" * 32)

    report = prune.prune_unsealed(tmp_path, apply=True, liveness=("unknown", None))

    assert _statuses(report) == {target.name: ("skipped", "liveness-unknown")}
    assert target.is_dir()


def test_real_cli_runs_from_neutral_directory(tmp_path: Path) -> None:
    import subprocess

    neutral = tmp_path / "neutral"
    neutral.mkdir()
    target = _bytecode_dir(tmp_path / "rt" / "cache" / "python", "unsealed-" + "c" * 32)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(Path(prune.__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")])}
    kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

    result = subprocess.run(
        [sys.executable, "-B", "-m", "agent_worktrees", "python-cache", "prune",
         "--root", str(tmp_path / "rt"), "--allow-age-only", "--json"],
        cwd=neutral, env=env, capture_output=True, text=True, timeout=60, **kwargs,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["apply"] is False
    assert payload["roots"][0]["summary"]["eligible"] == 1
    assert target.is_dir()


@pytest.mark.parametrize("mode", ["unavailable", "incomplete"])
def test_unproven_liveness_retains_unless_age_only_is_allowed(tmp_path: Path, mode: str) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "d" * 32)

    retained = prune.prune_unsealed(tmp_path, apply=True, liveness=(mode, set()))
    assert _statuses(retained) == {target.name: ("skipped", "liveness-unproven")}
    assert target.is_dir()

    removed = prune.prune_unsealed(tmp_path, apply=True, liveness=(mode, set()), allow_age_only=True)
    assert _statuses(removed) == {target.name: ("removed", "")}
    assert removed.as_dict()["age_only_allowed"] is True


def test_allow_age_only_never_overrides_failed_scan(tmp_path: Path) -> None:
    target = _bytecode_dir(tmp_path, "unsealed-" + "e" * 32)

    report = prune.prune_unsealed(tmp_path, apply=True, liveness=("unknown", None), allow_age_only=True)

    assert _statuses(report) == {target.name: ("skipped", "liveness-unknown")}
    assert target.is_dir()


def _fake_proc(root: Path, entries: dict[str, bytes | None]) -> Path:
    for pid, environ in entries.items():
        (root / pid).mkdir(parents=True)
        (root / pid / "environ").write_bytes(environ or b"")
        (root / pid / "cmdline").write_bytes(b"python\0")
    return root


@pytest.mark.parametrize("name", ["unsealed-123-456-789-101", "unsealed-123-456-789-101-"])
def test_process_scan_matches_hyphenated_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    proc = _fake_proc(tmp_path / "proc", {"1": f"PYTHONPYCACHEPREFIX=/c/{name}\0".encode()})
    monkeypatch.setattr(prune.sys, "platform", "linux")

    mode, names = prune.live_unsealed_names(str(proc))
    assert (mode, names) == ("process-scan", {name})

    cache = tmp_path / "cache"
    live = _bytecode_dir(cache, name)
    report = prune.prune_unsealed(cache, apply=True, liveness=(mode, names))
    assert _statuses(report) == {name: ("skipped", "referenced-by-live-process")}
    assert live.is_dir()


def test_denied_process_read_makes_scan_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _fake_proc(tmp_path / "proc", {"1": b"A=b\0", "2": None})
    denied = proc / "2" / "environ"
    original = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self == denied:
            raise PermissionError(13, "denied")
        return original(self)

    monkeypatch.setattr(prune.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    assert prune.live_unsealed_names(str(proc)) == ("incomplete", set())