"""Real Linux ownership boundary, complementing Windows Job regressions."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agent_index_service.staging import CandidateError, _run


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper boundary")
@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_contained_linux_reaps_descendants_without_killing_outer_group(
    tmp_path, monkeypatch, mode,
):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    receipt = tmp_path / "child-pid"
    script = f"""
import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path({str(receipt)!r}).write_text(str(child.pid))
if {mode!r} == 'failure':
    raise SystemExit(7)
time.sleep(60)
"""
    group = os.getpgrp()
    deadline = time.monotonic() + (2 if mode == "timeout" else 15)
    with pytest.raises(CandidateError, match="timeout" if mode == "timeout" else "exit status 7"):
        _run([sys.executable, "-I", "-c", script], cwd=tmp_path,
             env=dict(os.environ), deadline=deadline)
    assert os.getpgrp() == group
    pid = int(receipt.read_text())
    state = Path(f"/proc/{pid}/stat")
    assert not state.exists() or state.read_text().split(") ", 1)[1].startswith("Z ")
    assert not list(tmp_path.glob(".build-output-*"))
