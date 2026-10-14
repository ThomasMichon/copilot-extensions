"""Real cryptography/filesystem staging; installed delivery is proved separately."""

from contextlib import contextmanager
import copy
import importlib.util
import hashlib
import os
import json
import shutil
from pathlib import Path
import socket
import sys
import threading

import pytest

import fleet_manager.artifact_staging as staging
import fleet_manager.staging_cleanup as cleanup
from fleet_manager.planning import PlanError, canonical
from test_release_auth import artifact_fixture
from test_client_distribution import run, wheel as shared_wheel  # noqa: F401 - pytest fixture


@pytest.fixture
def primitive(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "libs" / "versioned-runtime" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("actual_staging_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    @contextmanager
    def resource():
        yield module

    monkeypatch.setattr(staging, "_primitive", resource)
    yield module
    sys.modules.pop(spec.name, None)


def stage(fixture, parent, *, role="client", clock=lambda: 150):
    trusted = copy.deepcopy(fixture["policy"])
    trusted["role"] = role
    trusted["keys"][0]["roles"] = ["client", "gateway", "node"]
    return staging.stage_authenticated_artifacts(
        canonical(fixture["manifest"]), fixture["proof"], trusted, fixture["sources"],
        authority="test-authority", installation="test-installation", role=role,
        staging_parent=parent, clock=clock,
    ), trusted


@pytest.mark.parametrize("role", ["client", "gateway", "node"])
def test_actual_signed_selected_bytes_retained_without_runtime_activation(
    tmp_path, primitive, monkeypatch, role,
):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    sentinel = parent / "user-config.json"
    sentinel.write_bytes(b"retained custom config\n")
    inputs = copy.deepcopy(fixture)

    def no_network(*args, **kwargs):
        raise AssertionError("staging must remain offline")

    monkeypatch.setattr(socket, "socket", no_network)
    context, trusted = stage(fixture, parent, role=role)
    with context as frozen:
        evidence = frozen.reverify(trusted, clock=lambda: 151)
        selected = sorted(fixture["manifest"]["roles"][role])
        assert sorted(frozen.artifacts) == selected
        assert evidence["authenticity_verified"] and evidence["artifact_integrity_verified"]
        assert evidence["retention_held"] and evidence["mutations_performed"]
        assert evidence["sequence"] == 10 and evidence["verified_at"] == 151
        assert not evidence["activation_authorized"] and not evidence["live_readiness_verified"]
        assert evidence["release_digest"] == frozen.release_digest
        for name, path in frozen._artifacts:
            assert frozen.read_artifact(name, trusted, clock=lambda: 151) == (
                Path(fixture["sources"]["components"][name]).read_bytes()
            )
            assert path.name == Path(fixture["sources"]["components"][name]).name
        assert not primitive.current_version(frozen._root)
        assert not primitive.read_marker(frozen._root, frozen._slot.name)
        assert not primitive.slot_python(frozen._root, frozen._slot.name)
        assert primitive.gc(frozen._root) == []
        assert frozen._slot.is_dir()
        assert not hasattr(frozen, "slot")
        with pytest.raises(TypeError):
            frozen.artifacts[0] = "unowned"
    assert not frozen._slot.exists()
    assert list(parent.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"retained custom config\n"
    assert fixture == inputs
    with pytest.raises(PlanError, match="retention has ended"):
        frozen.reverify(trusted, clock=lambda: 152)
    with pytest.raises(PlanError, match="retention has ended"):
        frozen.read_artifact(selected[0], trusted, clock=lambda: 152)


def test_original_mutation_cannot_replace_frozen_selected_bytes(tmp_path, primitive):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    with context as frozen:
        selected = next(iter(frozen.artifacts))
        original = Path(fixture["sources"]["components"][selected])
        original.write_bytes(b"new untrusted source bytes")
        assert frozen.reverify(trusted, clock=lambda: 151)["artifact_integrity_verified"]
        assert frozen.read_artifact(selected, trusted, clock=lambda: 151) != original.read_bytes()


def test_replacement_after_reverify_cannot_change_consumed_snapshot(tmp_path, primitive, monkeypatch):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    with context as frozen:
        component, path = frozen._artifacts[0]
        admitted = path.read_bytes()
        original = staging.StagedArtifacts._reverify
        replaced = []

        def replace_after_verification(value, policy, *, clock):
            report = original(value, policy, clock=clock)
            path.rename(path.with_name("moved-original"))
            path.write_bytes(b"unverified replacement bytes")
            replaced.append(True)
            return report

        monkeypatch.setattr(staging.StagedArtifacts, "_reverify", replace_after_verification)
        consumed = frozen.read_artifact(component, trusted, clock=lambda: 151)
        assert replaced and type(consumed) is bytes
        assert consumed == admitted and consumed != path.read_bytes()
        monkeypatch.setattr(staging.StagedArtifacts, "_reverify", original)
        with pytest.raises(PlanError, match="digest mismatch"):
            frozen.read_artifact(component, trusted, clock=lambda: 151)
    assert consumed == admitted


def test_root_replacement_between_reverify_and_read_refuses(tmp_path, primitive):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    with context as frozen:
        component, path = frozen._artifacts[0]
        assert frozen.reverify(trusted, clock=lambda: 151)["artifact_integrity_verified"]
        moved = parent / "moved-original"
        try:
            frozen._root.rename(moved)
        except OSError as error:
            assert os.name == "nt" and getattr(error, "winerror", None) in {5, 32}
            assert frozen.read_artifact(component, trusted, clock=lambda: 151)
            return
        try:
            replacement = frozen._root / path.relative_to(frozen._root)
            replacement.parent.mkdir(parents=True)
            replacement.write_bytes(b"unverified replacement bytes")
            with pytest.raises(PlanError, match="ownership changed"):
                frozen.read_artifact(component, trusted, clock=lambda: 151)
            assert replacement.read_bytes() == b"unverified replacement bytes"
        finally:
            frozen._root.rename(parent / "preserved-replacement")
            moved.rename(frozen._root)
    assert (parent / "preserved-replacement").is_dir()


@pytest.mark.parametrize("change", ["artifact", "revoke", "floor", "expiry", "unselected"])
def test_snapshot_reads_require_current_trust_and_selected_authentic_bytes(
    tmp_path, primitive, change,
):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    with context as frozen:
        component, path = frozen._artifacts[0]
        now = 151
        if change == "artifact":
            path.write_bytes(b"tampered")
        elif change == "revoke":
            trusted["keys"][0]["revoked"] = True
        elif change == "floor":
            trusted["minimum_sequence"] = 11
        elif change == "expiry":
            now = 200
        else:
            component = "unselected"
        with pytest.raises(PlanError):
            frozen.read_artifact(component, trusted, clock=lambda: now)


@pytest.mark.parametrize("operation", ["reverify", "read"])
def test_context_shutdown_waits_for_inflight_reverification(
    tmp_path, primitive, monkeypatch, operation,
):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    entered = threading.Event()
    release = threading.Event()
    shutdown = threading.Event()
    failures = []
    results = []
    original_canonical = staging.canonical
    original_close = staging.StagedArtifacts._close

    def serialization(value):
        if isinstance(value, dict) and value.get("schema") == "fleet-manager.staged-artifacts":
            if threading.current_thread().name == "stage-verification-fixture":
                entered.set()
                assert release.wait(5), "verification fixture was not released"
        return original_canonical(value)

    def close(value):
        shutdown.set()
        original_close(value)

    monkeypatch.setattr(staging, "canonical", serialization)
    monkeypatch.setattr(staging.StagedArtifacts, "_close", close)
    context, trusted = stage(fixture, parent)
    frozen = context.__enter__()
    shutdown.clear()

    def verify():
        try:
            if operation == "reverify":
                results.append(frozen.reverify(trusted, clock=lambda: 151))
            else:
                results.append(frozen.read_artifact(frozen.artifacts[0], trusted, clock=lambda: 151))
        except BaseException as error:
            failures.append(error)

    def allow_completion():
        try:
            assert shutdown.wait(5), "context owner did not start shutdown"
            assert not frozen._closed.is_set()
            assert frozen._slot.is_dir()
        except BaseException as error:
            failures.append(error)
        finally:
            release.set()

    worker = threading.Thread(target=verify, name="stage-verification-fixture")
    controller = threading.Thread(target=allow_completion)
    worker.start()
    try:
        assert entered.wait(5), "verification did not reach result serialization"
        controller.start()
        context.__exit__(None, None, None)
    finally:
        release.set()
        worker.join(5)
        if controller.ident is not None:
            controller.join(5)
    assert not failures
    assert not worker.is_alive() and not controller.is_alive()
    assert len(results) == 1
    if operation == "reverify":
        assert results[0]["retention_held"]
    else:
        assert type(results[0]) is bytes and results[0]
    assert frozen._closed.is_set() and not frozen._slot.exists()
    with pytest.raises(PlanError, match="retention has ended"):
        frozen.reverify(trusted, clock=lambda: 152)


@pytest.mark.parametrize("change", ["artifact", "manifest", "revoke", "floor", "expiry"])
def test_reverification_requires_current_policy_time_and_unchanged_bytes(
    tmp_path, primitive, change,
):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    with context as frozen:
        now = 151
        if change == "artifact":
            frozen._artifacts[0][1].write_bytes(b"tampered")
        elif change == "manifest":
            (frozen._slot / "release.json").write_bytes(b"{}")
        elif change == "revoke":
            trusted["keys"][0]["revoked"] = True
        elif change == "floor":
            trusted["minimum_sequence"] = 11
        else:
            now = 200
        with pytest.raises(PlanError):
            frozen.reverify(trusted, clock=lambda: now)
    assert list(parent.iterdir()) == []


def test_expiry_after_copy_refuses_yield_and_cleans_only_owned_stage(tmp_path, primitive):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    sentinel = parent / "unrelated"
    sentinel.write_bytes(b"preserved")
    ticks = iter((150, 200))
    context, _ = stage(fixture, parent, clock=lambda: next(ticks))
    with pytest.raises(PlanError, match="signature is not currently valid"):
        with context:
            pytest.fail("expired staged admission must not yield")
    assert list(parent.iterdir()) == [sentinel]


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, RuntimeError])
def test_body_interruption_releases_retention_and_preserves_input_config(
    tmp_path, primitive, interruption,
):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    with pytest.raises(interruption):
        with context as frozen:
            raise interruption("controlled interruption")
    assert list(parent.iterdir()) == []
    with pytest.raises(PlanError, match="retention has ended"):
        frozen.reverify(trusted, clock=lambda: 151)


def test_partial_copy_interruption_cleans_only_owned_spool(tmp_path, primitive, monkeypatch):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    sentinel = parent / "unrelated"
    sentinel.write_bytes(b"preserved")
    original = staging._copy
    copied = []

    def interrupted(*args):
        original(*args)
        copied.append(args[1])
        raise KeyboardInterrupt("controlled partial-copy interruption")

    monkeypatch.setattr(staging, "_copy", interrupted)
    context, _ = stage(fixture, parent, role="gateway")
    with pytest.raises(KeyboardInterrupt):
        with context:
            pytest.fail("interrupted stage must not yield")
    assert len(copied) == 1 and not copied[0].exists()
    assert list(parent.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserved"


def test_cleanup_never_deletes_a_replacement_directory(tmp_path, primitive):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, trusted = stage(fixture, parent)
    frozen = context.__enter__()
    moved = parent / "retained-original"
    try:
        frozen._root.rename(moved)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) not in {5, 32}:
            context.__exit__(None, None, None)
            raise
        assert frozen.reverify(trusted, clock=lambda: 151)["retention_held"]
        context.__exit__(None, None, None)
        assert list(parent.iterdir()) == []
        return
    frozen._root.mkdir()
    foreign = frozen._root / "foreign-content"
    foreign.write_bytes(b"must be preserved")
    with pytest.raises(PlanError, match="cleanup ownership is unconfirmed"):
        context.__exit__(None, None, None)
    assert foreign.read_bytes() == b"must be preserved"
    assert moved.is_dir()
    with pytest.raises(PlanError, match="retention has ended"):
        frozen.reverify(trusted, clock=lambda: 151)


def test_replacement_after_outer_cleanup_check_is_not_deleted(tmp_path, primitive, monkeypatch):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, _ = stage(fixture, parent)
    frozen = context.__enter__()
    moved = parent / "retained-original"
    original = staging.remove_owned_stage

    def race(root, identity):
        root.rename(moved)
        root.mkdir()
        (root / "foreign-content").write_bytes(b"must be preserved")
        original(root, identity)

    monkeypatch.setattr(staging, "remove_owned_stage", race)
    with pytest.raises(PlanError, match="ownership is unconfirmed"):
        context.__exit__(None, None, None)
    if os.name == "nt":
        preserved = frozen._root
    else:
        quarantines = list(parent.glob(".fleet-stage-cleanup-*"))
        assert len(quarantines) == 1
        preserved = quarantines[0] / "owned-root"
    assert (preserved / "foreign-content").read_bytes() == b"must be preserved"
    assert (moved / "versions").is_dir()


def test_cleanup_root_is_pinned_during_recursive_traversal(tmp_path, monkeypatch):
    root = tmp_path / "owned-root"
    root.mkdir()
    (root / "owned-file").write_bytes(b"owned")
    expected = staging._directory(root)
    moved = tmp_path / "moved-original"
    original = cleanup.os.scandir
    attempted = []

    def raced_scan(path):
        if not attempted:
            attempted.append(True)
            if os.name == "nt":
                with pytest.raises(OSError) as error:
                    root.rename(moved)
                assert getattr(error.value, "winerror", None) in {5, 32}
            else:
                root.mkdir()
                (root / "foreign-content").write_bytes(b"must be preserved")
        return original(path)

    monkeypatch.setattr(cleanup.os, "scandir", raced_scan)
    if os.name == "nt":
        cleanup.remove_owned_stage(root, expected)
        assert attempted and not root.exists() and not moved.exists()
    else:
        cleanup.remove_owned_stage(root, expected)
        assert attempted and not moved.exists()
        assert (root / "foreign-content").read_bytes() == b"must be preserved"
        assert list(root.iterdir()) == [root / "foreign-content"]
        assert not list(tmp_path.glob(".fleet-stage-cleanup-*"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows handle identity contract")
def test_windows_cleanup_admits_native_full_width_root_and_child_identities(tmp_path, monkeypatch):
    root = tmp_path / "owned-root"
    root.mkdir()
    (root / "owned-file").write_bytes(b"owned")
    child = root / "owned-child"
    child.mkdir()
    (child / "nested-file").write_bytes(b"owned")
    original = cleanup._windows_identity

    def full_width(kernel, handle):
        volume, identifier = original(kernel, handle)
        return volume, identifier | (1 << 96)

    monkeypatch.setattr(cleanup, "_windows_identity", full_width)
    expected = staging._directory(root)
    assert expected[1] >= 1 << 96
    assert expected != (root.stat().st_dev, root.stat().st_ino)
    cleanup.remove_owned_stage(root, expected)
    assert not root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX quarantine removal contract")
@pytest.mark.parametrize("entry", ["file", "child", "root"])
def test_published_replacement_at_final_removal_is_preserved(tmp_path, monkeypatch, entry):
    root = tmp_path / "owned-root"
    root.mkdir()
    (root / "owned-file").write_bytes(b"owned")
    (root / "owned-child").mkdir()
    expected = staging._directory(root)
    original = cleanup.os.unlink if entry == "file" else cleanup.os.rmdir
    target = {"file": "owned-file", "child": "owned-child", "root": "owned-root"}[entry]
    attempted = []

    def replace_before_removal(name, *, dir_fd):
        if name == target and not attempted:
            attempted.append(True)
            root.mkdir()
            if entry == "file":
                (root / target).write_bytes(b"must be preserved")
            elif entry == "child":
                (root / target).mkdir()
        return original(name, dir_fd=dir_fd)

    monkeypatch.setattr(cleanup.os, "unlink" if entry == "file" else "rmdir",
                        replace_before_removal)
    monkeypatch.setattr(cleanup.os, "supports_dir_fd",
                        cleanup.os.supports_dir_fd | {replace_before_removal})
    cleanup.remove_owned_stage(root, expected)
    assert attempted and root.is_dir()
    if entry == "file":
        assert (root / target).read_bytes() == b"must be preserved"
    elif entry == "child":
        assert (root / target).is_dir()
    else:
        assert list(root.iterdir()) == []
    assert not list(tmp_path.glob(".fleet-stage-cleanup-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX atomic capture contract")
def test_replacement_at_quarantine_capture_is_retained_with_explicit_error(tmp_path, monkeypatch):
    root = tmp_path / "owned-root"
    root.mkdir()
    (root / "owned-file").write_bytes(b"owned")
    expected = staging._directory(root)
    moved = tmp_path / "moved-original"
    original = cleanup.os.rename

    def replace_before_capture(source, target, *, src_dir_fd, dst_dir_fd):
        original(root, moved)
        root.mkdir()
        (root / "foreign-file").write_bytes(b"must be preserved")
        return original(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(cleanup.os, "rename", replace_before_capture)
    monkeypatch.setattr(cleanup.os, "supports_dir_fd",
                        cleanup.os.supports_dir_fd | {replace_before_capture})
    with pytest.raises(PlanError, match="ownership is unconfirmed; cleanup quarantine retained:"):
        cleanup.remove_owned_stage(root, expected)
    quarantines = list(tmp_path.glob(".fleet-stage-cleanup-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "owned-root" / "foreign-file").read_bytes() == b"must be preserved"
    assert (moved / "owned-file").read_bytes() == b"owned"


@pytest.mark.skipif(os.name == "nt", reason="POSIX quarantine linked-entry refusal")
def test_quarantine_refuses_linked_entries_and_preserves_external_target(tmp_path):
    root = tmp_path / "owned-root"
    root.mkdir()
    foreign = tmp_path / "foreign-target"
    foreign.write_bytes(b"must be preserved")
    (root / "linked-file").symlink_to(foreign)
    with pytest.raises(PlanError, match="linked or reparse.*cleanup quarantine retained:"):
        cleanup.remove_owned_stage(root, staging._directory(root))
    quarantines = list(tmp_path.glob(".fleet-stage-cleanup-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "owned-root" / "linked-file").is_symlink()
    assert foreign.read_bytes() == b"must be preserved"


def test_initial_signature_refusal_creates_no_stage(tmp_path, primitive):
    fixture = artifact_fixture(tmp_path)
    fixture["proof"]["payload"]["sequence"] = 11
    parent = tmp_path / "private-stage"
    parent.mkdir()
    context, _ = stage(fixture, parent)
    with pytest.raises(PlanError, match="signature verification failed"):
        with context:
            pytest.fail("unauthenticated input must not yield")
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("name", [
    "CON.whl", "nul.zip", "COM1.whl", "LPT0.tar.gz", "artifact.",
    "space artifact.whl", ".hidden", "a" * 201, "colon:artifact.whl",
])
def test_nonportable_artifact_filename_refuses(name):
    with pytest.raises(PlanError, match="portable ordinary filename"):
        staging._filename(name)


@pytest.mark.parametrize("name", ["client-1.0-py3-none-any.whl", "client.tar.gz", "COMPLETE.zip"])
def test_portable_artifact_filename_is_preserved(name):
    assert staging._filename(name) == name


def test_linked_staging_parent_refuses_without_modifying_target(tmp_path, primitive):
    fixture = artifact_fixture(tmp_path)
    actual = tmp_path / "actual-stage"
    actual.mkdir()
    sentinel = actual / "user-config"
    sentinel.write_bytes(b"retained")
    linked = tmp_path / "linked-stage"
    if os.name == "nt":
        made = run([os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(linked), str(actual)])
        assert made.returncode == 0, made.stderr
    else:
        linked.symlink_to(actual, target_is_directory=True)
    try:
        context, _ = stage(fixture, linked)
        with pytest.raises(PlanError, match="linked or non-directory"):
            with context:
                pytest.fail("linked staging parent must not yield")
        assert list(actual.iterdir()) == [sentinel]
        assert sentinel.read_bytes() == b"retained"
    finally:
        if os.name == "nt":
            os.rmdir(linked)
        else:
            linked.unlink()


def test_missing_packaged_resource_is_not_replaced_by_repository_helper(tmp_path, monkeypatch):
    fixture = artifact_fixture(tmp_path)
    parent = tmp_path / "private-stage"
    parent.mkdir()
    monkeypatch.setattr(staging, "__file__", str(tmp_path / "without-resource" / "artifact_staging.py"))
    context, _ = stage(fixture, parent)
    with pytest.raises(PlanError, match="could not be read"):
        with context:
            pytest.fail("source-only delivery must not stage an installer resource")
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("operation", ["read", "copy"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, RuntimeError])
def test_descriptor_is_closed_when_stream_construction_is_interrupted(
    tmp_path, monkeypatch, operation, interruption,
):
    source = tmp_path / "source.artifact"
    data = b"bounded source bytes"
    source.write_bytes(data)
    original = os.fdopen
    descriptors = []

    def interrupted(descriptor, *args, **kwargs):
        if kwargs.get("closefd") is False:
            descriptors.append(descriptor)
            raise interruption("controlled stream-construction interruption")
        return original(descriptor, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", interrupted)
    with pytest.raises(interruption):
        if operation == "read":
            staging._bytes(source, 1024)
        else:
            staging._copy(source, tmp_path / "destination.artifact",
                          hashlib.sha256(data).hexdigest())
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert source.read_bytes() == data
    assert not (tmp_path / "destination.artifact").exists()


def test_actual_installed_authenticated_artifact_staging_has_no_execution_or_network(
    tmp_path, request,
):
    artifact, _ = request.getfixturevalue("shared_wheel")
    uv = shutil.which("uv")
    assert uv is not None
    target = tmp_path / "installed-sdk"
    installed = run([uv, "pip", "install", "--python", sys.executable,
                     "--target", str(target), "--no-deps",
                     "--no-index", "--offline", str(artifact)])
    assert installed.returncode == 0, installed.stderr
    fixture = artifact_fixture(tmp_path)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(fixture), encoding="utf-8")
    parent = tmp_path / "private-stage"
    parent.mkdir()
    config = parent / "user-config.json"
    config.write_bytes(b"preserved configuration")
    import cryptography

    crypto_site = Path(cryptography.__file__).resolve().parent.parent
    code = r"""
import json, pathlib, socket, sys
target, inputs, parent, crypto_site = sys.argv[1:]
sys.path[:0] = [target, crypto_site]
import fleet_manager, cryptography
assert pathlib.Path(fleet_manager.__file__).absolute().parent == pathlib.Path(target)/'fleet_manager'
assert cryptography.__version__.split('.')[0] == '46'
def no_network(*args, **kwargs):
    raise AssertionError('Python network entry point used')
socket.socket = socket.create_connection = socket.getaddrinfo = no_network
from fleet_manager.artifact_staging import stage_authenticated_artifacts
from fleet_manager.planning import canonical
value = json.loads(pathlib.Path(inputs).read_text(encoding='utf-8'))
with stage_authenticated_artifacts(
    canonical(value['manifest']), value['proof'], value['policy'], value['sources'],
    authority='test-authority', installation='test-installation', role='client',
    staging_parent=pathlib.Path(parent), clock=lambda:150,
) as stage:
    result = stage.reverify(value['policy'], clock=lambda:151)
    assert result['status'] == 'retained-byte-stage'
    assert result['authenticity_verified'] and result['artifact_integrity_verified']
    assert result['retention_held'] and result['mutations_performed']
    assert not result['activation_authorized'] and not result['live_readiness_verified']
    assert not (stage._root/'current-version').exists()
    assert not (stage._slot/'.install-complete.json').exists()
    for name in stage.artifacts:
        assert stage.read_artifact(name, value['policy'], clock=lambda:151) == (
            pathlib.Path(value['sources']['components'][name]).read_bytes()
        )
    slot = stage._slot
assert not slot.exists()
assert list(pathlib.Path(parent).iterdir()) == [pathlib.Path(parent)/'user-config.json']
print(json.dumps(result))
"""
    checked = run([sys.executable, "-I", "-S", "-B", "-c", code,
                   str(target), str(inputs), str(parent), str(crypto_site)])
    assert checked.returncode == 0, checked.stderr
    result = json.loads(checked.stdout)
    assert result["sequence"] == 10
    assert result["verified_at"] == 151
    assert config.read_bytes() == b"preserved configuration"
