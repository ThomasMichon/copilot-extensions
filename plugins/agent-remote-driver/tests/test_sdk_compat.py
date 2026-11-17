"""Exercise the shared extension adapter without a live CLI session."""

from importlib.resources import files
from pathlib import Path
import shutil
import subprocess

from agent_procutil import no_window_kwargs
from agent_remote_driver.headed.staging import stage_extension
import pytest


def test_extension_sdk_compatibility() -> None:
    node = shutil.which("node")
    assert node, "extension SDK compatibility controls require Node"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("sdk-compat.test.mjs"))],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_headed_payload_retains_and_verifies_the_shared_sdk_helper(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    selected = stage_extension(attempt)
    helper = selected / "extensions" / "copilot-driver-headed" / "sdk-compat.mjs"
    assert helper.read_bytes() == files("agent_remote_driver.bridge_extension").joinpath(
        "sdk-compat.mjs"
    ).read_bytes()
    helper.write_text("changed retained helper", encoding="utf-8")
    with pytest.raises(ValueError, match="assets changed"):
        stage_extension(attempt)
    assert helper.read_text() == "changed retained helper"
