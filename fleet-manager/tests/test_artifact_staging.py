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

import pytest

import fleet_manager.artifact_staging as staging
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
        for name, path in frozen.artifacts.items():
            assert path.read_bytes() == Path(fixture["sources"]["components"][name]).read_bytes()
            assert path.name == Path(fixture["sources"]["components"][name]).name
        assert not primitive.current_version(frozen._root)
        assert not primitive.read_marker(frozen._root, frozen.slot.name)
        assert not primitive.slot_python(frozen._root, frozen.slot.name)
        assert primitive.gc(frozen._root) == []
        assert frozen.slot.is_dir()
        with pytest.raises(TypeError):
            frozen.artifacts["unowned"] = parent / "unowned"
    assert not frozen.slot.exists()
    assert list(parent.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"retained custom config\n"
    assert fixture == inputs
    with pytest.raises(PlanError, match="retention has ended"):
        frozen.reverify(trusted, clock=lambda: 152)


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
        assert next(iter(frozen.artifacts.values())).read_bytes() != original.read_bytes()


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
            next(iter(frozen.artifacts.values())).write_bytes(b"tampered")
        elif change == "manifest":
            (frozen.slot / "release.json").write_bytes(b"{}")
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
    installed = run([uv, "pip", "install", "--target", str(target), "--no-deps",
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
    assert not (stage.slot/'.install-complete.json').exists()
    slot = stage.slot
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
