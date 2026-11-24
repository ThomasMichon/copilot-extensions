"""Targeted failed-launch disposition with real private queue and lock custody."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, NoReturn

import pytest

import agent_procutil as pu
from single_instance_lease.operation import OperationLock
from update_driver import (
    DispositionError, Request, RequestSpec, SpawnError, TrustedRunner,
    enqueue, ensure_worker, resolve_failed_unstarted, status,
)


def _read(request_id: str, root: Path) -> Request:
    request = status(request_id, root=root)
    assert isinstance(request, Request)
    return request


def _failed(area: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Request]:
    root = area / "state"
    request = enqueue(
        RequestSpec("fixture"), runner=TrustedRunner((sys.executable,), str(area)), root=root,
    )

    def refuse(*args: Any, **kwargs: Any) -> NoReturn:
        raise pu.DaemonSpawnError(
            "fixture launch refusal", process_created=False,
            cleanup_confirmed=True, admission_sent=False,
        )

    monkeypatch.setattr(pu, "spawn_daemon", refuse)
    with pytest.raises(SpawnError, match="worker launch failed"):
        ensure_worker((sys.executable,), root=root)
    return root, _read(request.id, root)


def test_resolution_retains_failure_and_peers_and_allows_a_fresh_attempt(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original = _failed(area, monkeypatch)
    peer = enqueue(RequestSpec("fixture", argv=("peer",)), runner=original.runner, root=root)
    with pytest.raises(DispositionError, match="not a recorded failed-unstarted"):
        resolve_failed_unstarted(peer.id, reason="fixture repair", authorized=True, root=root)
    result = resolve_failed_unstarted(
        original.id, reason="fixture repair", authorized=True, root=root,
    )
    assert result.id == original.id and result.state == "completed" and result.exit_code == 125
    assert result.spec == original.spec and result.runner == original.runner
    assert result.created_at == original.created_at
    assert original.error is not None and result.error is not None and original.error in result.error
    assert "fixture repair" in result.error
    assert result.started_at is None and result.boot_id is None and result.finished_at is not None
    assert result.settled_at is None and result.released_at is None
    assert _read(peer.id, root) == peer
    assert resolve_failed_unstarted(
        original.id, reason="fixture repair", authorized=True, root=root,
    ) == result
    fresh = enqueue(original.spec, runner=original.runner, root=root)
    assert fresh.id != original.id and fresh.state == "pending"


def test_early_worker_exit_is_diagnosed_without_copying_worker_output(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = area / "state"
    request = enqueue(
        RequestSpec("fixture"), runner=TrustedRunner((sys.executable,), str(area)), root=root,
    )
    (root / "worker.log").write_text("earlier attempt: ValueError\n", encoding="utf-8")
    offset = (root / "worker.log").stat().st_size

    class Exited:
        def poll(self) -> int:
            return 1

    def exits(argv: Any, *, log_path: Path, **kwargs: Any) -> Exited:
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write("2026 spawn_daemon cwd=x executable=python.exe\n")
            log.write("token=secret-value\n")
            log.write("python.exe: No module named update_driver\n")
        return Exited()

    monkeypatch.setattr(pu, "spawn_daemon", exits)
    with pytest.raises(SpawnError, match="exit 1; shared worker log since launch records ModuleNotFoundError") as caught:
        ensure_worker((sys.executable,), root=root)
    error = _read(request.id, root).error
    assert error is not None and error == str(caught.value)
    assert error.endswith(
        f"exit 1; shared worker log since launch records ModuleNotFoundError (update_driver); "
        f"see {root / 'worker.log'} from byte {offset}",
    )
    assert "secret" not in error and "python.exe" not in error

    def refused(argv: Any, *, log_path: Path, **kwargs: Any) -> Exited:
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write("another worker: RuntimeError\n")
        raise SpawnError("Windows refused the required breakaway process")

    monkeypatch.setattr(pu, "spawn_daemon", refused)
    with pytest.raises(SpawnError) as caught:
        ensure_worker((sys.executable,), root=root)
    assert str(caught.value) == "worker launch failed: Windows refused the required breakaway process"


def test_disposition_requires_authority_and_free_execution_custody(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original = _failed(area, monkeypatch)
    before = (root / "queue.sqlite3").read_bytes()
    with pytest.raises(DispositionError, match="authorization"):
        resolve_failed_unstarted(original.id, reason="fixture repair", root=root)
    with OperationLock(root / "execution.lock"):
        with pytest.raises(DispositionError, match="execution is active"):
            resolve_failed_unstarted(original.id, reason="fixture repair", authorized=True, root=root)
    assert (root / "queue.sqlite3").read_bytes() == before
    assert _read(original.id, root) == original


@pytest.mark.parametrize("evidence", ["running", "started", "finished", "boot"])
def test_execution_evidence_refuses_without_changing_the_request(
    area: Path, monkeypatch: pytest.MonkeyPatch, evidence: str,
) -> None:
    root, original = _failed(area, monkeypatch)
    updates = {
        "running": ("state", "running"), "started": ("started_at", 1.0),
        "finished": ("finished_at", 1.0),
        "boot": ("boot_id", "windows:11111111-1111-4111-8111-111111111111"),
    }
    field, value = updates[evidence]
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(f"UPDATE requests SET {field}=? WHERE id=?", (value, original.id))
    before = _read(original.id, root)
    with pytest.raises(DispositionError, match="not a recorded failed-unstarted"):
        resolve_failed_unstarted(original.id, reason="fixture repair", authorized=True, root=root)
    assert _read(original.id, root) == before


def test_finite_cli_disposes_only_the_selected_failed_unstarted_request(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, original = _failed(area, monkeypatch)
    peer = enqueue(RequestSpec("fixture", argv=("peer",)), runner=original.runner, root=root)
    result = subprocess.run(
        [
            sys.executable, "-m", "update_driver", "resolve-failed-unstarted",
            "--root", str(root), "--request-id", original.id,
            "--reason", "fixture CLI repair", "--authorized-repair",
        ],
        cwd=area, capture_output=True, text=True, timeout=15, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["request_id"] == original.id and output["exit_code"] == 125
    assert output["started_at"] is None and output["finished_at"] is not None
    assert _read(peer.id, root) == peer
    assert _read(original.id, root).state == "completed"
