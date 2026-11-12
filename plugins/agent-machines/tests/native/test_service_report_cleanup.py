"""Opt-in real child/pipe bounds; no service, credentials or carrier."""

from __future__ import annotations

import os
import sys
import time

import pytest

from agent_machines import service_reports as reports


@pytest.mark.parametrize("code,state", [
    ("import time; time.sleep(30)", "timeout"),
    (
        "import subprocess,sys;"
        "from agent_procutil import no_window_kwargs;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
        "stdout=sys.stdout,stderr=sys.stderr,**no_window_kwargs());"
        "print('{\"status\":\"unsupported\"}',flush=True)",
        "timeout",
    ),
    ("import sys; sys.stdout.buffer.write(b'x'*70000); sys.stdout.flush()", "unavailable"),
    ("import sys; sys.stdout.buffer.write(b'\\xff'); sys.stdout.flush()", "invalid"),
], ids=["deadline", "retained-pipe-after-root-exit", "output-bound", "invalid-utf8"])
def test_native_export_failures_settle_through_the_caller_owned_boundary(monkeypatch, code, state):
    hour = time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime())
    monkeypatch.setattr(reports, "SERVICE_SECONDS", 3)
    monkeypatch.setattr(reports, "select", lambda service: reports.SelectedService(
        "fixture-installation", service, [sys.executable, "-c", code], dict(os.environ),
    ))
    owned = reports.run_caller_owned
    settled = []
    deadlines = []

    def run(*args, **kwargs):
        deadlines.append(kwargs["budget"].deadline)
        return owned(*args, **kwargs, on_settled=lambda: settled.append(time.monotonic()))

    monkeypatch.setattr(reports, "run_caller_owned", run)
    started = time.monotonic()
    result = reports.collect(["agent-dispatch"], hour, now_ms=int(time.time() * 1000))
    assert result[0]["state"] == state
    assert "contribution" not in result[0]
    assert time.monotonic() - started < 15
    assert len(settled) == len(deadlines) == 1
    assert settled[0] <= deadlines[0]
