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
        ["node", "--test", str(tests / "plugin-readiness.test.mjs"),
         str(tests / "delivery.test.mjs"), str(tests / "retained-delivery.test.mjs"),
         str(tests / "retained-extension.test.mjs"), str(tests / "questions.test.mjs")],
        capture_output=True, text=True, timeout=25, **no_window_kwargs(),
    )
    if result.returncode != 0:
        version = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        pytest.fail(f"Node runtime: {version.stdout.strip()}\n{result.stdout}{result.stderr}")


def test_core_extension_helper_contracts():
    tests = Path(__file__).parent
    result = subprocess.run(
        ["node", "--test", *(
            str(tests / name) for name in (
                "announce.test.mjs", "bridge-endpoint.test.mjs", "delivery-state.test.mjs",
                "extension.test.mjs", "headed-client.test.mjs", "metadata.test.mjs",
                "remote-driver.test.mjs",
            )
        )],
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
