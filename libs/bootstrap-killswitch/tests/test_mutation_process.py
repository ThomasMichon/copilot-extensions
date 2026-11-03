"""Real controller processes must share the audit hold's compare-and-mutate lock."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil.owned_process import run_caller_owned

CANONICAL = Path(__file__).resolve().parents[1]
REPO = CANONICAL.parents[1]
_SPEC = importlib.util.spec_from_file_location("canonical_killswitch_mutation", CANONICAL / "killswitch_mutation.py")
assert _SPEC is not None and _SPEC.loader is not None
mutation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutation)


def command(controller: str, action: str, expected: str = "") -> list[str]:
    arguments = [action, *(["concurrent writer"] if action == "on" else [])]
    if controller == "machines":
        assert not expected
        code = (
            "import argparse, sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "from agent_machines import killswitch_cli as cli; "
            "args=argparse.Namespace(reason='concurrent writer', json=True); "
            "raise SystemExit(getattr(cli, 'cmd_'+sys.argv[2])(args))"
        )
        return [sys.executable, "-c", code, str(REPO / "plugins/agent-machines/src"), action]
    if controller == "bash":
        if os.name == "nt":
            pytest.skip("Bash/Python native interoperability is qualified on POSIX")
        host = shutil.which("bash")
        flag = "--expected-state"
    else:
        host = shutil.which(os.environ.get("BOOTSTRAP_KILLSWITCH_TEST_SHELL", "pwsh"))
        flag = "-ExpectedState"
    if host is None:
        pytest.skip(f"{controller} host unavailable")
    prefix = (
        [host, str(CANONICAL / "bootstrap-killswitch-guard.sh")]
        if controller == "bash" else
        [host, "-NoProfile", "-NonInteractive", "-File", str(CANONICAL / "bootstrap-killswitch-guard.ps1")]
    )
    return [*prefix, *arguments, *([flag, expected] if expected else [])]


def run(argv: list[str], path: Path) -> subprocess.CompletedProcess[str]:
    return run_caller_owned(
        argv, timeout=15, max_stdout_bytes=65536, max_stderr_bytes=65536,
        env={
            **{key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(path),
            "BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT": str(path.parent),
            "HOME": str(path.parent), "USERPROFILE": str(path.parent),
            "PYTHONNOUSERSITE": "1",
        },
    )


@pytest.mark.parametrize("controller", ["bash", "powershell", "machines"])
@pytest.mark.parametrize("action", ["on", "off"])
def test_every_writer_respects_the_shared_native_lock_and_recovers(
    tmp_path: Path, controller: str, action: str,
) -> None:
    path = tmp_path / "hold.json"
    baseline = b'{"active":true,"reason":"retained audit hold"}'
    path.write_bytes(baseline)
    argv = command(controller, action)
    with mutation.mutation_lock(path):
        result = run(argv, path)
        assert result.returncode != 0, (controller, action, result.stdout, result.stderr)
        assert "writer lock unavailable" in result.stderr
        assert path.read_bytes() == baseline
    result = run(argv, path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    if action == "on":
        assert json.loads(path.read_bytes())["reason"] == "concurrent writer"
    else:
        assert not path.exists()
    assert path.with_name(path.name + ".lock").is_file()


@pytest.mark.parametrize("controller", ["bash", "powershell"])
@pytest.mark.parametrize("action", ["on", "off"])
def test_conditional_mutation_checks_exact_audit_bytes_not_just_active(
    tmp_path: Path, controller: str, action: str,
) -> None:
    path = tmp_path / "hold-\u00e9.json"
    path.write_bytes(b'{"active":true,"reason":"confirmed audit hold"}')
    expected = mutation.state_revision(path)
    baseline = b'{"active":true,"reason":"new unconfirmed audit hold"}'
    path.write_bytes(baseline)
    result = run(command(controller, action, expected), path)
    assert result.returncode != 0
    assert "switch changed" in result.stderr
    assert path.read_bytes() == baseline
    result = run(command(controller, action, mutation.state_revision(path)), path)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert path.exists() is (action == "on")
