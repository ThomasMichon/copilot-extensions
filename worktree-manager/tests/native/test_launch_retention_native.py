"""Actual restart boundaries for private-input expiry, without executing launches."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

import pytest
from agent_procutil import no_window_kwargs


_PREPARE = """
import os, pathlib, sqlite3, sys
from uuid import uuid4
from worktree_manager import launch_attempts
root = pathlib.Path(sys.argv[1])
history = root / "seed-history" / "history.sqlite3"
history.parent.mkdir()
conn = sqlite3.connect(history)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA secure_delete=OFF")
conn.execute("CREATE TABLE seeds (id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
             "prompt TEXT NOT NULL, submitted_at REAL NOT NULL)")
conn.executemany("INSERT INTO seeds VALUES (?, 'example', ?, ?)", [
    (1, "expired-native-marker-" * 400, 1),
    (2, " exact retained\\n", 2000000000),
    (3, "deleted-native-marker-" * 400, 1),
])
conn.commit()
conn.execute("DELETE FROM seeds WHERE id=3")
conn.commit()
launch_attempts.time.time = lambda: 1
store = launch_attempts.LaunchAttempts(root)
store.record_intent(uuid4().hex, action="new", project="example", surface="native",
                    is_local=True, prompt="expired-native-marker-" * 400)
(root / "pending-daemon-seed.json").write_text("pending exact input\\n", encoding="utf-8")
os._exit(77)
"""

_MAINTAIN = """
import os, pathlib, sqlite3, sys
from worktree_manager import launch_retention, seed_history
root, boundary = pathlib.Path(sys.argv[1]), sys.argv[2]
seed_history.time.time = lambda: 2000000000
if boundary == "before-scrub":
    connect = sqlite3.connect
    class Interrupted(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql == "VACUUM":
                os._exit(78)
            return super().execute(sql, parameters)
    sqlite3.connect = lambda *a, **kw: connect(*a, **kw, factory=Interrupted)
assert launch_retention.maintain(root) is None
"""

_VERIFY = """
import json, pathlib, sqlite3, sys
from worktree_manager import launch_attempts, seed_history
root = pathlib.Path(sys.argv[1])
seed_history.time.time = lambda: 2000000000
history = seed_history.SeedHistory("example", root)
assert history.entries() == [" exact retained\\n"]
with sqlite3.connect(history.path) as conn:
    assert conn.execute("SELECT id, submitted_at FROM seeds").fetchall() == [(2, 2000000000)]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
journal = launch_attempts.LaunchAttempts(root)
rows = journal.entries()
assert len(rows) == 1 and rows[0]["inputs_available"] is False
assert len(journal.detail(rows[0]["attempt_id"])["events"]) == 1
for store in (history, journal):
    assert list(store.path.parent.iterdir()) == [store.path]
    content = store.path.read_bytes()
    assert b"expired-native-marker-" not in content
    assert b"deleted-native-marker-" not in content
assert (root / "pending-daemon-seed.json").read_text(encoding="utf-8") == "pending exact input\\n"
print(json.dumps({"physical_expiry": True, "timestamp_preserved": True,
                  "metadata_retained": True, "pending_seed_unchanged": True}))
"""


def _run(script, *args):
    return subprocess.run(
        [sys.executable, "-c", script, *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", timeout=20, **no_window_kwargs(),
    )


@pytest.mark.parametrize("interrupt", [False, True])
def test_restart_recovers_actual_legacy_wal_and_retries_interrupted_scrub(tmp_path, interrupt):
    result = _run(_PREPARE, tmp_path)
    assert result.returncode == 77, result.stderr
    history_path = tmp_path / "seed-history" / "history.sqlite3"
    assert history_path.with_name(history_path.name + "-wal").exists()
    if interrupt:
        result = _run(_MAINTAIN, tmp_path, "before-scrub")
        assert result.returncode == 78, result.stderr
        with closing(sqlite3.connect(history_path)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    result = _run(_MAINTAIN, tmp_path, "complete")
    assert result.returncode == 0, result.stderr
    result = _run(_VERIFY, tmp_path)
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout).values())


def test_maintenance_latency_reports_migration_and_warm_noop_costs(tmp_path, monkeypatch, capsys):
    from worktree_manager import launch_retention, seed_history

    monkeypatch.setattr(seed_history.time, "time", lambda: 2_000_000_000.0)
    history = seed_history.SeedHistory("example", tmp_path)
    history.path.parent.mkdir()
    with closing(sqlite3.connect(history.path)) as conn, conn:
        conn.execute("PRAGMA secure_delete=OFF")
        conn.execute(
            "CREATE TABLE seeds (id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
            "prompt TEXT NOT NULL, submitted_at REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO seeds VALUES (?, 'example', ?, ?)",
            [(index, "synthetic input\n" * 100, 1 if index % 2 else 2_000_000_000)
             for index in range(1, 102)],
        )
    samples = []
    for _ in range(21):
        start = time.perf_counter_ns()
        assert launch_retention.maintain(tmp_path) is None
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    assert len(history.entries()) == 50
    warm = sorted(samples[1:])
    with capsys.disabled():
        print("LAUNCH_RETENTION_NATIVE_MEASUREMENT " + json.dumps({
            "boundary": "in-process-maintenance-not-ui-or-installed-launch",
            "platform": sys.platform, "legacy_rows": 101, "retained_rows": 50,
            "failures": 0, "migration_ms": samples[0], "warm_samples_ms": samples[1:],
            "quantile": "nearest-rank", "warm_p50_ms": warm[9], "warm_p95_ms": warm[18],
            "warm_p99_ms": warm[19], "warm_max_ms": warm[-1],
            "tail_qualification": "insufficient-samples",
        }))
