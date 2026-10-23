"""Separate native construction, concurrent registration and command contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from fleet_manager import client_construction as construction
from fleet_manager.client_command_removal import unregister_client_frontdoor
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate
from test_client_admission import database_bytes, identity, selection
from test_client_construction import (
    built_client as built_client, signed_wheel as signed_wheel, target,  # noqa: F401
)
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401
from test_client_frontdoor import command_directory, register


@pytest.mark.timeout(90)
def test_installed_frontdoor_construction_prerequisite(built_client):
    root, _fixture, _staged, _trusted, user, floor, report = built_client
    assert report["constructed"] and report["healthy"] and not report["active"]
    assert database_bytes(root) == floor and user.read_bytes() == b"preserved configuration\n"


@pytest.fixture
def restricted_powershell(monkeypatch):
    if os.name == "nt":
        original_environment = construction._environment

        def restricted_environment(slot):
            environment = original_environment(slot)
            assert "PSExecutionPolicyPreference" not in environment
            environment["PSExecutionPolicyPreference"] = "Restricted"
            return environment

        monkeypatch.setattr(construction, "_environment", restricted_environment)


@pytest.fixture
def native_commands(built_client, tmp_path):
    root, *_rest = built_client
    directory = command_directory(tmp_path, "client commands \u00e9\u4e2d")
    yield directory
    result = unregister_client_frontdoor(root, identity(), command_directory=directory)
    assert result["commands_absent"]


@pytest.mark.timeout(90)
def test_independent_installed_frontdoor_owners_serialize_and_reuse(
    primitive, built_client, tmp_path, restricted_powershell, native_commands,
):
    root, fixture, staged, trusted, user, floor, _report = built_client
    directory = native_commands
    started = time.monotonic()
    print("CLIENT_FRONTDOOR_TIMING " + json.dumps({"phase": "activation-start"}), flush=True)
    activate(root, fixture, staged, trusted)
    print("CLIENT_FRONTDOOR_TIMING " + json.dumps({
        "phase": "activation-completed", "seconds": time.monotonic() - started,
    }), flush=True)
    program = """
import json,sys
from pathlib import Path
from fleet_manager.artifact_staging import stage_authenticated_artifacts
from fleet_manager.client_frontdoor import register_client_frontdoor
from fleet_manager import client_construction as construction
from fleet_manager.planning import canonical
value=json.loads(sys.stdin.readline())
if value["restricted_powershell"]:
    original=construction._environment
    def restricted(slot):
        environment=original(slot)
        assert "PSExecutionPolicyPreference" not in environment
        environment["PSExecutionPolicyPreference"]="Restricted"
        return environment
    construction._environment=restricted
with stage_authenticated_artifacts(
    canonical(value["manifest"]),value["proof"],value["policy"],value["sources"],
    authority=value["identity"]["authority"],installation=value["identity"]["installation"],
    role="client",staging_parent=Path(value["stage"]),clock=lambda:150,
) as staged:
    print(json.dumps(register_client_frontdoor(
        Path(value["root"]),value["identity"],value["selection"],staged,
        command_directory=Path(value["directory"]),
        current_policy=lambda:value["policy"],current_target=lambda:value["target"],
        clock=lambda:150,
    )),flush=True)
"""
    processes = []
    reports = []
    try:
        for index in range(2):
            area = command_directory(tmp_path, f"owner-{index}")
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", program], cwd=area,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **FLAGS,
            )
            processes.append(process)
            process.stdin.write(json.dumps({
                **fixture, "policy": trusted, "root": str(root), "identity": identity(),
                "selection": selection(fixture), "target": target(fixture), "stage": str(area),
                "directory": str(directory),
                "restricted_powershell": os.name == "nt",
            }) + "\n")
            process.stdin.close()
            process.stdin = None
        print("CLIENT_FRONTDOOR_TIMING " + json.dumps({
            "phase": "independent-owners-started", "seconds": time.monotonic() - started,
        }), flush=True)
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stderr
            reports.append(json.loads(stdout))
            print("CLIENT_FRONTDOOR_TIMING " + json.dumps({
                "phase": "independent-owner-completed",
                "seconds": time.monotonic() - started, "reused": reports[-1]["reused"],
            }), flush=True)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    assert sorted(report["reused"] for report in reports) == [False, True]
    for report in reports:
        assert report["health"]["status"] == "installed" and report["frontdoor_registered"]
        assert report["mutations_performed"] is not report["reused"]
    assert database_bytes(root) == floor and user.read_bytes() == b"preserved configuration\n"


@pytest.mark.timeout(90)
def test_installed_frontdoor_commands_and_exact_rerun(
    primitive, built_client, restricted_powershell, native_commands,
):
    root, fixture, staged, trusted, user, floor, _report = built_client
    directory = native_commands
    activate(root, fixture, staged, trusted)
    result = register(root, fixture, staged, trusted, directory)
    assert result["health"]["status"] == "installed" and result["frontdoor_registered"]
    environment = os.environ.copy()
    if os.name == "nt":
        environment["PSExecutionPolicyPreference"] = "Restricted"
        powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        control = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-File",
             str(directory / "fleet-manager.ps1"), "--version"],
            cwd=directory, env=environment, capture_output=True, text=True,
            timeout=20, check=False, **FLAGS,
        )
        assert control.returncode != 0 and "UnauthorizedAccess" in control.stderr
        programs = [
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", directory / "fleet-manager.ps1"],
            [os.environ["COMSPEC"], "/d", "/c", directory / "fleet-manager.cmd"],
        ]
    else:
        programs = [[directory / "fleet-manager"]]
    for program in programs:
        health = subprocess.run(
            [*map(str, program), "--version"], cwd=directory, env=environment,
            capture_output=True, text=True, timeout=20, check=False, **FLAGS,
        )
        assert health.returncode == 0, health.stderr
        assert health.stdout.strip() == f"fleet-manager {result['health']['package_version']}"
    repeated = register(root, fixture, staged, trusted, directory)
    assert repeated["reused"] and not repeated["mutations_performed"]
    assert database_bytes(root) == floor and user.read_bytes() == b"preserved configuration\n"
