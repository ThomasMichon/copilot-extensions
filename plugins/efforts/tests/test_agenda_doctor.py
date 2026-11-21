"""Report-only diagnosis and lossless, retryable checkout-source retirement."""

from __future__ import annotations

from typing import Any

import importlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

FIXTURE = importlib.import_module("test_agenda_rescue")
lane, packet, rows, source_bytes, STORE, outcome = (
    getattr(FIXTURE, name) for name in ("lane", "packet", "rows", "source_bytes", "STORE", "outcome")
)

DOCTOR = importlib.import_module("agenda_doctor")
pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


def repair(packet: dict[str, Any]) -> Any:
    return DOCTOR.repair(packet["source"], packet["export"], **packet["arguments"])


def test_report_is_read_only_and_retirement_preserves_complete_history(packet: dict[str, Any]) -> None:
    before = source_bytes(packet["source"])
    report = DOCTOR.inspect(packet["source"], worktree=packet["arguments"]["worktree"])
    assert report["state"] == "legacy-local" and report["revision"] == 3
    assert source_bytes(packet["source"]) == before
    expected = rows(packet["source"])
    result = repair(packet)
    assert result["source_retired"] and not packet["source"].exists()
    retired = Path(result["retired_home"])
    assert rows(retired) == rows(packet["target"]) == expected
    assert result == repair(packet)
    outcome(packet["target"])
    assert repair(packet)["revision"] == 4
    with pytest.raises(STORE.AgendaError, match="unsupported"):
        STORE.read(retired)


@pytest.mark.parametrize("damage", ["healthy", "missing", "corrupt", "foreign", "history-gap"])
def test_external_report_validates_published_store_without_writes(packet: dict[str, Any], damage: str) -> None:
    repair(packet)
    target = packet["target"]
    expected_store = packet["arguments"]["store_id"]
    if damage == "missing":
        (target / STORE.DATABASE).unlink()
    elif damage == "corrupt":
        (target / STORE.DATABASE).write_bytes(b"not a database")
    elif damage == "foreign":
        expected_store = "b" * 32
    elif damage == "history-gap":
        with sqlite3.connect(target / STORE.DATABASE) as db:
            db.execute("DELETE FROM operations WHERE revision=1")
        assert STORE.read(target)["revision"] == 3
    before = source_bytes(target)
    result = DOCTOR.inspect_external(
        target, worktree=packet["arguments"]["worktree"], store_id=expected_store,
    )
    assert result["state"] == {
        "healthy": "external-correct", "missing": "external-missing",
        "corrupt": "external-corrupt", "foreign": "external-corrupt",
        "history-gap": "external-corrupt",
    }[damage]
    assert source_bytes(target) == before


def test_unknown_source_members_hold_before_any_cutover(packet: dict[str, Any]) -> None:
    (packet["source"] / "unknown.bin").write_bytes(b"retain this evidence")
    before = source_bytes(packet["source"]), source_bytes(packet["target"])
    with pytest.raises(STORE.AgendaError, match="unrelated files"):
        repair(packet)
    assert (source_bytes(packet["source"]), source_bytes(packet["target"])) == before
    assert STORE.read(packet["source"])["revision"] == 3


def test_interrupted_first_export_retains_partial_and_recovers_before_cutover(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = rows(packet["source"])
    original = source_bytes(packet["source"])
    partial = packet["export"].with_name("initial-export")
    partial.mkdir()
    packet["export"] = partial

    def interrupted(*args: Any) -> None:
        raise OSError("fixture initial export interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(DOCTOR.export, "_backup", interrupted)
        with pytest.raises(STORE.AgendaError, match="partial destination retained"):
            repair(packet)
    retained = source_bytes(partial)
    assert retained and source_bytes(packet["source"]) == original
    assert list(packet["target"].iterdir()) == []
    with pytest.raises(STORE.AgendaError, match="initial export is incomplete"):
        repair(packet)
    assert source_bytes(partial) == retained
    assert source_bytes(packet["source"]) == original
    fresh = partial.with_name("fresh-export")
    fresh.mkdir()
    packet["export"] = fresh
    assert repair(packet)["source_retired"]
    assert rows(packet["target"]) == expected
    assert source_bytes(partial) == retained


def test_missing_published_export_after_fencing_requires_exact_recovery(
    packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = repair(packet)
    saved = packet["export"]
    (saved / DOCTOR.export.MANIFEST).rename(saved / "retained-manifest")
    retired = Path(result["retired_home"])
    before = source_bytes(saved), source_bytes(retired), source_bytes(packet["target"])
    monkeypatch.setattr(
        DOCTOR.managed, "allocate", lambda *args: pytest.fail("missing export allocated a store"),
    )
    with pytest.raises(STORE.AgendaError, match="restore the exact verified export"):
        repair(packet)
    assert (source_bytes(saved), source_bytes(retired), source_bytes(packet["target"])) == before


def test_interrupted_retirement_retries_without_rolling_back_new_work(packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.rename
    with monkeypatch.context() as patch:
        def interrupted(path: Path, target: Any) -> Any:
            if path == packet["source"]:
                raise OSError("fixture retirement interrupted")
            return original(path, target)
        patch.setattr(Path, "rename", interrupted)
        with pytest.raises(STORE.AgendaError, match="retirement interrupted"):
            repair(packet)
    report = DOCTOR.inspect(packet["source"], worktree=packet["arguments"]["worktree"])
    assert report["state"] == "interrupted"
    outcome(packet["target"])
    assert repair(packet)["revision"] == 4


def test_post_rename_barrier_failure_keeps_exact_retry_recovery(packet: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    sync = DOCTOR.export._sync_directory
    with monkeypatch.context() as patch:
        def interrupted(path: Path, fd: Any) -> Any:
            if path == packet["source"].parent and not packet["source"].exists():
                raise OSError("fixture retirement barrier interrupted")
            sync(path, fd)
        patch.setattr(DOCTOR.export, "_sync_directory", interrupted)
        with pytest.raises(STORE.AgendaError, match="retirement barrier interrupted"):
            repair(packet)
    assert not packet["source"].exists()
    assert repair(packet)["source_retired"]


def test_conflicting_retired_source_is_not_replaced(packet: dict[str, Any]) -> None:
    retired = packet["export"].with_name(packet["export"].name + "." + DOCTOR.RETIRED)
    retired.mkdir()
    with pytest.raises(STORE.AgendaError, match="both original and retired"):
        repair(packet)
    assert STORE.read(packet["source"])["revision"] == 3


def test_default_container_report_lists_exact_lane_without_relocation(packet: dict[str, Any]) -> None:
    container = packet["source"].parent / ".effort-agenda"
    container.mkdir()
    source = container / "main"
    packet["source"].rename(source)
    before = source_bytes(source)
    report = DOCTOR.inspect(container, worktree=packet["arguments"]["worktree"])
    assert report["state"] == "legacy-local" and report["repairable"] is False
    assert report["lanes"][0]["source_home"] == str(source)
    assert report["lanes"][0]["revision"] == 3
    assert source_bytes(source) == before


@pytest.mark.effect("process")
def test_exact_retry_survives_removal_of_the_original_checkout(packet: dict[str, Any]) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable")
    anchor = packet["source"].parent / "anchor"
    hooks = packet["source"].parent / "empty-hooks"
    anchor.mkdir()
    hooks.mkdir()
    def command(*arguments: Any) -> Any:
        subprocess.run(
            [git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", f"core.hooksPath={hooks}", *arguments],
            cwd=anchor, check=True, capture_output=True, text=True, timeout=30,
            env={**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_TERMINAL_PROMPT": "0"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    command("init", "-q")
    command("commit", "-q", "--allow-empty", "-m", "Fixture base")
    checkout = packet["source"].parent / "checkout"
    command("worktree", "add", "-q", "-b", "fixture-retirement", str(checkout))
    container = checkout / ".effort-agenda"
    container.mkdir(parents=True)
    source = container / "main"
    packet["source"].rename(source)
    packet["source"] = source
    expected = rows(source)
    result = repair(packet)
    container.rmdir()
    command("worktree", "remove", str(checkout))
    assert repair(packet) == result
    command("worktree", "add", "-q", str(checkout), "fixture-retirement")
    assert STORE.read(packet["target"])["revision"] == 3
    assert rows(packet["target"]) == expected
    assert not (checkout / ".effort-agenda").exists()
    command("worktree", "remove", str(checkout))
