"""The containment gate must start independently of coordinator imports."""

from __future__ import annotations

import os
import signal
import sys
from types import SimpleNamespace

from agent_dispatch import companion


def test_native_gate_does_not_initialize_coordinator(tmp_path):
    shadow = tmp_path / "agent_dispatch"
    shadow.mkdir()
    (shadow / "__init__.py").write_text(
        "raise RuntimeError('containment gate initialized coordinator package')\n",
        encoding="utf-8",
    )
    marker = tmp_path / "contributed-command"
    resolution = SimpleNamespace(
        command=(sys.executable, "-c",
                 "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
                 str(marker)),
        environment={**os.environ, "PYTHONPATH": str(tmp_path)},
        cwd=str(tmp_path),
    )
    process = companion._launch_gated(resolution)
    try:
        assert not marker.exists()
        process.release()
        assert process.wait(timeout=5) == (0 if os.name == "nt" else -signal.SIGKILL)
        assert marker.read_text() == "ran"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
