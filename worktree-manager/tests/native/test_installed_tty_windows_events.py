"""Portable scope/privacy controls; not native fault reproduction."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from _installed_tty_windows_events import capture_fault_events


def _capture(tmp_path, response):
    faults = tmp_path / "faults"
    faults.mkdir()
    (faults / "manager-123.fault.log").write_bytes(
        b"Windows fatal exception: access violation\n",
    )
    calls = []

    def run(arguments, *args, **kwargs):
        calls.append((arguments, kwargs))
        return json.dumps(response)

    capture_fault_events(faults, tmp_path, {}, tmp_path, "powershell", 100, run)
    result = json.loads((tmp_path / "powershell.native-error-events.json").read_text())
    return result, calls


def test_native_fault_metadata_retains_only_its_observed_process(tmp_path):
    row = {
        "pid": 123, "process_birth": 116444737000000000,
        "module": "python312.dll", "module_version": "3.12.10.0",
        "exception": "c0000005", "offset": "12ab",
    }
    result, calls = _capture(tmp_path, [row])
    assert result == {"owned_fault_pids": [123], "query_complete": True, "events": [row]}
    assert len(calls) == 1 and calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("change", [
    {"pid": 456}, {"pid": True}, {"module": r"C:\private\python312.dll"},
    {"operator_data": "must not be retained"}, {"process_birth": 1},
    {"process_birth": 999999999999999999}, {"process_birth": True},
])
def test_unqualified_event_data_is_not_retained(tmp_path, change):
    row = {
        "pid": 123, "process_birth": 116444737000000000,
        "module": "python312.dll", "module_version": "3.12.10.0",
        "exception": "c0000005", "offset": "12ab", **change,
    }
    result, _ = _capture(tmp_path, [row])
    assert result["events"] == [] and result["query_complete"] is False
    assert result["unavailable"] == "ValueError"


def test_empty_or_nonfatal_logs_do_not_query_other_processes(tmp_path):
    (tmp_path / "manager-123.fault.log").write_bytes(b"")
    (tmp_path / "manager-456.fault.log").write_bytes(b"ordinary diagnostic")
    (tmp_path / "manager-other.fault.log").write_bytes(
        b"Windows fatal exception: access violation\n",
    )
    capture_fault_events(
        tmp_path, tmp_path, {}, tmp_path, "powershell", 100,
        lambda *_args, **_kwargs: pytest.fail("No owned native fault was observed"),
    )
    assert not (tmp_path / "powershell.native-error-events.json").exists()
