"""Opt-in authentic preparation prerequisite for installed namespace qualification."""

import json
import os
from pathlib import Path
import re
import shutil
import sys

import pytest
from agent_procutil.daemon_instances import daemon_test_lifecycle
from agent_procutil.owned_process import run_caller_owned


ROOT = Path(__file__).parents[3]
QUALIFICATION = "COPILOT_EXTENSIONS_MCP_APPLICATION_QUALIFICATION"
SOURCE_ENV = "COPILOT_EXTENSIONS_MCP_APPLICATION_SOURCE_COMMIT"


class _PrivateEnvironment(dict):
    def __repr__(self):
        return "<private child environment>"


def _run(arguments, *, cwd, environment, timeout=120):
    result = run_caller_owned(
        list(map(str, arguments)), cwd=cwd, env=environment, timeout=timeout,
        max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20, binary=True,
    )
    assert result.returncode == 0, (
        result.stdout.decode(errors="replace")[-8000:]
        + result.stderr.decode(errors="replace")[-8000:]
    )
    return result.stdout.decode("utf-8")


@pytest.mark.skipif(os.name != "nt", reason="native installed Windows application qualification")
@pytest.mark.skipif(os.environ.get(QUALIFICATION) != "1", reason="explicit application qualification")
@pytest.mark.timeout(270)
def test_prepare_installed_application_namespace_inputs(tmp_path, record_testsuite_property):
    from plugin_test_environment import isolated_environment

    source_commit = os.environ.get(SOURCE_ENV, "")
    assert re.fullmatch(r"[0-9a-f]{40}", source_commit), "an exact approved source commit is required"
    environment = _PrivateEnvironment(isolated_environment(os.environ, tmp_path / "profile"))
    environment.update(
        AGENT_MCP_INSTALL_DIR=str(tmp_path / "installation"),
        AGENT_MCP_HOME=str(tmp_path / "configuration"),
        AGENT_MCP_NO_VERSION_REAP="1",
        UV_CACHE_DIR=str(Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"]) / ".uv-cache"),
    )
    uv = shutil.which("uv")
    assert uv
    with daemon_test_lifecycle(
        owner="installed-application-namespace", registry_root=tmp_path / "instances",
    ) as lifecycle:
        environment.update(lifecycle)
        record_testsuite_property("application_source_commit", source_commit)
        source = tmp_path / "source"
        _run([
            sys.executable, ROOT / "tools" / "local_install_source.py",
            "--source-checkout", ROOT, "--source-commit", source_commit,
            "--output", source, "--project", "agent-mcp",
        ], cwd=tmp_path, environment=environment)
        output = _run([
            sys.executable, ROOT / "tools" / "local_install_profile.py",
            "--prepare-profile", "--install-source", source,
            "--component", "agent-mcp", "--output", tmp_path / "prepared",
            "--uv", uv, "--timeout", "120",
        ], cwd=tmp_path, environment=environment)
        prepared = Path(json.loads(output.splitlines()[-1])["snapshot"])
        assert prepared.is_dir()
        assert not Path(environment["AGENT_MCP_INSTALL_DIR"]).exists()
