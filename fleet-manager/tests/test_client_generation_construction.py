"""Installed replacement health while a real predecessor retains its lifecycle custody."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager import (
    client_admission as admission, client_construction as construction,
    client_generations as generations,
)
from fleet_manager.planning import PlanError, digest
from test_artifact_staging import primitive as primitive  # noqa: F401 - pytest fixture
from test_client_admission import (
    database_bytes, identity, independently_sign, retained, root_at, selection,
)
from test_client_construction import (
    built_client as built_client, signed_wheel as signed_wheel, target,
)
from test_client_distribution import FLAGS, run
from test_client_construction_native import failed_doctor_fixture
from test_client_generations import FIRST, REPOSITORY, _HOLD_LEGACY, lineage_at, state


def construct_generation(root, fixture, staged, trusted, generation=FIRST, request="forward-1"):
    return generations.construct_client_generation(
        root, identity(), selection(fixture), staged, generation=generation, request=request,
        current_policy=lambda: trusted, current_target=lambda: target(fixture), clock=lambda: 150,
    )


def direct_health(result, cwd):
    for arguments in (["--version"], ["doctor", "--role", "client"]):
        invoked = run([result["direct_command"], *arguments], cwd=cwd)
        assert invoked.returncode == 0, invoked.stderr
        if arguments[0] == "doctor":
            assert json.loads(invoked.stdout)["status"] == "installed"


@pytest.fixture(scope="module")
def prepared_generation(tmp_path_factory, signed_wheel):
    area = tmp_path_factory.mktemp("f")
    root = lineage_at(area)
    generations._initialize_lineage(root, identity())
    user = root / "user-config"
    user.write_bytes(b"retained generation configuration\n")
    with retained(signed_wheel, area) as (staged, trusted):
        first = construct_generation(root, signed_wheel, staged, trusted)
        yield root, signed_wheel, staged, trusted, user, first


@pytest.mark.timeout(90)
def test_forward_generation_preparation(primitive, prepared_generation):
    root, _, _, _, user, first = prepared_generation
    assert first["installed"] and first["started"] and first["healthy"]
    assert not first["selected"] and not first["registered"]
    slot = primitive.version_dir(
        Path(first["generation_root"]) / construction.NAMESPACE, first["candidate"],
    )
    before = {str(path.relative_to(slot)) for path in slot.rglob("*") if path.is_file()}
    imports = run([
        str(construction._python(slot)), "-I", "-c", "import sys; print(sys.dont_write_bytecode)",
    ], cwd=root)
    assert imports.returncode == 0 and imports.stdout.strip() == "True", imports.stderr
    direct_health(first, root.parent)
    after = {str(path.relative_to(slot)) for path in slot.rglob("*") if path.is_file()}
    assert before == after, {"added": sorted(after - before), "removed": sorted(before - after)}
    assert construction._tree(primitive, slot) == first["runtime_sha256"]
    assert user.read_bytes() == b"retained generation configuration\n"


@pytest.mark.timeout(90)
def test_same_generation_retry_reuses_exact_installed_runtime_and_durable_request(
    primitive, prepared_generation,
):
    root, fixture, staged, trusted, user, first = prepared_generation
    before = database_bytes(root)
    namespace = Path(first["generation_root"]) / construction.NAMESPACE
    slot = primitive.version_dir(namespace, first["candidate"])
    completed = (slot / construction.COMPLETION).read_bytes()
    retried = construct_generation(root, fixture, staged, trusted)
    assert retried["reused"] and retried["healthy"]
    assert retried["direct_command"] == first["direct_command"]
    assert retried["generation_root"] == first["generation_root"]
    assert database_bytes(root) == before
    assert (slot / construction.COMPLETION).read_bytes() == completed
    assert user.read_bytes() == b"retained generation configuration\n"
    direct_health(retried, root.parent)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("release", ["same", "higher", "failed-health"])
def test_forward_reinstall_is_independent_and_failed_health_preserves_prior_runtime(
    tmp_path_factory, primitive, prepared_generation, release,
):
    root, fixture, staged, trusted, user, first = prepared_generation
    area = tmp_path_factory.mktemp("n")
    previous_namespace = Path(first["generation_root"]) / construction.NAMESPACE
    previous_slot = primitive.version_dir(previous_namespace, first["candidate"])
    completion = (previous_slot / construction.COMPLETION).read_bytes()
    number = {"same": 2, "higher": 3, "failed-health": 4}[release]
    generation, request = str(number) * 32, "forward-" + str(number)
    if release == "failed-health":
        replacement = failed_doctor_fixture(fixture, area)
    else:
        replacement = copy.deepcopy(fixture)
    if release != "same":
        replacement["manifest"]["id"] += "-forward"
        payload = {
            **replacement["proof"]["payload"],
            "sequence": selection(fixture)["sequence"] + number - 2,
            "release_digest": digest(replacement["manifest"]),
        }
        signed = independently_sign([payload], area)
        replacement["proof"] = {**replacement["proof"], **signed["proofs"][0]}
        replacement["policy"]["keys"][0]["public_key"] = signed["public_key"]
    with (
        primitive.retention_lease(previous_namespace, first["candidate"]),
        retained(replacement, area) as (new_stage, new_policy),
    ):
        if release == "failed-health":
            with pytest.raises(PlanError, match="local diagnostics failed with exit status 23"):
                construct_generation(
                    root, replacement, new_stage, new_policy, generation, request,
                )
            failed_root = root / ("generation-" + generation)
            admitted = state(root)[0]
            failed_candidate = (
                f"client-{admitted['sequence']}-{admitted['release_digest']}"
            )
            failed_slot = primitive.version_dir(
                failed_root / construction.NAMESPACE, failed_candidate,
            )
            assert failed_slot.is_dir() and not (failed_slot / primitive.COMPLETE_MARKER).exists()
        else:
            installed = construct_generation(
                root, replacement, new_stage, new_policy, generation, request,
            )
            assert installed["installed"] and installed["started"] and installed["healthy"]
            assert not installed["selected"] and not installed["registered"]
            assert installed["generation_root"] != first["generation_root"]
            assert installed["direct_command"] != first["direct_command"]
            assert (installed["candidate"] == first["candidate"]) is (release == "same")
            direct_health(installed, area)
            if release == "same":
                before = database_bytes(root)
                losing_retry = construct_generation(root, fixture, staged, trusted)
                assert losing_retry["reused"] and losing_retry["started"]
                assert losing_retry["direct_command"] == first["direct_command"]
                assert database_bytes(root) == before
                assert state(root, fences=True)[3]["current_generation"] == generation
        direct_health(first, area)
        assert (previous_slot / construction.COMPLETION).read_bytes() == completion
    admitted, _, reservations = state(root)
    assert admitted["sequence"] == selection(replacement)["sequence"]
    assert reservations[FIRST]["selection"] == selection(fixture)
    assert reservations[generation]["selection"] == selection(replacement)
    assert user.read_bytes() == b"retained generation configuration\n"


@pytest.mark.timeout(90)
def test_retired_generation_stays_directly_usable_but_cannot_reconstruct_while_successor_starts(
    tmp_path_factory, primitive, signed_wheel,
):
    area = tmp_path_factory.mktemp("r")
    root = lineage_at(area)
    generations._initialize_lineage(root, identity())
    with retained(signed_wheel, area) as (staged, trusted):
        first = construct_generation(root, signed_wheel, staged, trusted)
        namespace = Path(first["generation_root"]) / construction.NAMESPACE
        slot = primitive.version_dir(namespace, first["candidate"])
        completed = (slot / construction.COMPLETION).read_bytes()
        admitted, _, reservations = state(root)
        with primitive.retention_lease(namespace, first["candidate"]):
            tombstone = generations._retire_generation(
                root, identity(), reservations[FIRST], clock=lambda: 160,
            )
            before = database_bytes(root)
            with pytest.raises(PlanError, match="retired"):
                construct_generation(root, signed_wheel, staged, trusted)
            assert database_bytes(root) == before
            second = construct_generation(
                root, signed_wheel, staged, trusted, "2" * 32, "forward-2",
            )
            direct_health(first, area)
            direct_health(second, area)
        assert (slot / construction.COMPLETION).read_bytes() == completed
        assert state(root)[0] == admitted
        assert state(root, fences=True)[3] == {
            "current_generation": "2" * 32, "selected_generation": None,
            "retired": {FIRST: tombstone},
        }


@pytest.mark.timeout(90)
def test_new_generation_installed_console_while_initialized_predecessor_custody_is_held(
    tmp_path, tmp_path_factory, primitive, signed_wheel,
):
    fixture = signed_wheel
    area = tmp_path_factory.mktemp("g")
    legacy = root_at(area)
    root = lineage_at(area)
    admission.initialize_client_admission(legacy, identity())
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _HOLD_LEGACY, str(REPOSITORY), str(legacy),
         json.dumps(identity())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **FLAGS,
    )
    try:
        assert process.stdout.readline().strip() == "LEGACY_CONSTRUCTION_CUSTODY"
        before = database_bytes(legacy)
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
        with retained(fixture, area) as (staged, trusted):
            try:
                result = generations.construct_client_generation(
                    root, identity(), selection(fixture), staged,
                    generation=FIRST, request="forward-1",
                    current_policy=lambda: trusted, current_target=lambda: target(fixture),
                    clock=lambda: 150,
                )
            except PlanError as error:
                print("GENERATION_FAILURE_CONTEXT", repr(error.__context__))
                raise
        assert result["installed"] and result["started"] and result["healthy"]
        assert not result["selected"] and not result["registered"] and not result["active"]
        direct = Path(result["direct_command"])
        version = run([str(direct), "--version"], cwd=tmp_path)
        assert version.returncode == 0, version.stderr
        diagnosed = run([str(direct), "doctor", "--role", "client"], cwd=tmp_path)
        assert diagnosed.returncode == 0, diagnosed.stderr
        assert json.loads(diagnosed.stdout)["status"] == "installed"
        assert process.poll() is None and database_bytes(legacy) == before
        assert state(root)[0]["sequence"] == selection(fixture)["sequence"]
        assert not (Path(result["generation_root"]) / admission.DATABASE).exists()
        process.stdin.write("\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "LEGACY_STILL_OWNED"
        assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.timeout(90)
def test_installed_predecessor_preparation(primitive, built_client):
    root, _, _, _, _, _, report = built_client
    assert report["constructed"] and report["healthy"]
    slot = primitive.version_dir(root / construction.NAMESPACE, report["candidate"])
    assert (slot / construction.COMPLETION).is_file()


@pytest.mark.timeout(90)
def test_real_installed_replacement_starts_with_legacy_construction_and_retention_still_held(
    tmp_path, tmp_path_factory, primitive, built_client,
):
    legacy, fixture, staged, trusted, user, _, old = built_client
    root = lineage_at(tmp_path_factory.mktemp("r"))
    legacy_before = database_bytes(legacy)
    user_before = user.read_bytes()
    script = _HOLD_LEGACY.replace(
        "print('LEGACY_CONSTRUCTION_CUSTODY',flush=True)",
        "retained=runtime.retention_lease(path/'client-runtime',sys.argv[4])\n"
        "        retained.__enter__()\n"
        "        print('LEGACY_CONSTRUCTION_CUSTODY',flush=True)",
    ).replace(
        "print('LEGACY_STILL_OWNED',flush=True)",
        "print('LEGACY_STILL_OWNED',flush=True)\n        retained.__exit__(None,None,None)",
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(REPOSITORY), str(legacy),
         json.dumps(identity()), old["candidate"]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **FLAGS,
    )
    try:
        assert process.stdout.readline().strip() == "LEGACY_CONSTRUCTION_CUSTODY"
        namespace = legacy / construction.NAMESPACE
        pending = namespace / "client-activation-pending.json"
        pending.write_bytes(b"malformed old publication evidence")
        removing = namespace / construction.REMOVING
        removing.write_bytes(b"malformed old removal evidence")
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
        result = generations.construct_client_generation(
            root, identity(), selection(fixture), staged, generation=FIRST, request="forward-1",
            current_policy=lambda: trusted, current_target=lambda: target(fixture), clock=lambda: 150,
        )
        assert result["installed"] and result["started"] and result["healthy"]
        assert not result["selected"] and not result["registered"] and not result["active"]
        assert result["predecessor_cleanup"] == "not-attempted"
        direct = Path(result["direct_command"])
        assert direct.is_file() and direct.is_relative_to(Path(result["generation_root"]))
        assert not direct.is_relative_to(legacy) and not direct.is_relative_to(staged._root)
        for args in (["--version"], ["doctor", "--role", "client"]):
            invoked = run([str(direct), *args], cwd=tmp_path)
            assert invoked.returncode == 0, invoked.stderr
            if args[0] == "doctor":
                assert json.loads(invoked.stdout)["status"] == "installed"
        assert process.poll() is None
        assert database_bytes(legacy) == legacy_before and user.read_bytes() == user_before
        assert pending.read_bytes() == b"malformed old publication evidence"
        assert removing.read_bytes() == b"malformed old removal evidence"
        assert not (Path(result["generation_root"]) / admission.DATABASE).exists()
        with pytest.raises(PlanError):
            admission.initialize_client_admission(Path(result["generation_root"]), identity())
        assert state(root)[0]["sequence"] == old["admitted"]["sequence"]
        process.stdin.write("\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "LEGACY_STILL_OWNED"
        assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
