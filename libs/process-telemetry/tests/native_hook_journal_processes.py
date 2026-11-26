"""Explicitly selected hook-journal checks across real, independent processes."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

from process_telemetry import hook_journal

SOURCE = str(Path(hook_journal.__file__).resolve().parents[1])
TIMEOUT_S = 60


def _python(script: str, *args: str, capture: bool = False) -> subprocess.Popen[bytes]:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([SOURCE, os.environ.get("PYTHONPATH", "")]))
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and fixture-authored script
        [sys.executable, "-c", textwrap.dedent(script), *args], env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.PIPE if capture else None,
    )


def _wait_all(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        assert process.wait(timeout=TIMEOUT_S) == 0


@contextlib.contextmanager
def _reaped() -> Iterator[list[subprocess.Popen[bytes]]]:
    """Kill and reap every started child, even when an assertion fails first."""
    processes: list[subprocess.Popen[bytes]] = []
    try:
        yield processes
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=TIMEOUT_S)


def test_simultaneous_starts_in_independent_processes_honor_the_marker_cap(
    tmp_path: Path,
) -> None:
    gate = tmp_path / "go"
    starter = """
        import sys, time
        from pathlib import Path
        from process_telemetry import hook_journal
        hook_journal.MARKER_LIMIT = 3
        hook_journal.LOCK_WAIT_S = 10.0
        gate = Path(sys.argv[2])
        while not gate.exists():
            time.sleep(0.001)
        hook_journal.HookRun.begin(Path(sys.argv[1]), "agent-vault", "sessionStart")
    """
    with _reaped() as processes:
        processes.extend(_python(starter, str(tmp_path), str(gate)) for _ in range(8))
        gate.write_bytes(b"")
        _wait_all(processes)
    assert len(list(tmp_path.glob("*.start"))) == 3


def test_independent_writers_and_a_drainer_report_each_run_exactly_once(tmp_path: Path) -> None:
    writer = """
        import sys
        from pathlib import Path
        from process_telemetry import hook_journal
        hook_journal.LOCK_WAIT_S = 10.0
        for _ in range(5):
            hook_journal.HookRun.begin(Path(sys.argv[1]), "agent-vault", "sessionStart").finish(
                outcome="success",
            )
    """
    drainer = """
        import json, sys
        from pathlib import Path
        from process_telemetry import hook_journal
        hook_journal.LOCK_WAIT_S = 10.0
        seen = []
        for _ in range(40):
            seen.extend(event["run_sequence"] for event in hook_journal.drain(Path(sys.argv[1])))
        print(json.dumps(seen))
    """
    with _reaped() as processes:
        writers = [_python(writer, str(tmp_path)) for _ in range(4)]
        processes.extend(writers)
        reader = _python(drainer, str(tmp_path), capture=True)
        processes.append(reader)
        _wait_all(writers)
        output, _ = reader.communicate(timeout=TIMEOUT_S)
        assert reader.returncode == 0
    sequences = json.loads(output) + [
        event["run_sequence"] for event in hook_journal.drain(tmp_path)
    ]
    assert sorted(sequences) == list(range(1, 21))
    assert not list(tmp_path.glob("*.start"))


def test_a_killed_lock_holder_does_not_wedge_the_journal(tmp_path: Path) -> None:
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart").finish(outcome="success")
    ready = tmp_path / "held"
    holder = """
        import sys, time
        from pathlib import Path
        from process_telemetry import hook_journal
        with hook_journal._journal_lock(Path(sys.argv[1])) as held:
            assert held
            Path(sys.argv[2]).write_bytes(b"")
            time.sleep(TIMEOUT)
    """.replace("TIMEOUT", str(TIMEOUT_S))
    process = _python(holder, str(tmp_path), str(ready))
    try:
        deadline = time.monotonic() + TIMEOUT_S
        while not ready.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        assert hook_journal.drain(tmp_path) == []
    finally:
        process.kill()
        process.wait(timeout=TIMEOUT_S)
    events = hook_journal.drain(tmp_path)
    assert [event["outcome"] for event in events] == ["success"]
