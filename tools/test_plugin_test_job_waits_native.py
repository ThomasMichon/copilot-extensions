"""Fresh disposable-Job wait probe; never inspect a pre-existing process."""

import json
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import plugin_test_containment as containment
else:
    from tools import plugin_test_containment as containment


@pytest.mark.skipif(os.name != "nt", reason="Windows process snapshot and WCT APIs")
def test_query_information_job_member_supports_process_scoped_thread_capture(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = containment._run_contained_process(
        [sys.executable, "-I", "-S", "-B", "-c", "import time; time.sleep(8.75)"],
        cwd=tmp_path,
        env=dict(os.environ),
        sandbox=tmp_path / "disposable-job",
        limits=containment.Limits(wall_seconds=10),
        job_observation=True,
    )
    assert result == 0
    lines = capsys.readouterr().err.splitlines()
    rows = [
        json.loads(line.split(" ", 1)[1])
        for line in lines
        if line.startswith("[CI-JOB-WAIT] ")
    ]
    summary = json.loads(
        next(
            line.split(" ", 1)[1]
            for line in lines
            if line.startswith("[CI-JOB-OBSERVATION] ")
        )
    )
    processes = [
        json.loads(line.split(" ", 1)[1])
        for line in lines
        if line.startswith("[CI-JOB-PROCESS-SUMMARY] ")
    ]
    assert rows, summary
    assert all(row["sample_at_ms"] >= 8000 for row in rows), rows
    assert any(row.get("threads_observed", 0) > 0 for row in rows), rows
    assert summary["unclosed_wait_requests"] == summary["unclosed_handles"] == 0
    assert len(processes) == summary["summary_rows"] > 0
    assert (
        summary["rows"]
        == summary["process_rows"] + summary["wait_rows"] + summary["summary_rows"]
        <= 4096
    )
    wait_identities = {(row["pid"], row["birth_100ns"]) for row in rows}
    assert any(
        row["exited"] is True
        and row["exit_code"] == 0
        and (row["pid"], row["birth_100ns"]) in wait_identities
        for row in processes
    ), processes
    assert all(row["first_at_ms"] <= row["at_ms"] for row in processes)
    assert all(row["first_kernel_100ns"] <= row["kernel_100ns"] for row in processes)
    assert all(row["first_user_100ns"] <= row["user_100ns"] for row in processes)
    assert not any("close" in error for error in summary["errors"]), summary
    assert all(
        type(value) in (int, bool, type(None))
        for row in rows + processes
        for value in row.values()
    )
    print(
        "[CI-JOB-WAIT-FIXTURE] "
        + json.dumps(
            {
                "wait_rows": len(rows),
                "first_wait_sample_ms": min(row["sample_at_ms"] for row in rows),
                "thread_snapshots": sum(
                    row.get("threads_observed", 0) > 0 for row in rows
                ),
                "callback_statuses": sorted(
                    {row["wait_status"] for row in rows if "wait_status" in row}
                ),
                "error_codes": sorted(
                    {row["error_code"] for row in rows if "error_code" in row}
                ),
                "cancelled_pending": sum(
                    bool(row.get("cancelled_pending")) for row in rows
                ),
                "unclosed_handles": summary["unclosed_handles"],
                "unclosed_wait_requests": summary["unclosed_wait_requests"],
            },
            sort_keys=True,
        )
    )
