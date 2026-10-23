"""Installed CLI recovery and independent owners; no mocked health or construction."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from fleet_manager import client_activation as activation, client_admission as admission
from fleet_manager import client_construction as construction
from fleet_manager.client_command_removal import unregister_client_frontdoor
from fleet_manager.planning import canonical
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate
from test_client_admission import database_bytes, identity, independently_sign, retained, selection
from test_client_construction import built_client as built_client, signed_wheel as signed_wheel, target  # noqa: F401
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401
from test_client_frontdoor import command_directory
from test_client_recovery import decision_for, higher_release


@pytest.mark.timeout(90)
def test_retained_recovery_construction_prerequisite(built_client):
    root, _fixture, _staged, _trusted, user, floor, report = built_client
    assert report["healthy"] and report["constructed"] and not report["active"]
    assert database_bytes(root) == floor and user.read_bytes() == b"preserved configuration\n"


@pytest.fixture(scope="module")
def native_recovery(built_client, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("native-retained-recovery")
    started = time.monotonic()
    root, old, staged, trusted, user, _floor, _report = built_client
    directory = command_directory(tmp_path, "recovered commands \u00e9\u4e2d")
    activate(root, old, staged, trusted)
    print("RETAINED_RECOVERY_NATIVE " + json.dumps({
        "phase": "prior-healthy-activation", "seconds": time.monotonic() - started,
    }), flush=True)
    namespace = root / construction.NAMESPACE
    prior = construction._read(namespace / activation.ACTIVE)
    slot = namespace / "versions" / prior["candidate"]
    completion = construction._read(slot / construction.COMPLETION)
    newer = higher_release(old, tmp_path)
    with retained(newer, tmp_path) as (new_stage, policy):
        admission.admit_client_release(
            root, identity(), selection(newer), new_stage, policy, clock=lambda: 150,
        )
    decision = decision_for(root, prior, completion, directory)
    print("RETAINED_RECOVERY_NATIVE " + json.dumps({
        "phase": "higher-floor-and-decision", "seconds": time.monotonic() - started,
    }), flush=True)
    floor = database_bytes(root)
    # Fresh synthetic signer/policy admits the same immutable retained release
    # at the real wall clock used by the unmodified installed CLI.
    now = int(time.time())
    current = copy.deepcopy(old)
    payload = {**old["proof"]["payload"], "issued_at": now - 60, "expires_at": now + 3600}
    signed = independently_sign([payload], tmp_path)
    current["proof"] = {**old["proof"], **signed["proofs"][0]}
    policy = current["policy"]
    policy.update(valid_from=now - 120, valid_until=now + 7200)
    policy["keys"][0].update(
        public_key=signed["public_key"], valid_from=now - 120, valid_until=now + 7200,
    )
    stage_parent = command_directory(tmp_path, "independent stages")
    values = {
        "identity": identity(), "selection": selection(old), "release": current["manifest"],
        "proof": current["proof"], "policy": policy, "sources": current["sources"],
        "target": target(old), "recovery-decision": decision,
    }
    paths = {}
    for name, value in values.items():
        path = tmp_path / (name + ".json")
        path.write_bytes(canonical(value))
        paths[name] = path
    arguments = [
        sys.executable, "-I", "-B", "-m", "fleet_manager", "client-state", "recover-retained",
        "--root", str(root), "--command-directory", str(directory),
        "--staging-parent", str(stage_parent),
    ]
    for name, path in paths.items():
        arguments.extend(["--" + name, str(path)])
    print("RETAINED_RECOVERY_NATIVE " + json.dumps({
        "phase": "independent-cli-inputs", "seconds": time.monotonic() - started,
    }), flush=True)
    yield arguments, root, namespace, directory, user, floor, decision
    result = unregister_client_frontdoor(root, identity(), command_directory=directory)
    assert result["commands_absent"]


def assert_report(result, root, user, floor, decision):
    assert result["schema"] == "fleet-manager.client-retained-recovery"
    assert result["active"] and result["frontdoor_registered"]
    assert result["admitted"]["sequence"] == 11
    assert result["recovered_active"] == decision["retained_active"]["candidate"]
    assert result["health"]["status"] == "installed"
    assert not result["admission_modified"] and not result["constructed"]
    assert database_bytes(root) == floor and user.read_bytes() == b"preserved configuration\n"


@pytest.mark.timeout(90)
def test_installed_retained_recovery_cli_transition(primitive, native_recovery):
    arguments, root, namespace, directory, user, floor, decision = native_recovery
    existing = construction._read(namespace / activation.ACTIVE)
    already = existing["publication"] != decision["retained_active"]["publication"]
    started = time.monotonic()
    result = subprocess.run(
        arguments, cwd=directory, capture_output=True, text=True, timeout=60, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    print("RETAINED_RECOVERY_NATIVE " + json.dumps({
        "phase": "cli-transition", "seconds": time.monotonic() - started,
    }), flush=True)
    report = json.loads(result.stdout)
    assert_report(report, root, user, floor, decision)
    assert report["reused"] is already
    assert primitive.current_version(namespace) == decision["retained_active"]["candidate"]


@pytest.mark.timeout(90)
def test_installed_retained_recovery_commands_and_exact_rerun(primitive, native_recovery):
    arguments, root, _namespace, directory, user, floor, decision = native_recovery
    reports = []
    for _attempt in range(2):
        result = subprocess.run(
            arguments, cwd=directory, capture_output=True, text=True, timeout=60, **FLAGS,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert_report(report, root, user, floor, decision)
        reports.append(report)
    assert reports[-1]["reused"]
    if os.name == "nt":
        programs = [
            [Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe",
             "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
             directory / "fleet-manager.ps1"],
            [os.environ["COMSPEC"], "/d", "/c", directory / "fleet-manager.cmd"],
        ]
    else:
        programs = [[directory / "fleet-manager"]]
    for program in programs:
        result = subprocess.run(
            [*map(str, program), "doctor", "--role", "client"],
            cwd=directory, capture_output=True, text=True, timeout=20, **FLAGS,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == reports[-1]["health"]


@pytest.mark.timeout(90)
def test_independent_installed_retained_recovery_owners(primitive, native_recovery):
    arguments, root, namespace, directory, user, floor, decision = native_recovery
    existing = construction._read(namespace / activation.ACTIVE)
    already = existing["publication"] != decision["retained_active"]["publication"]
    processes, reports, deadlines = [], [], []
    started = time.monotonic()
    try:
        for _owner in range(2):
            deadlines.append(time.monotonic() + 60)
            processes.append(subprocess.Popen(
                arguments, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **FLAGS,
            ))
        print("RETAINED_RECOVERY_NATIVE " + json.dumps({
            "phase": "independent-owners-started", "seconds": time.monotonic() - started,
        }), flush=True)
        for process, deadline in zip(processes, deadlines, strict=True):
            output, errors = process.communicate(timeout=max(0, deadline - time.monotonic()))
            assert process.returncode == 0, errors
            report = json.loads(output)
            assert_report(report, root, user, floor, decision)
            reports.append(report)
            print("RETAINED_RECOVERY_NATIVE " + json.dumps({
                "phase": "independent-owner-completed", "seconds": time.monotonic() - started,
                "reused": report["reused"],
            }), flush=True)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    assert sorted(report["reused"] for report in reports) == [already, True]
