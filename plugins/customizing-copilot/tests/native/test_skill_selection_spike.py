"""Opt-in Node transport-helper controls; no SDK, auth, or model inference."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


def test_model_free_context_helpers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the model-free SDK helper packet")
    test_directory = Path(__file__).parents[1]
    result = subprocess.run(
        [node, "--test", "--test-reporter=tap",
         str(test_directory / "skill-selection-spike.test.mjs"),
         str(test_directory / "skill-selection-contract.test.mjs")],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"^# tests [1-9]\d*$", result.stdout, re.MULTILINE), result.stdout
    assert "# cancelled 0" in result.stdout and "# skipped 0" in result.stdout, result.stdout
