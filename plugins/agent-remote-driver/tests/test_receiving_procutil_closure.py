"""Import the relocated receiving closure without installed package fallbacks."""

from pathlib import Path
import shutil
import sys

from agent_procutil.owned_process import run_caller_owned
from agent_remote_driver.session_host.bundle import build_session_host_bundle


def test_relocated_archive_retains_daemon_registry_io(tmp_path: Path) -> None:
    source = tmp_path / "source"
    archive, _ = build_session_host_bundle(source)
    retained = tmp_path / archive.name
    shutil.copy2(archive, retained)
    shutil.rmtree(source)
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from agent_procutil import _daemon_instance_io, _daemon_instance_store
assert _daemon_instance_io.__file__.startswith(sys.argv[1])
assert _daemon_instance_store.__file__.startswith(sys.argv[1])
"""
    result = run_caller_owned(
        [sys.executable, "-I", "-S", "-c", script, str(retained)],
        cwd=tmp_path, timeout=15,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, result.stderr
