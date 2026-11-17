"""Actor-free decoding of diagnostic progress never creates capture acceptance."""
from __future__ import annotations

from pathlib import Path

import bridge_capture_progress as progress


def test_progress_summary_retains_last_complete_record_after_interrupted_write(tmp_path: Path) -> None:
    (tmp_path / "observer-1.jsonl").write_text(
        '{"kind":"start","nodeid":"test_probe.py::test_control"}\n'
        '{"kind":"report",', encoding="utf-8",
    )
    summary = progress.summarize(tmp_path)
    observer, = summary["observers"]
    assert observer["started"] == 1 and observer["completed"] == 0
    assert observer["last_started"] == {"kind": "start", "nodeid": "test_probe.py::test_control"}
    assert summary["scope"] == "diagnostics-only; not capture evidence"


def test_progress_summary_keeps_slow_phase_separate_from_completion(tmp_path: Path) -> None:
    (tmp_path / "observer-1.jsonl").write_text(
        '{"kind":"start","nodeid":"test_probe.py::test_control"}\n'
        '{"kind":"report","nodeid":"test_probe.py::test_control","phase":"call",'
        '"seconds":7.5,"outcome":"passed"}\n', encoding="utf-8",
    )
    summary = progress.summarize(tmp_path)
    observer, = summary["observers"]
    assert observer["started"] == 1 and observer["completed"] == 0
    slow, = observer["slowest_phases"]
    assert slow["phase"] == "call" and slow["seconds"] == 7.5
    assert summary["scope"] == "diagnostics-only; not capture evidence"
