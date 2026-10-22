"""Signed wheel construction, real local health and preservation of admission."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from fleet_manager import client_admission as admission
from fleet_manager import client_construction as construction
from fleet_manager.planning import PlanError, digest
from test_artifact_staging import primitive as primitive  # noqa: F401 - pytest fixture
from test_client_admission import (
    database_bytes, identity, independently_sign, retained, root_at, selection,
)
from test_client_distribution import wheel as wheel  # noqa: F401 - pytest fixture
from test_release_auth import artifact_fixture


@pytest.fixture(scope="module")
def signed_wheel(tmp_path_factory, wheel):
    area = tmp_path_factory.mktemp("signed-construction")
    fixture = artifact_fixture(area)
    source = wheel[0]
    fixture["sources"]["components"]["cli"] = str(source)
    fixture["manifest"]["components"]["cli"]["artifact_sha256"] = hashlib.sha256(
        source.read_bytes(),
    ).hexdigest()
    payload = {
        **fixture["proof"]["payload"],
        "release_digest": digest(fixture["manifest"]),
    }
    signed = independently_sign([payload], area)
    fixture["proof"] = {**fixture["proof"], **signed["proofs"][0]}
    fixture["policy"]["keys"][0]["public_key"] = signed["public_key"]
    return fixture


def target(fixture):
    return {
        "schema": "fleet-manager.client-target", "version": 1,
        "authority": "test-authority", "installation": "test-installation", "revision": 1,
        "components": {
            name: {"api": 1, "data_schemas": [1]}
            for name in fixture["manifest"]["roles"]["client"]
        },
    }


def construct(root, fixture, staged, trusted, **kwargs):
    try:
        return construction.construct_client_release(
            root, identity(), selection(fixture), staged,
            current_policy=kwargs.pop("current_policy", lambda: trusted),
            current_target=kwargs.pop("current_target", lambda: target(fixture)),
            clock=lambda: 150, **kwargs,
        )
    except PlanError as error:
        timeout = error.__context__
        if isinstance(timeout, subprocess.TimeoutExpired):
            output = timeout.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            print("CONSTRUCTION_CHILD_TIMEOUT " + json.dumps({
                "timeout_seconds": timeout.timeout,
                "pip_install_started": "Installing collected packages:" in output,
                "pip_install_completed": "Successfully installed " in output,
                "captured_stdout_bytes": len(output.encode("utf-8")),
                "captured_stdout_tail": output[-2048:],
                "captured_stderr_tail": str(timeout.stderr or "")[-2048:],
            }))
        raise


def admit(root, fixture, staged, trusted):
    admission.initialize_client_admission(root, identity())
    admission.admit_client_release(
        root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
    )


def candidate(root, fixture, primitive):
    selected = selection(fixture)
    version = f"client-{selected['sequence']}-{selected['release_digest']}"
    return primitive.version_dir(root / construction.NAMESPACE, version)


@pytest.mark.client_distribution_full
def test_owned_wheel_snapshot_stability(tmp_path, signed_wheel):
    staging = construction.artifact_staging
    source = Path(signed_wheel["sources"]["components"]["cli"])
    expected = source.read_bytes()
    target = tmp_path / source.name
    staging._copy(source, target, hashlib.sha256(expected).hexdigest())
    for sample in range(64):
        before = staging._file_stat(target)
        try:
            assert staging._bytes(target, staging.MAX_ARTIFACT_BYTES) == expected
        except PlanError:
            after = staging._file_stat(target)
            print("CLIENT_SNAPSHOT_CONTROL " + json.dumps({
                "sample": sample,
                "size_changed": before.st_size != after.st_size,
                "mtime_changed": before.st_mtime_ns != after.st_mtime_ns,
                "ctime_changed": before.st_ctime_ns != after.st_ctime_ns,
                "identity_changed": staging._identity(before) != staging._identity(after),
            }), flush=True)
            raise


@pytest.fixture(scope="module")
def built_client(tmp_path_factory, signed_wheel):
    fixture = signed_wheel
    area = tmp_path_factory.mktemp("client lifecycle")
    root = root_at(area)
    start = time.monotonic()
    with retained(fixture, area) as (staged, trusted):
        print("CLIENT_LIFECYCLE_TIMING " + json.dumps({
            "phase": "retained-stage", "seconds": time.monotonic() - start,
        }), flush=True)
        start = time.monotonic()
        admit(root, fixture, staged, trusted)
        print("CLIENT_LIFECYCLE_TIMING " + json.dumps({
            "phase": "durable-admission", "seconds": time.monotonic() - start,
        }), flush=True)
        user = root / "user-config.json"
        user.write_bytes(b"preserved configuration\n")
        before = database_bytes(root)
        def observed(label, operation):
            def invoke(*args, **kwargs):
                start = time.monotonic()
                try:
                    return operation(*args, **kwargs)
                finally:
                    print("CLIENT_LIFECYCLE_TIMING " + json.dumps({
                        "phase": args[2] if label == "_run" else label,
                        "seconds": time.monotonic() - start,
                    }), flush=True)
            return invoke

        with pytest.MonkeyPatch.context() as observer:
            for owner, name in (
                (construction, "_run"), (construction, "_tree"),
                (admission, "_read_state"), (type(staged), "reverify"),
                (type(staged), "read_manifest"), (type(staged), "read_artifacts"),
            ):
                observer.setattr(owner, name, observed(name, getattr(owner, name)))
            report = construct(root, fixture, staged, trusted)
            yield root, fixture, staged, trusted, user, before, report


@pytest.mark.timeout(90)
def test_real_offline_wheel_console_doctor_constructs_a_standard_immutable_slot(
    primitive, built_client,
):
    root, fixture, staged, _, user, before, report = built_client
    slot = candidate(root, fixture, primitive)
    assert slot.is_dir() and not slot.is_relative_to(staged._root)
    assert report["constructed"] and report["healthy"] and not report["reused"]
    assert report["health"]["status"] == "installed"
    assert not any(report[name] for name in (
        "active", "activation_authorized", "live_readiness_verified",
    ))
    assert not primitive.current_version(root / construction.NAMESPACE)
    assert not (root / construction.NAMESPACE / primitive.LAST_KNOWN_GOOD_FILE).exists()
    executable = construction._python(slot)
    assert executable.is_file()
    assert primitive.slot_python(root / construction.NAMESPACE, slot.name) == executable
    library = (slot / "Lib" if os.name == "nt"
               else slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}")
    assert not (library / "site-packages" / "pip").exists()
    assert user.read_bytes() == b"preserved configuration\n"
    assert database_bytes(root) == before


@pytest.mark.timeout(90)
def test_real_offline_immutable_reuse_and_tamper_refusal(primitive, built_client):
    root, fixture, staged, trusted, user, before, report = built_client
    slot = candidate(root, fixture, primitive)
    marker = (slot / primitive.COMPLETE_MARKER).read_bytes()
    completion = (slot / construction.COMPLETION).read_bytes()
    retried = construct(root, fixture, staged, trusted)
    assert retried["reused"] and not retried["mutations_performed"]
    assert retried["runtime_sha256"] == report["runtime_sha256"]
    assert (slot / primitive.COMPLETE_MARKER).read_bytes() == marker
    assert (slot / construction.COMPLETION).read_bytes() == completion
    revoked = copy.deepcopy(trusted)
    revoked["keys"][0]["revoked"] = True
    with pytest.raises(PlanError, match="revoked"):
        construct(root, fixture, staged, trusted, current_policy=lambda: revoked)
    incompatible = target(fixture)
    incompatible["components"]["cli"]["data_schemas"] = [2]
    with pytest.raises(PlanError, match="incompatible"):
        construct(root, fixture, staged, trusted, current_target=lambda: incompatible)
    configuration = slot / "pyvenv.cfg"
    configuration.write_bytes(configuration.read_bytes() + b"\n# changed after completion\n")
    with pytest.raises(PlanError, match="distinct-generation repair"):
        construct(root, fixture, staged, trusted)
    assert slot.is_dir() and (slot / primitive.COMPLETE_MARKER).read_bytes() == marker
    assert user.read_bytes() == b"preserved configuration\n"
    assert database_bytes(root) == before


@pytest.mark.parametrize("change", [
    "missing-component", "unknown-component", "api", "data", "installation", "revision",
])
def test_incompatible_or_incomplete_target_refuses_before_namespace_or_execution(
    tmp_path, primitive, signed_wheel, monkeypatch, change,
):
    fixture = signed_wheel
    root = root_at(tmp_path)
    requested = target(fixture)
    if change == "missing-component":
        requested["components"].clear()
    elif change == "unknown-component":
        requested["components"]["extra"] = {"api": 1, "data_schemas": [1]}
    elif change == "api":
        requested["components"]["cli"]["api"] = 65535
    elif change == "data":
        requested["components"]["cli"]["data_schemas"] = [65535]
    elif change == "installation":
        requested["installation"] = "other"
    else:
        requested["revision"] = 0

    def no_execution(*args, **kwargs):
        raise AssertionError("incompatible client must not execute")

    monkeypatch.setattr(construction, "_run", no_execution)
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        before = database_bytes(root)
        with pytest.raises(PlanError):
            construct(root, fixture, staged, trusted, current_target=lambda: requested)
        assert not (root / construction.NAMESPACE).exists()
        assert database_bytes(root) == before


def test_signed_but_unadmitted_candidate_has_no_construction_effect(tmp_path, primitive, signed_wheel):
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    before = database_bytes(root)
    with retained(signed_wheel, tmp_path) as (staged, trusted):
        with pytest.raises(PlanError, match="exact durably admitted selection"):
            construct(root, signed_wheel, staged, trusted)
    assert database_bytes(root) == before and not (root / construction.NAMESPACE).exists()


def test_unsupported_signed_closure_refuses_before_materializing_artifact_snapshots(
    tmp_path, primitive, signed_wheel, monkeypatch,
):
    fixture = copy.deepcopy(signed_wheel)
    fixture["manifest"]["components"]["cli-2"] = copy.deepcopy(
        fixture["manifest"]["components"]["cli"],
    )
    fixture["manifest"]["roles"]["client"] = ["cli", "cli-2"]
    fixture["sources"]["components"]["cli-2"] = fixture["sources"]["components"]["cli"]
    payload = {**fixture["proof"]["payload"], "release_digest": digest(fixture["manifest"])}
    signed = independently_sign([payload], tmp_path)
    fixture["proof"] = {**fixture["proof"], **signed["proofs"][0]}
    fixture["policy"]["keys"][0]["public_key"] = signed["public_key"]
    root = root_at(tmp_path)
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        before = database_bytes(root)

        def no_snapshots(*args, **kwargs):
            raise AssertionError("unsupported closure must not be materialized")

        monkeypatch.setattr(type(staged), "read_artifacts", no_snapshots)
        with pytest.raises(PlanError, match="requires one portable"):
            construct(root, fixture, staged, trusted)
        assert database_bytes(root) == before
        assert not (root / construction.NAMESPACE).exists()


@pytest.mark.parametrize("error,message", [
    (construction.metadata.PackageNotFoundError("pip"), "construction extra"),
    (PermissionError("private toolchain detail"), "metadata could not be read"),
    (ValueError("private toolchain detail"), "metadata could not be read"),
])
def test_unavailable_owner_toolchain_refuses_before_candidate_mutation(
    tmp_path, primitive, signed_wheel, monkeypatch, error, message,
):
    root = root_at(tmp_path)
    fixture = signed_wheel
    original = construction.metadata.distribution

    def missing(name):
        if name == "pip":
            raise error
        return original(name)

    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        before = database_bytes(root)
        monkeypatch.setattr(construction.metadata, "distribution", missing)
        with pytest.raises(PlanError, match=message) as refused:
            construct(root, fixture, staged, trusted)
        assert "private toolchain detail" not in str(refused.value)
        assert not (root / construction.NAMESPACE).exists()
        assert database_bytes(root) == before


@pytest.mark.parametrize("boundary", ["policy", "target"])
def test_changed_current_inputs_refuse_at_the_execution_boundary_and_preserve_floor(
    tmp_path, primitive, signed_wheel, monkeypatch, boundary,
):
    root = root_at(tmp_path)
    fixture = signed_wheel
    requests = []

    def no_execution(*args, **kwargs):
        raise AssertionError("revalidation must precede execution")

    monkeypatch.setattr(construction, "_run", no_execution)
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        before = database_bytes(root)

        def policy():
            if boundary == "policy":
                requests.append(True)
            value = copy.deepcopy(trusted)
            if boundary == "policy" and len(requests) > 1:
                value["keys"][0]["revoked"] = True
            return value

        def current_target():
            if boundary == "target":
                requests.append(True)
            value = target(fixture)
            if boundary == "target" and len(requests) > 1:
                value["components"]["cli"]["api"] = 65535
            return value

        with pytest.raises(PlanError):
            construct(root, fixture, staged, trusted,
                      current_policy=policy, current_target=current_target)
        assert database_bytes(root) == before
        slot = candidate(root, fixture, primitive)
        assert not slot.exists()


@pytest.mark.timeout(90)
def test_failed_build_retains_owned_candidate_and_same_admitted_retry_succeeds(
    tmp_path, primitive, signed_wheel, monkeypatch,
):
    root = root_at(tmp_path)
    fixture = signed_wheel
    real_run = construction._run
    calls = []

    def interrupted(argv, slot, phase, **kwargs):
        calls.append(phase)
        if phase == "environment construction":
            (slot / "partial-build").write_bytes(b"interrupted")
            raise PlanError("injected build interruption")
        return real_run(argv, slot, phase, **kwargs)

    monkeypatch.setattr(construction, "_run", interrupted)
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        before = database_bytes(root)
        with pytest.raises(PlanError, match="build interruption"):
            construct(root, fixture, staged, trusted)
        assert calls == ["environment construction"]
        partial = candidate(root, fixture, primitive)
        assert partial.is_dir() and (partial / "partial-build").read_bytes() == b"interrupted"
        assert not (partial / primitive.COMPLETE_MARKER).exists()
        assert database_bytes(root) == before
        remove_slot = primitive._remove_slot

        def partial_removal(path, **kwargs):
            (path / "partial-build").unlink()
            return False

        monkeypatch.setattr(primitive, "_remove_slot", partial_removal)
        with pytest.raises(PlanError, match="healthy owned candidate"):
            construct(root, fixture, staged, trusted)
        assert not (partial / "partial-build").exists()
        inputs = list((root / construction.NAMESPACE / "client-inputs").glob("*.json"))
        assert len(inputs) == 1
        assert construction._read(inputs[0])["identity"] == identity()
        monkeypatch.setattr(primitive, "_remove_slot", remove_slot)
        monkeypatch.setattr(construction, "_run", real_run)
        report = construct(root, fixture, staged, trusted)
        assert report["healthy"] and not report["active"]
        assert database_bytes(root) == before


def test_foreign_namespace_or_unfinished_slot_is_preserved(tmp_path, primitive, signed_wheel):
    fixture = signed_wheel
    root = root_at(tmp_path)
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        namespace = root / construction.NAMESPACE
        namespace.mkdir()
        sentinel = namespace / "foreign"
        sentinel.write_bytes(b"not ours")
        with pytest.raises(PlanError):
            construct(root, fixture, staged, trusted)
        assert sentinel.read_bytes() == b"not ours"
        sentinel.unlink()
        namespace.rmdir()
        construction._namespace(root, identity())
        slot = candidate(root, fixture, primitive)
        slot.mkdir(parents=True)
        sentinel = slot / "foreign"
        sentinel.write_bytes(b"not ours")
        with pytest.raises(PlanError, match="no owned construction intent"):
            construct(root, fixture, staged, trusted)
        assert sentinel.read_bytes() == b"not ours"


def test_default_help_has_no_implicit_construction_or_hosting(tmp_path, capsys):
    from fleet_manager.__main__ import main

    previous = sys.modules.get("fleet_manager.gateway_server")
    assert main(["client-state", "construct", "--help"]) == 0
    assert "--target" in capsys.readouterr().out
    assert sys.modules.get("fleet_manager.gateway_server") is previous


def test_sanitized_child_environment_preserves_native_custody_tools_without_host_secrets(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("PATH", "not-the-native-toolchain")
    monkeypatch.setenv("CONSTRUCTION_TEST_SECRET", "must-not-reach-the-child")
    environment = construction._environment(tmp_path)
    assert environment["HOME"] == str(tmp_path / "health-home")
    assert "CONSTRUCTION_TEST_SECRET" not in environment
    assert "not-the-native-toolchain" not in environment["PATH"]
    if os.name == "nt":
        assert environment["PATH"] == os.path.dirname(sys.executable)
    else:
        assert set(os.defpath.split(os.pathsep)) <= set(environment["PATH"].split(os.pathsep))


@pytest.mark.parametrize("member", ["_installer_process.py", "_construction_procutil.py"])
def test_installed_process_resource_replacement_refuses_execution(tmp_path, monkeypatch, member):
    resource = tmp_path / member
    resource.write_bytes(b"raise AssertionError('changed resource executed')\n")
    monkeypatch.setattr(construction, "__file__", str(tmp_path / "client_construction.py"))
    monkeypatch.setattr(construction.artifact_staging, "diagnose_client", lambda: {
        "runtime_resource_sha256s": {member: "0" * 64},
    })
    with pytest.raises(PlanError, match="changed after RECORD admission"):
        construction._process_resource(member)


@pytest.mark.skipif(os.name == "nt", reason="POSIX linked namespace; native Windows reparse coverage is separate")
def test_linked_versions_directory_never_writes_or_removes_foreign_content(
    tmp_path, primitive, signed_wheel,
):
    root = root_at(tmp_path)
    fixture = signed_wheel
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "retained"
    sentinel.write_bytes(b"outside installation")
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        namespace = construction._namespace(root, identity())
        (namespace / "versions").symlink_to(foreign, target_is_directory=True)
        with pytest.raises(PlanError, match="linked"):
            construct(root, fixture, staged, trusted)
    assert sentinel.read_bytes() == b"outside installation"
    assert list(foreign.iterdir()) == [sentinel]


@pytest.mark.parametrize("boundary", ["empty", "temporary-owner", "completed-owner"])
def test_interrupted_namespace_initialization_is_retried_without_foreign_cleanup(
    tmp_path, primitive, boundary,
):
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    temporary = root / ("." + construction.NAMESPACE + "-initializing")
    temporary.mkdir(mode=0o700)
    if boundary != "empty":
        name = construction.OWNER + (".tmp" if boundary == "temporary-owner" else "")
        path = temporary / name
        path.write_bytes(b"interrupted initialization\n")
        path.chmod(0o600)
    namespace = construction._namespace(root, identity())
    assert construction._read(namespace / construction.OWNER)["identity"] == identity()
    assert not temporary.exists()
    assert construction._namespace(root, identity()) == namespace
