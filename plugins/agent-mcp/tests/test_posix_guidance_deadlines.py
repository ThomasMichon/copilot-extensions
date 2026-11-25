"""Actual Linux hook deadlines retain selector and writer group ownership."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
from collections.abc import Callable

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .managed_hook_fixture import packaged_hooks as packaged_hooks
from .managed_hook_fixture import prepared_helper as prepared_helper

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native hook custody")


def _alive(pid: int) -> bool:
    try:
        return not (Path("/proc") / str(pid) / "stat").read_text().split(") ", 1)[1].startswith("Z")
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("missing", [
    "native_owned.sh", "daemon_instance_runtime.pyz", "bootstrap_uv_capture.py",
])
def test_bash_missing_custody_refuses_before_selection(
    tmp_path: Path, packaged_hooks: Path, missing: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    payload = tmp_path / "agent-mcp"
    shutil.copytree(packaged_hooks, payload)
    support = payload / "scripts/_bootstrap_python"
    (support / missing).unlink()
    marker = tmp_path / "selector-ran"
    (support / "select.sh").write_text(
        'printf invoked > "$MCP_SELECTOR_MARKER"\n', encoding="utf-8",
    )
    result = run_caller_owned(
        [bash, str(payload / "scripts/write-session-guidance.sh")],
        cwd=tmp_path, env={
            **os.environ, "COPILOT_PLUGIN_ROOT": str(payload), "MCP_SELECTOR_MARKER": str(marker),
        },
        input_text='{"sessionId":"session-1"}', timeout=5,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
    assert not marker.exists()


@pytest.mark.parametrize("scenario", ["held-input", "slow-selection", "blocked-writer"])
def test_bash_guidance_deadlines(
    tmp_path: Path, scenario: str, packaged_hooks: Path,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    bash = shutil.which("bash")
    assert bash
    _, selected, managed_root = prepared_helper
    payload = tmp_path / "agent-mcp"
    shutil.copytree(packaged_hooks, payload)
    scripts = payload / "scripts"
    proofs = tmp_path / "processes"
    proofs.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(managed_root), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "MCP_DEADLINE_PROOFS": str(proofs),
        "COPILOT_EXTENSIONS_GUIDANCE_DEADLINE": "invalid-inherited-deadline",
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "BASH_ENV", "ENV"):
        env.pop(name, None)
    if scenario == "slow-selection":
        selector = scripts / "_bootstrap_python/select.sh"
        selector.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s' \"$BASHPID\" > \"$MCP_DEADLINE_PROOFS/selector\"\n"
            "sleep 300 &\nprintf '%s' \"$!\" > \"$MCP_DEADLINE_PROOFS/descendant\"\n"
            "wait\n",
            encoding="utf-8",
        )
    elif scenario == "blocked-writer":
        (scripts / "write_session_guidance.py").write_text(
            "import os,subprocess,sys,time\nfrom pathlib import Path\n"
            "root=Path(os.environ['MCP_DEADLINE_PROOFS'])\n"
            "(root/'writer').write_text(str(os.getpid()))\n"
            "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(300)'])\n"
            "(root/'descendant').write_text(str(child.pid))\n"
            "time.sleep(300)\n",
            encoding="utf-8",
        )
    argv = [bash, str(scripts / "write-session-guidance.sh")]
    if scenario == "held-input":
        worker = (
            "import json,subprocess,sys\n"
            "p=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.PIPE,"
            "stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
            "try:\n"
            " p.stdin.write(b'{\"sessionId\":\"session-1\"}');p.stdin.flush()\n"
            " p.wait(timeout=45)\n"
            " print(json.dumps({'code':p.returncode,'out':p.stdout.read().decode(),"
            "'err':p.stderr.read().decode()}))\n"
            "finally:\n"
            " if p.poll() is None:p.kill();p.wait(timeout=5)\n"
            " p.stdin.close()\n"
        )
        argv = [str(selected), "-I", "-B", "-c", worker, json.dumps(argv)]
    started = time.monotonic()
    result = run_caller_owned(
        argv, env=env, cwd=tmp_path, input_text='{"sessionId":"session-1"}',
        timeout=50, max_stdout_bytes=65536, max_stderr_bytes=65536,
        budget=OwnedExecutionBudget.from_timeout(55, cleanup_reserve=5),
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0 and not result.stderr, result
    if scenario == "held-input":
        assert json.loads(result.stdout) == {"code": 0, "out": "{}", "err": ""}
        assert not list(proofs.iterdir())
    else:
        assert result.stdout == "{}"
        pids = [int(path.read_text()) for path in proofs.iterdir()]
        assert len(pids) == 2
        assert not [pid for pid in pids if _alive(pid)]
    assert elapsed < 45, elapsed
    record_property("mcp_posix_deadline_execution", json.dumps({
        "scenario": scenario, "elapsed_seconds": elapsed,
        "hook_timeout_seconds": 45, "contributors_remaining": 0,
    }))
