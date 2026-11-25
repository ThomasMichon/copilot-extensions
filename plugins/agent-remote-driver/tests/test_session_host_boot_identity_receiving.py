"""Native kernel observation through current and legacy relocated Core exports."""

from pathlib import Path
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
from agent_remote_driver.session_host.bundle import build_session_host_bundle
import pytest


@pytest.mark.parametrize("legacy", [False, True])
def test_relocated_compact_host_observes_the_same_native_boot_in_fresh_processes(
    tmp_path: Path, legacy: bool,
) -> None:
    archive, _ = build_session_host_bundle(tmp_path / "build", legacy=legacy)
    receiver = tmp_path / "receiver"
    receiver.mkdir()
    relocated = receiver / archive.name
    shutil.copyfile(archive, relocated)
    shutil.rmtree(tmp_path / "build")
    script = """
import sys,uuid
sys.path.insert(0,sys.argv[1])
from agent_remote_driver.session_host import launcher
assert launcher.__file__.startswith(sys.argv[1])
observed=launcher._boot_id()
if sys.platform == "win32" or sys.platform.startswith("linux"):
    assert str(uuid.UUID(observed)) == observed
else:
    assert observed == ""
print(observed)
"""
    results = [
        subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, str(relocated)], cwd=relocated.parent,
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
        for _ in range(2)
    ]
    for result in results:
        assert result.returncode == 0, result.stderr
    assert results[0].stdout == results[1].stdout
