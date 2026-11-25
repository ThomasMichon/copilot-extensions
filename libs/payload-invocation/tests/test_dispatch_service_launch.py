"""Actual POSIX service ingress retains uv selection and native main-PID ownership."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from test_adopter_publication import _command
from test_command_binding import binding_fixture as _binding_fixture
from test_dispatch_first_use_publication import (
    _dispatch_environment, dispatch_release as _dispatch_release,
)
from test_installer_publication import _policy
from test_native_publication import _completed_slot, _run

binding_fixture = _binding_fixture
dispatch_release = _dispatch_release
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.timeout(60),
    pytest.mark.skipif(os.name == "nt", reason="POSIX native service ingress"),
]


def _function(payload: Path, name: str) -> str:
    source = (payload / "scripts/install.sh").read_text(encoding="utf-8")
    start = source.index(f"{name}() {{")
    return source[start:source.index("\n}\n", start) + 2]


def _service(
    fixture: dict[str, Any], release: Path, entry: str, *, prepared: bool = True,
) -> tuple[dict[str, str], list[str], Path]:
    environment = _dispatch_environment(fixture, release)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    environment["AGENT_DISPATCH_SUPERVISE_MACHINE"] = "fixture"
    stamped = _run(fixture, environment, [*_command(fixture, "bash", "install"), "stamp"])
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    root = fixture["legacy"]
    _policy(fixture, root, fixture["arguments"], "managed")
    python = root / "versions/1.0.0/bin/python"
    if prepared:
        python = _completed_slot(fixture)
        package = python.parent.parent / (
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/agent_dispatch"
        )
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'pid':os.getpid(), 'entry':os.environ['ENTRY_PID'],"
            "'native':os.environ.get('AGENT_DISPATCH_SUPERVISOR_MAIN_PID'),"
            "'unit':os.environ.get('AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT'),"
            "'python':sys.executable,'root':os.environ['AGENT_DISPATCH_INSTALL_DIR'],"
            "'args':sys.argv[1:],'stdin':sys.stdin.read()}))\n"
            "print('runtime-stderr', file=sys.stderr)\n"
            "raise SystemExit(37)\n",
            encoding="utf-8",
        )
    launcher = root / "supervisor-launcher.sh"
    unit_dir = fixture["home"] / "units"
    unit_dir.mkdir()
    env_file = root / "service.env"
    env_file.write_text("# retained service settings\n", encoding="utf-8")
    values = {
        "INSTALL_DIR": root, "SUPERVISOR_LAUNCHER": launcher,
        "UNIT_DIR": unit_dir, "SYSTEMD_UNIT": "agent-dispatch.service",
        "SUPERVISOR_PATH": environment["PATH"], "ENV_FILE": env_file,
    }
    service_source = _function(fixture["payload"], "_install_service")
    unit_source = service_source[service_source.index('    cat > "$UNIT_DIR/$SYSTEMD_UNIT"'):]
    unit_source = unit_source[:unit_source.index("\nEOF\n") + len("\nEOF\n")]
    script = "\n".join([
        "set -euo pipefail",
        *(f"{name}={shlex.quote(str(value))}" for name, value in values.items()),
        unit_source,
        _function(fixture["payload"], "_write_supervisor_launcher"),
        "_write_supervisor_launcher",
    ])
    generated = subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=15,
        env=environment,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    unit = (unit_dir / "agent-dispatch.service").read_text(encoding="utf-8")
    assert f'Environment="PATH={environment["PATH"]}"\n' in unit
    command = shlex.split(next(line.removeprefix("ExecStart=") for line in unit.splitlines()
                               if line.startswith("ExecStart=")))
    assert command == [
        "/usr/bin/env", "AGENT_DISPATCH_COMMAND_MODULE=agent_dispatch",
        "/bin/bash", str(root / "bin/runtime-gate.sh"), "serve",
    ]
    if entry != "coordinator":
        command = ["/bin/bash", str(launcher), "--systemd-unit", "agent-dispatch-supervisor.service"]
        if entry == "registrar":
            environment["AGENT_DISPATCH_SUPERVISE_MODE"] = "serve"
        elif entry == "supervisor":
            environment["AGENT_DISPATCH_SUPERVISE_LABELS"] = "fixture"
    environment["AGENT_DISPATCH_INSTALL_DIR"] = str(root)
    return environment, command, python


@pytest.mark.parametrize("entry", ["coordinator", "supervisor", "registrar"])
def test_dispatch_service_preserves_interpreter_pid_streams_and_exit(
    binding_fixture: dict[str, Any], dispatch_release: Path, entry: str,
    record_property: Callable[[str, object], None],
) -> None:
    environment, command, python = _service(binding_fixture, dispatch_release, entry)
    environment.update(
        UV_PYTHON="foreign", UV_NO_MANAGED_PYTHON="1",
        VIRTUAL_ENV="foreign", UV_CONFIG_FILE="nonexistent.toml",
        UV_WORKING_DIR="nonexistent-directory",
        AGENT_DISPATCH_COMMAND_MODULE="agent_dispatch.board_cli",
    )
    tools = binding_fixture["home"] / "test-bin"
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv
    calls = binding_fixture["home"] / "uv-calls"
    (tools / "uv").write_text(
        '#!/bin/sh\nprintf "%s\\t" "$@" >> "$UV_CALLS"; printf "\\n" >> "$UV_CALLS"\n'
        f'exec {shlex.quote(uv)} "$@"\n',
        encoding="utf-8",
    )
    (tools / "uv").chmod(0o700)
    for name in ("python", "python3"):
        (tools / name).write_text("#!/bin/sh\nexit 96\n", encoding="utf-8")
    environment["UV_CALLS"] = str(calls)
    expected_args = (
        ["serve"] if entry == "coordinator" else
        ["supervise", "serve"] if entry == "registrar" else ["supervise", "--all-repos"]
    )
    samples: dict[str, list[float]] = {"direct": [], "gate": []}
    for route, argv in (("direct", [str(python), "-m", "agent_dispatch", *expected_args]),
                        ("gate", command)):
        for _ in range(2):
            started = time.monotonic()
            result = subprocess.run(
                ["/bin/bash", "-c", 'export ENTRY_PID=$$; exec "$@"', "service", *argv],
                capture_output=True, text=True, input="service-input\n", timeout=30,
                cwd=binding_fixture["home"], env=environment,
            )
            samples[route].append(time.monotonic() - started)
            assert result.returncode == 37, result.stdout + result.stderr
            data = json.loads(result.stdout)
            assert str(data["pid"]) == data["entry"]
            native = route == "gate" and entry != "coordinator"
            assert data["native"] == (data["entry"] if native else None)
            assert data["unit"] == ("agent-dispatch-supervisor.service" if native else None)
            assert data["python"] == str(python)
            assert data["root"] == str(binding_fixture["legacy"])
            assert data["stdin"] == "service-input\n"
            assert data["args"][:2] == expected_args
            assert result.stderr == "runtime-stderr\n"
        if route == "direct":
            assert not calls.exists()
    runtime_calls = [
        line for line in calls.read_text(encoding="utf-8").splitlines()
        if line.split("\t")[-2] == str(python)
    ]
    assert runtime_calls == ["\t".join([
        "python", "find", "--no-project", "--managed-python", "--no-python-downloads",
        str(python), "",
    ])] * 2
    record_property("dispatch_service_ingress_performance", json.dumps({
        "status": "measured", "scenario": entry, "expected": 2, "completed": 2,
        "samples_seconds": samples, "uv_runtime_selections_per_launch": 1,
        "scope": "synthetic CLI completion, first/repeated launch; no daemon readiness or tail qualification",
    }))


@pytest.mark.parametrize("entry", ["coordinator", "supervisor", "registrar"])
def test_dispatch_cold_service_does_not_provision(
    binding_fixture: dict[str, Any], dispatch_release: Path, entry: str,
) -> None:
    environment, command, _ = _service(binding_fixture, dispatch_release, entry, prepared=False)
    result = _run(binding_fixture, environment, command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "service runtime is not prepared" in result.stderr
    assert not (binding_fixture["legacy"] / ".provision-status").exists()
    assert not (binding_fixture["legacy"] / ".provision.lock").exists()


def test_dispatch_supervisor_label_refusal_precedes_runtime_ingress(
    binding_fixture: dict[str, Any], dispatch_release: Path,
) -> None:
    environment, command, _ = _service(binding_fixture, dispatch_release, "no-labels", prepared=False)
    result = _run(binding_fixture, environment, command)
    assert result.returncode == 78, result.stdout + result.stderr
    assert "refusing to run with no opt-in label" in result.stderr
    assert "service runtime is not prepared" not in result.stderr
