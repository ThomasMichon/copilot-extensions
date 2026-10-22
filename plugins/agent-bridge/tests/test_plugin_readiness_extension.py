"""Run the pure public-SDK observer/control tests inside plugin containment."""

import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_readiness_and_existing_delivery_node_contracts():
    tests = Path(__file__).parent
    result = subprocess.run(
        ["node", "--test", str(tests / "plugin-readiness.test.mjs"), str(tests / "delivery.test.mjs")],
        capture_output=True, text=True, timeout=25, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
