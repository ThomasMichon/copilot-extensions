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


def run(
    argv: list[str], path: Path, *, override: str | None = None,
    home: Path | None = None, cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    home = home or path.parent
    return run_caller_owned(
        argv, timeout=15, max_stdout_bytes=65536, max_stderr_bytes=65536,
        cwd=cwd,
        env={
            **{key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            "BOOTSTRAP_KILLSWITCH_STATE_FILE": override if override is not None else str(path),
            "BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT": str(path.parent),
            "HOME": str(home), "USERPROFILE": str(home),
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


@pytest.mark.parametrize("controller", ["bash", "powershell", "machines"])
@pytest.mark.parametrize("override", ["~/hold.json", "relative/hold.json", "missing/../hold.json"])
def test_readers_and_writers_select_the_same_normalized_override(
    tmp_path: Path, controller: str, override: str,
) -> None:
    selected = tmp_path / ("hold.json" if override.startswith("~") else override)
    path = selected.parent.resolve() / selected.name

    def execute(action: str) -> subprocess.CompletedProcess[str]:
        return run(command(controller, action), path, override=override, home=tmp_path, cwd=tmp_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"active":true}', encoding="utf-8")
    initial = execute("status")
    assert initial.returncode == 0, (initial.stdout, initial.stderr)
    assert json.loads(initial.stdout)["active"] is True
    activated = execute("on")
    assert activated.returncode == 0, (activated.stdout, activated.stderr)
    assert json.loads(path.read_bytes())["active"] is True
    if controller != "machines":
        checked = execute("check")
        assert checked.returncode == 0, (checked.stdout, checked.stderr)
        assert "ACTIVE" in checked.stderr
    reported = execute("status")
    assert reported.returncode == 0, (reported.stdout, reported.stderr)
    assert json.loads(reported.stdout)["active"] is True
    cleared = execute("off")
    assert cleared.returncode == 0, (cleared.stdout, cleared.stderr)
    assert not path.exists()
    if override.startswith("missing/"):
        assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("controller", ["bash", "powershell"])
def test_normalized_override_retains_path_bound_confirmation(
    tmp_path: Path, controller: str,
) -> None:
    path = tmp_path / "hold.json"
    path.write_bytes(b'{"active":true,"reason":"retained audit hold"}')
    expected = mutation.state_revision(path)
    result = run(
        command(controller, "off", expected), path,
        override="missing/../hold.json", home=tmp_path, cwd=tmp_path,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture; no Windows elevation")
@pytest.mark.parametrize("controller", ["bash", "powershell", "machines"])
@pytest.mark.parametrize("action", ["on", "off"])
def test_normalized_override_does_not_follow_linked_state_leaf(
    tmp_path: Path, controller: str, action: str,
) -> None:
    target = tmp_path / "retained.json"
    baseline = b'{"active":true,"reason":"retained audit hold"}'
    target.write_bytes(baseline)
    path = tmp_path / "hold.json"
    path.symlink_to(target)
    result = run(
        command(controller, action), path,
        override="missing/../hold.json", home=tmp_path, cwd=tmp_path,
    )
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert "linked" in result.stderr.lower()
    assert path.is_symlink() and target.read_bytes() == baseline


@pytest.mark.parametrize("controller", ["bash", "powershell"])
def test_capability_discovery_does_not_resolve_or_touch_state(
    tmp_path: Path, controller: str,
) -> None:
    path = tmp_path / "never-created.json"
    result = run(
        command(controller, "capabilities"), path,
        override="~unknown-controller-capability-user/hold.json", home=tmp_path,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["capabilities"] == ["conditional-mutation-v1"]
    assert not path.exists() and not path.with_name(path.name + ".lock").exists()
