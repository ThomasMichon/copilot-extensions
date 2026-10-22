"""Real created-file registration proofs and installed retained client commands."""

import copy
import codecs
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager import client_frontdoor as frontdoor, client_construction as construction
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, independently_sign, retained, root_at, selection
from test_client_construction import (
    admit, built_client as built_client, signed_wheel as signed_wheel, target,
)
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401


def register(root, fixture, staged, trusted, directory, **kwargs):
    return frontdoor.register_client_frontdoor(
        root, identity(), selection(fixture), staged, command_directory=directory,
        current_policy=kwargs.pop("current_policy", lambda: trusted),
        current_target=kwargs.pop("current_target", lambda: target(fixture)),
        clock=lambda: 150, **kwargs,
    )


def command_directory(parent, name="commands"):
    directory = parent / name
    directory.mkdir(mode=0o700)
    return directory


@pytest.fixture
def selected(tmp_path, primitive, signed_wheel, monkeypatch):
    root = root_at(tmp_path)
    directory = command_directory(tmp_path)
    fixture = signed_wheel
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        namespace, slot = completed_candidate(
            root, fixture, staged, trusted, primitive,
            prepare=lambda slot: construction._bootstrap(slot, construction.artifact_staging.diagnose_client()),
        )
        monkeypatch.setattr(construction, "_health", lambda *_args: {"unit_health_stub": True})
        monkeypatch.setattr(frontdoor, "_health", lambda *_args: None)
        activate(root, fixture, staged, trusted)
        yield root, fixture, staged, trusted, directory, namespace, slot


def states(paths):
    return {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()) for path in paths}


def test_created_command_proofs_and_fresh_exact_noop(selected, primitive):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    floor = database_bytes(root)
    selectors = states([*namespace.glob("*version"), namespace / "last-known-good"])
    environment = os.environ.copy()
    result = register(root, fixture, staged, trusted, directory)
    assert result["frontdoor_registered"] and result["active"] and not result["reused"]
    assert result["mutations_performed"] and not result["path_modified"]
    assert not result["live_readiness_verified"]
    commands = list(frontdoor._commands(directory, slot))
    assert result["commands"] == [str(path) for path in commands]
    assert all(str(construction._python(slot)).encode() in path.read_bytes()
               for path in commands if path.suffix != ".cmd")
    if os.name == "nt":
        assert (directory / "fleet-manager.ps1").read_bytes().startswith(codecs.BOM_UTF8)
        assert (directory / "fleet-manager.cmd").read_bytes().isascii()
        assert b"%~dp0fleet-manager.ps1" in (directory / "fleet-manager.cmd").read_bytes()
        assert b"-ExecutionPolicy Bypass" in (directory / "fleet-manager.cmd").read_bytes()
    receipt = construction._read(namespace / frontdoor.REGISTERED)
    workspace = namespace / frontdoor.HISTORY / receipt["publication"]
    publication = primitive._runtime_component("versioned_runtime_publication")
    publication.check_owned(
        workspace / "copy-context.json", [workspace / "copy-forward.jsonl"],
        commands, frontdoor._expected(frontdoor._commands(directory, slot)),
    )
    before = states([*commands, namespace / frontdoor.REGISTERED])
    repeated = register(root, fixture, staged, trusted, directory)
    assert repeated["reused"] and not repeated["mutations_performed"]
    assert states(before) == before
    assert database_bytes(root) == floor and states(selectors) == selectors
    assert os.environ == environment
    assert not (namespace / frontdoor.PENDING).exists()


@pytest.mark.parametrize("change", [
    "unattributed", "same-byte-peer", "modified", "pending", "proof", "directory",
])
def test_foreign_or_unknown_registration_refuses_without_execution(selected, monkeypatch, change, tmp_path):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    commands = frontdoor._commands(directory, slot)
    command = next(iter(commands))
    if change == "unattributed":
        command.write_bytes(b"foreign\n")
    else:
        register(root, fixture, staged, trusted, directory)
        receipt = construction._read(namespace / frontdoor.REGISTERED)
        if change == "same-byte-peer":
            peer = directory / "peer"
            peer.write_bytes(command.read_bytes())
            peer.chmod(command.stat().st_mode)
            os.replace(peer, command)
        elif change == "modified":
            command.write_bytes(b"foreign edits\n")
        elif change == "pending":
            construction._write(namespace / frontdoor.PENDING, {"unknown": True})
        elif change == "proof":
            (namespace / frontdoor.HISTORY / receipt["publication"] / "copy-forward.jsonl").write_bytes(b"{}\n")
        else:
            directory = command_directory(tmp_path, "different")
    floor = database_bytes(root)
    before = states(path for path in commands if path.exists())

    def forbidden(*_args):
        raise AssertionError("foreign registration executed a candidate")

    monkeypatch.setattr(construction, "_health", forbidden)
    with pytest.raises(PlanError):
        register(root, fixture, staged, trusted, directory)
    assert states(before) == before and database_bytes(root) == floor


@pytest.mark.parametrize("change", ["revoked", "incompatible", "runtime", "target-revision"])
def test_fresh_inputs_and_health_cannot_reuse_a_saved_receipt(selected, monkeypatch, change):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    before = states([*frontdoor._commands(directory, slot), namespace / frontdoor.REGISTERED])
    floor = database_bytes(root)
    kwargs = {}
    if change == "revoked":
        policy = copy.deepcopy(trusted)
        policy["keys"][0]["revoked"] = True
        kwargs["current_policy"] = lambda: policy
    elif change == "incompatible":
        incompatible = target(fixture)
        incompatible["components"]["cli"]["api"] = 65535
        kwargs["current_target"] = lambda: incompatible
    elif change == "runtime":
        (slot / "candidate-bytes").write_bytes(b"foreign runtime bytes\n")
    else:
        def changed(*_args):
            requested["revision"] += 1
            return {"unit_health_stub": True}
        requested = target(fixture)
        kwargs["current_target"] = lambda: requested
        monkeypatch.setattr(construction, "_health", changed)
    with pytest.raises(PlanError):
        register(root, fixture, staged, trusted, directory, **kwargs)
    assert states(before) == before and database_bytes(root) == floor


@pytest.mark.parametrize("boundary", ["command", "health", "receipt", "ack"])
def test_interrupted_registration_is_explicit_unknown_and_preserves_recovery(selected, monkeypatch, boundary):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    replace = os.replace
    unlink = Path.unlink
    commands = frontdoor._commands(directory, slot)

    def failed_replace(old, new):
        if new == (next(iter(commands)) if boundary == "command" else namespace / frontdoor.REGISTERED):
            raise OSError("injected registration write failure")
        return replace(old, new)

    def failed_unlink(path, *args, **kwargs):
        if path == namespace / frontdoor.PENDING:
            raise OSError("injected registration acknowledgement failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if boundary in ("command", "receipt"):
            patch.setattr(os, "replace", failed_replace)
        elif boundary == "health":
            def failed(*_args):
                raise PlanError("injected registered-command health failure")
            patch.setattr(frontdoor, "_health", failed)
        else:
            patch.setattr(Path, "unlink", failed_unlink)
        with pytest.raises(PlanError, match="outcome is unknown"):
            register(root, fixture, staged, trusted, directory)
    assert (namespace / frontdoor.PENDING).is_file()
    assert list((namespace / frontdoor.HISTORY).glob("*/copy-forward.jsonl"))
    with pytest.raises(PlanError, match="outcome is unknown"):
        register(root, fixture, staged, trusted, directory)
    if boundary in ("health", "command"):
        assert not any(path.exists() for path in commands)
    else:
        assert all(path.exists() for path in commands)


def test_fresh_foreign_command_at_copy_boundary_is_preserved(selected, monkeypatch):
    root, fixture, staged, trusted, directory, _namespace, slot = selected
    command = next(iter(frontdoor._commands(directory, slot)))

    def health(*_args):
        command.write_bytes(b"peer command\n")
        return {"unit_health_stub": True}

    monkeypatch.setattr(construction, "_health", health)
    with pytest.raises(PlanError):
        register(root, fixture, staged, trusted, directory)
    assert command.read_bytes() == b"peer command\n"


def test_same_byte_foreign_replacement_after_publication_is_not_rolled_back(selected, monkeypatch):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    command = next(iter(frontdoor._commands(directory, slot)))
    peer_state = None

    def replaced(*_args):
        nonlocal peer_state
        peer = directory / "peer"
        peer.write_bytes(command.read_bytes())
        peer.chmod(command.stat().st_mode)
        os.replace(peer, command)
        peer_state = states([command])

    monkeypatch.setattr(frontdoor, "_health", replaced)
    with pytest.raises(PlanError, match="outcome is unknown"):
        register(root, fixture, staged, trusted, directory)
    assert states([command]) == peer_state
    assert (namespace / frontdoor.PENDING).is_file()
    assert not (namespace / frontdoor.REGISTERED).exists()


def test_higher_active_candidate_updates_only_attributed_commands(selected, primitive, tmp_path):
    root, fixture, staged, trusted, directory, namespace, old_slot = selected
    first = register(root, fixture, staged, trusted, directory)
    newer = copy.deepcopy(fixture)
    payload = {**fixture["proof"]["payload"], "sequence": first["admitted"]["sequence"] + 1}
    signed = independently_sign([payload], tmp_path)
    newer["proof"] = {**newer["proof"], **signed["proofs"][0]}
    newer["policy"]["keys"][0]["public_key"] = signed["public_key"]
    from fleet_manager.client_admission import admit_client_release

    with retained(newer, tmp_path) as (next_stage, policy):
        admit_client_release(root, identity(), selection(newer), next_stage, policy, clock=lambda: 150)
        _, next_slot = completed_candidate(
            root, newer, next_stage, policy, primitive,
            prepare=lambda slot: construction._bootstrap(slot, construction.artifact_staging.diagnose_client()),
        )
        activate(root, newer, next_stage, policy)
        floor = database_bytes(root)
        result = register(root, newer, next_stage, policy, directory)
        assert result["candidate"] == next_slot.name and not result["reused"]
        assert all(str(construction._python(next_slot)).encode() in path.read_bytes()
                   for path in frontdoor._commands(directory, next_slot) if path.suffix != ".cmd")
        assert old_slot.is_dir() and (old_slot / primitive.COMPLETE_MARKER).is_file()
        assert database_bytes(root) == floor


@pytest.mark.timeout(90)
def test_installed_retained_frontdoor_outside_checkout_and_exact_rerun(
    primitive, built_client, tmp_path, monkeypatch,
):
    root, fixture, staged, trusted, user, floor, _report = built_client
    directory = command_directory(tmp_path, "client commands \u00e9\u4e2d")
    if os.name == "nt":
        original_environment = construction._environment

        def restricted_environment(slot):
            environment = original_environment(slot)
            assert "PSExecutionPolicyPreference" not in environment
            environment["PSExecutionPolicyPreference"] = "Restricted"
            return environment

        monkeypatch.setattr(construction, "_environment", restricted_environment)
    activate(root, fixture, staged, trusted)
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
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stderr
            reports.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    assert sorted(report["reused"] for report in reports) == [False, True]
    result = reports[0]
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
