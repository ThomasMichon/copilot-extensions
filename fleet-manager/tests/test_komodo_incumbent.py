"""Original-volume observations and no-apply controls, not live adoption proof."""

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import asyncio
import hashlib
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_incumbent import (
    KomodoConfigUsername, KomodoIncumbentMaterialization, mount_evidence,
)
from fleet_manager.komodo_native import KomodoNative, KomodoTooling, KomodoImage
from fleet_manager.komodo_evidence import native_fingerprint
from fleet_manager.komodo_health import identity_digest
from fleet_manager.komodo_identity import effective_global_key
from fleet_manager.komodo_network import network_fingerprint, same_network_observation, verify_network
from fleet_manager.komodo_owner import KomodoOwner
from fleet_manager.komodo_repair import KomodoRepairOwner
from fleet_manager.komodo_repair_selection import KomodoRepairSelection
from fleet_manager.komodo_profile import DATA_NAMES, SECRET_NAMES, KomodoMaterialization, render_profile
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.node_journal import NodeJournal
from fleet_manager.planning import PlanError, canonical, decode, digest
from fleet_manager.private_state import create_private_file, private_directory
from fleet_manager.runtime_admission import OperationGrant
from komodo_public_fixtures import PUBLIC_DER, PUBLIC_PEM
from test_komodo_declaration import declaration, intent
from test_runtime_admission import Harness, actor, policy
from test_runtime_identity import keys as keys


@pytest.fixture
def incumbent(tmp_path, monkeypatch):
    paths = {}
    for name in ("database", "keys"):
        path = tmp_path / name
        path.mkdir(mode=0o700)
        paths[name] = path
    files = {}
    for name in ("admin_username", "admin_password", "core_config", "periphery_config"):
        path = tmp_path / name
        descriptor = create_private_file(path)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((name + "-original-fixture-material").encode())
        files[name] = path
    for name in ("core", "periphery"):
        path = paths["keys"] / (name + ".key")
        descriptor = create_private_file(path)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((name + "-original-private-key-fixture").encode())
        files[name + "_key"] = path
    volume = {
        "Name": "original-database", "Driver": "local", "Scope": "local",
        "Mountpoint": str(paths["database"]), "CreatedAt": "2026-01-01T00:00:00Z",
        "Options": None, "Labels": {"original-owner": "example"}, "Status": {},
    }
    mounts = {
        "core": [
            {"Type": "bind", "Source": str(paths["keys"]), "Destination": "/config/keys", "RW": True},
            {"Type": "bind", "Source": str(files["core_config"]),
             "Destination": "/config/config.toml", "RW": False},
        ],
        "periphery": [
            {"Type": "bind", "Source": str(paths["keys"]), "Destination": "/config/keys", "RW": True},
            {"Type": "bind", "Source": str(files["periphery_config"]),
             "Destination": "/config/config.toml", "RW": False},
        ],
        "mongo": [
            {"Type": "volume", "Name": volume["Name"], "Driver": "local",
             "Source": volume["Mountpoint"], "Destination": "/data/db", "RW": True},
        ],
    }
    keys_volume = {**volume, "Name": "original-keys", "Mountpoint": str(paths["keys"])}
    for name in ("core", "periphery"):
        mounts[name][0].update(Type="volume", Name=keys_volume["Name"], Driver="local")
    volumes = [volume, keys_volume]
    raw = canonical({
        "schema": "fleet-manager.komodo-incumbent-material", "version": 1,
        "mounts": mounts, "volumes": volumes,
    })
    docker_socket = tmp_path / "docker-socket"
    if sys.platform == "linux":
        from test_komodo_native import _private_docker_socket

        socket_context = _private_docker_socket()
        docker_socket = socket_context.__enter__()
    else:
        docker_socket.touch()
        monkeypatch.setattr("fleet_manager.komodo_incumbent.stat.S_ISSOCK", lambda mode: True)
        is_file = Path.is_file
        monkeypatch.setattr(Path, "is_file", lambda path: False if path == docker_socket else is_file(path))
        socket_context = None
    try:
        material = KomodoIncumbentMaterialization(
            docker_socket, paths["keys"], files["admin_username"], files["admin_password"],
            (files["core_config"], files["periphery_config"], files["core_key"], files["periphery_key"]), raw,
        )
        yield SimpleNamespace(material=material, mounts=mounts, volumes=volumes, files=files)
    finally:
        if socket_context is not None:
            socket_context.__exit__(None, None, None)


def test_incumbent_observation_retains_originals_without_synthesizing_fresh_credentials(incumbent):
    material = incumbent.material
    before = {path: (path.read_bytes(), path.stat().st_ino) for path in incumbent.files.values()}
    profile = decode(material.profile())
    assert "services" not in profile
    assert set(material.credential_bytes()) == {"admin_username", "admin_password"}
    for value in material.credential_bytes().values():
        assert value not in material.profile()
    assert material.volumes() == incumbent.volumes
    assert before == {path: (path.read_bytes(), path.stat().st_ino) for path in incumbent.files.values()}
    selected = declaration()
    selected.update(profile="preserve-incumbent")
    selected["images"]["mongo"]["version"] = "7.0.45"
    with pytest.raises(PlanError, match="cannot render"):
        render_profile(KomodoDeclaration.from_dict(selected), material, owner="a" * 64)


def _socket_mount(material, source):
    value = decode(material.encoded)
    value["mounts"]["periphery"].append({
        "Type": "bind", "Source": source, "Destination": "/var/run/docker.sock",
        "RW": True, "Propagation": "rprivate",
    })
    return canonical(value)


def test_explicit_socket_source_preserves_native_bytes_and_binds_physical_identity(incumbent):
    material = incumbent.material
    source = str(material.docker_socket)
    encoded = _socket_mount(material, source)
    selected = replace(material, encoded=encoded, docker_socket_source=source)
    assert selected.encoded == encoded
    assert selected.evidence()["docker_socket"] == {
        "source": source, "path": source,
        "device": material.docker_socket.stat().st_dev,
        "inode": material.docker_socket.stat().st_ino,
    }
    mounts = decode(encoded)["mounts"]["periphery"]
    selected.check_mounts("periphery", mounts)
    changed = deepcopy(mounts)
    changed[-1]["Propagation"] = "rshared"
    with pytest.raises(PlanError, match="mounts changed"):
        selected.check_mounts("periphery", changed)


@pytest.mark.parametrize("change", ["missing", "different", "volume", "core", "duplicate", "unsafe"])
def test_explicit_socket_source_refuses_unmatched_or_ambiguous_native_mounts(incumbent, change):
    material = incumbent.material
    source = str(material.docker_socket)
    value = decode(_socket_mount(material, source))
    mount = value["mounts"]["periphery"][-1]
    if change == "missing":
        value["mounts"]["periphery"].pop()
    elif change == "different":
        mount["Source"] = str(incumbent.files["core_config"])
    elif change == "volume":
        mount["Type"] = "volume"
    elif change == "core":
        value["mounts"]["core"].append(value["mounts"]["periphery"].pop())
    elif change == "duplicate":
        value["mounts"]["periphery"].append({**mount, "Destination": "/other-socket"})
    else:
        source = str(material.docker_socket.parent / ".." / material.docker_socket.name)
        mount["Source"] = source
    with pytest.raises(PlanError, match="socket"):
        replace(material, encoded=canonical(value), docker_socket_source=source)


@pytest.mark.skipif(sys.platform != "linux", reason="real native socket and symlink semantics require Linux")
def test_native_socket_directory_alias_is_opt_in_and_rechecked_without_rewriting(incumbent, tmp_path):
    from test_komodo_native import _private_docker_socket

    from fleet_manager.komodo_profile import ordinary_path

    material = incumbent.material
    alias = tmp_path / "native-run"
    alias.symlink_to(material.docker_socket.parent, target_is_directory=True)
    source = str(alias / material.docker_socket.name)
    encoded = _socket_mount(material, source)
    with pytest.raises(PlanError, match="indirection"):
        replace(material, encoded=encoded)
    with pytest.raises(PlanError, match="indirection"):
        ordinary_path(Path(source))
    selected = replace(material, encoded=encoded, docker_socket_source=source)
    before = selected.evidence()
    assert before["docker_socket"]["path"] == str(material.docker_socket)
    assert source in before["paths"]
    assert selected.encoded == encoded
    changed = decode(encoded)["mounts"]["periphery"]
    changed[-1]["Source"] = str(material.docker_socket)
    with pytest.raises(PlanError, match="mounts changed"):
        selected.check_mounts("periphery", changed)
    with _private_docker_socket() as another:
        alias.unlink()
        alias.symlink_to(another.parent, target_is_directory=True)
        with pytest.raises(PlanError, match="socket"):
            selected.evidence()
    alias.unlink()
    with pytest.raises(PlanError, match="socket"):
        selected.evidence()
    alias.symlink_to(alias, target_is_directory=True)
    with pytest.raises(PlanError, match="socket"):
        selected.evidence()


def test_explicit_socket_source_refuses_python310_to312_resolver_loop_error(incumbent, monkeypatch):
    material = incumbent.material
    source = str(material.docker_socket)
    selected = replace(material, encoded=_socket_mount(material, source), docker_socket_source=source)
    resolve = Path.resolve

    def cyclic(path, *args, **options):
        if path == Path(source):
            raise RuntimeError("Symlink loop on Python 3.10-3.12")
        return resolve(path, *args, **options)

    monkeypatch.setattr(Path, "resolve", cyclic)
    with pytest.raises(PlanError, match="socket source is unavailable"):
        selected.evidence()


@pytest.mark.skipif(sys.platform != "linux", reason="real native socket and symlink semantics require Linux")
def test_native_socket_retarget_during_retained_file_reads_refuses(incumbent, tmp_path, monkeypatch):
    from test_komodo_native import _private_docker_socket

    material = incumbent.material
    alias = tmp_path / "native-run"
    alias.symlink_to(material.docker_socket.parent, target_is_directory=True)
    source = str(alias / material.docker_socket.name)
    selected = replace(material, encoded=_socket_mount(material, source), docker_socket_source=source)
    reader = KomodoIncumbentMaterialization.credential_bytes
    with _private_docker_socket() as another:
        def retargeting(self):
            values = reader(self)
            alias.unlink()
            alias.symlink_to(another.parent, target_is_directory=True)
            return values

        monkeypatch.setattr(KomodoIncumbentMaterialization, "credential_bytes", retargeting)
        with pytest.raises(PlanError, match="socket"):
            selected.evidence()


def test_explicit_socket_binding_does_not_relax_other_bind_sources(incumbent):
    material = incumbent.material
    source = str(material.docker_socket)
    value = decode(_socket_mount(material, source))
    value["mounts"]["core"][1]["Source"] = str(material.keys / ".." / "core_config")
    with pytest.raises(PlanError, match="ordinary"):
        replace(material, encoded=canonical(value), docker_socket_source=source)


def test_incumbent_reads_username_from_retained_core_config_without_a_username_file(incumbent):
    path = incumbent.files["core_config"]
    path.write_bytes(b'init_admin_username = "original-admin"\nlocal_auth = true\n')
    incumbent.files["admin_username"].unlink()
    material = replace(incumbent.material, admin_username=KomodoConfigUsername(path))
    assert material.credential_bytes()["admin_username"] == b"original-admin"
    before = material.evidence()
    assert before["username_source"] == {"config": str(path), "field": "init_admin_username"}
    assert b"original-admin" not in material.profile()
    path.write_bytes(b'init_admin_username = "another-admin"\nlocal_auth = true\n')
    assert material.credential_bytes()["admin_username"] == b"another-admin"
    assert material.evidence() != before
    assert not incumbent.files["admin_username"].exists()


def test_python310_import_and_configured_username_use_declared_toml_fallback(incumbent):
    from agent_procutil.owned_process import run_caller_owned

    source = Path(__file__).resolve().parents[1] / "src"
    code = """
import builtins, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def without_stdlib_toml(name, *args, **options):
    if name == "tomllib":
        raise ModuleNotFoundError("Python 3.10 has no tomllib")
    return original(name, *args, **options)
builtins.__import__ = without_stdlib_toml
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_native import KomodoNative
from fleet_manager.komodo_incumbent import KomodoConfigUsername
from fleet_manager.planning import PlanError
assert KomodoConfigUsername.value(b'init_admin_username = "original-admin"') == b"original-admin"
try:
    KomodoConfigUsername.value(b'init_admin_username = "a"\\ninit_admin_username = "b"')
except PlanError:
    pass
else:
    raise AssertionError("duplicate TOML must refuse")
"""
    result = run_caller_owned(
        [sys.executable, "-I", "-c", code, str(source)],
        cwd=str(incumbent.material.keys.parent), timeout=10,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("content", [
    b"", b"invalid toml", b'init_admin_username = ""', b"init_admin_username = 123",
    b'init_admin_username = "a"\ninit_admin_username = "b"',
    b'init_admin_username = "a\\nb"', b'init_admin_username = "\\u0000"',
])
def test_config_username_refuses_missing_ambiguous_or_unsupported_values(incumbent, content):
    path = incumbent.files["core_config"]
    path.write_bytes(content)
    with pytest.raises(PlanError, match="username|login"):
        replace(incumbent.material, admin_username=KomodoConfigUsername(path))


def test_config_username_cannot_select_an_unmounted_configuration(incumbent):
    path = incumbent.files["periphery_config"]
    path.write_bytes(b'init_admin_username = "another-admin"')
    with pytest.raises(PlanError, match="retained Core"):
        replace(incumbent.material, admin_username=KomodoConfigUsername(path))


def test_config_username_change_between_login_read_and_evidence_read_refuses(incumbent, monkeypatch):
    import fleet_manager.komodo_incumbent as inputs

    path = incumbent.files["core_config"]
    path.write_bytes(b'init_admin_username = "original-admin"\n')
    material = replace(incumbent.material, admin_username=KomodoConfigUsername(path))
    reader = inputs.read_private_file
    reads = []

    def changing(source, *args, **options):
        if source == path:
            reads.append(source)
            if len(reads) == 2:
                path.write_bytes(b'init_admin_username = "changed-admin"\n')
        return reader(source, *args, **options)

    monkeypatch.setattr(inputs, "read_private_file", changing)
    with pytest.raises(PlanError, match="login changed during observation"):
        material.evidence()


def _two_networks():
    networks, containers = {}, {}
    for index, name in enumerate(("original-management", "original-egress"), 1):
        networks[name] = {
            "Name": name, "Id": str(index) * 64, "Driver": "bridge", "Scope": "local",
            "Internal": index == 1, "Attachable": False, "Ingress": False, "ConfigOnly": False,
            "ConfigFrom": {"Network": ""}, "Options": {}, "Labels": {"original-owner": "example"},
            "IPAM": {"Driver": "default"}, "Containers": {},
        }
    attachments = {}
    for index, service in enumerate(("core", "mongo", "periphery"), 3):
        names = ["original-management", "original-egress"] if service == "core" else ["original-management"]
        attachments[service] = names
        node = str(index) * 64
        connections = {}
        for name in names:
            connections[name] = {"NetworkID": networks[name]["Id"], "EndpointID": service + "-" + name}
            networks[name]["Containers"][node] = {"EndpointID": service + "-" + name}
        containers[service] = {
            "Id": node, "State": {"Running": True, "Status": "running"},
            "HostConfig": {"NetworkMode": "original-management"},
            "NetworkSettings": {"Networks": connections},
        }
    return {
        "networks": networks, "attachments": attachments,
        "modes": dict.fromkeys(containers, "original-management"),
    }, containers


@pytest.mark.parametrize("change", ["alias", "host-mode", "duplicate", "missing", "redirect", "unused"])
def test_incumbent_topology_refuses_ambiguous_or_unsupported_owner_observations(incumbent, change):
    topology, _ = _two_networks()
    if change == "alias":
        topology["networks"]["original-egress"]["Id"] = topology["networks"]["original-management"]["Id"]
    elif change == "host-mode":
        topology["modes"]["core"] = "host"
    elif change == "duplicate":
        topology["attachments"]["core"].append("original-egress")
    elif change == "missing":
        topology["attachments"].pop("mongo")
    elif change == "redirect":
        topology["networks"]["original-egress"]["ConfigFrom"] = {"Network": "foreign"}
    else:
        topology["attachments"]["core"] = ["original-management"]
    with pytest.raises(PlanError, match="topology"):
        replace(incumbent.material, topology=canonical(topology))


@pytest.mark.parametrize("change", [
    None, "reverse", "missing-network", "replacement", "policy", "foreign-endpoint",
    "extra-service", "missing-membership", "endpoint", "mode", "attachment",
])
def test_incumbent_topology_retains_internal_bridge_and_core_only_egress(incumbent, change):
    topology, containers = _two_networks()
    material = replace(incumbent.material, topology=canonical(topology))
    selected = SimpleNamespace(declaration=SimpleNamespace(preserving=True), material=material)
    observed = {"networks": deepcopy(topology["networks"])}
    expected = network_fingerprint(observed)
    original = deepcopy(observed)
    if change == "reverse":
        topology["attachments"]["core"].reverse()
        assert replace(material, topology=canonical(topology)).evidence() == material.evidence()
    elif change == "missing-network":
        del observed["networks"]["original-egress"]
    elif change == "replacement":
        observed["networks"]["original-egress"]["Id"] = "9" * 64
    elif change == "policy":
        observed["networks"]["original-management"]["Internal"] = False
    elif change == "foreign-endpoint":
        observed["networks"]["original-egress"]["Containers"]["9" * 64] = {"EndpointID": "foreign"}
    elif change == "extra-service":
        observed["networks"]["original-egress"]["Containers"][containers["mongo"]["Id"]] = {
            "EndpointID": "unselected-mongo"}
    elif change == "missing-membership":
        observed["networks"]["original-egress"]["Containers"].clear()
    elif change == "endpoint":
        containers["core"]["NetworkSettings"]["Networks"]["original-egress"]["EndpointID"] = "changed"
    elif change == "mode":
        containers["core"]["HostConfig"]["NetworkMode"] = "original-egress"
    elif change == "attachment":
        del containers["core"]["NetworkSettings"]["Networks"]["original-egress"]
    if change in (None, "reverse"):
        assert verify_network(observed, selected, containers, expected) == expected
        assert same_network_observation(original, observed)
        assert material.network_mode("core", "unused-project") == "original-management"
        with pytest.raises(PlanError):
            verify_network(observed, selected, containers, expected, created=True)
    else:
        with pytest.raises(PlanError, match="topology|attachment|membership|endpoints"):
            verify_network(observed, selected, containers, expected)
    observed["networks"]["original-management"]["Containers"].clear()
    assert not same_network_observation(original, observed)


@pytest.mark.parametrize("change", ["driver", "source", "unrelated", "config", "duplicate"])
def test_incumbent_material_refuses_unproved_or_ambiguous_storage(incumbent, change):
    value = decode(incumbent.material.encoded)
    if change == "driver":
        value["volumes"][0]["Driver"] = "unselected-driver"
    elif change == "source":
        value["mounts"]["mongo"][0]["Source"] = str(incumbent.files["core_config"].parent)
    elif change == "unrelated":
        value["volumes"].append({**value["volumes"][0], "Name": "unrelated"})
    elif change == "config":
        with pytest.raises(PlanError, match="mounted configuration"):
            replace(incumbent.material, retained_files=(incumbent.files["core_config"],))
        return
    else:
        value["mounts"]["core"].append(deepcopy(value["mounts"]["core"][0]))
    with pytest.raises(PlanError):
        replace(incumbent.material, encoded=canonical(value))


def test_mount_comparison_retains_all_fields_and_ignores_only_order(incumbent):
    original = incumbent.mounts["core"]
    assert mount_evidence(original) == mount_evidence(list(reversed(original)))
    changed = deepcopy(original)
    changed[0]["Propagation"] = "unselected-propagation"
    with pytest.raises(PlanError, match="mounts changed"):
        incumbent.material.check_mounts("core", changed)


def test_refreshed_materialization_preserves_selection_evidence_across_observation_order(incumbent):
    material = incumbent.material
    observed = decode(material.encoded)
    for mounts in observed["mounts"].values():
        mounts.reverse()
    observed["volumes"].reverse()
    refreshed = replace(material, encoded=canonical(observed))
    assert refreshed.encoded != material.encoded
    assert refreshed.evidence() == material.evidence()
    assert refreshed.profile() == material.profile()


@pytest.mark.parametrize("changed", [False, True])
def test_global_key_reference_requires_exact_original_named_volume_mount(incumbent, changed):
    mounts = deepcopy(incumbent.mounts["core"])
    if changed:
        mounts[0]["Name"] = "replacement-keys"
    core = {"Config": {"Env": ["KOMODO_PERIPHERY_PUBLIC_KEY=file:/config/keys/periphery.pub"]},
            "Mounts": mounts}
    if changed:
        with pytest.raises(PlanError, match="mounts changed"):
            effective_global_key(core, incumbent.material, PUBLIC_PEM["periphery"])
    else:
        assert effective_global_key(core, incumbent.material, PUBLIC_PEM["periphery"]) == PUBLIC_PEM["periphery"]


def test_incumbent_file_content_and_physical_replacement_change_selection_evidence(incumbent):
    original = incumbent.material.evidence()
    path = incumbent.files["core_config"]
    path.write_bytes(b"changed-native-configuration")
    assert incumbent.material.evidence() != original
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    before = incumbent.material.evidence()
    replacement.replace(path)
    assert incumbent.material.evidence() != before


@pytest.mark.parametrize("name", ["core_key", "periphery_key"])
def test_mandatory_private_key_permission_drift_refuses_unchanged_material(incumbent, name):
    path = incumbent.files[name]
    original = path.read_bytes(), path.stat().st_ino
    incumbent.material.evidence()
    if os.name == "nt":
        from agent_procutil.owned_process import run_caller_owned

        executable = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
        result = run_caller_owned(
            [str(executable), str(path), "/grant", "*S-1-1-0:(R)"],
            cwd=str(path.parent), timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        assert result.returncode == 0
    else:
        path.chmod(0o644)
    assert (path.read_bytes(), path.stat().st_ino) == original
    with pytest.raises(PlanError):
        incumbent.material.evidence()


def test_empty_original_configuration_is_retained_but_empty_private_keys_refuse(incumbent):
    material = incumbent.material
    configuration = incumbent.files["core_config"]
    configuration.write_bytes(b"")
    refreshed = replace(material)
    evidence = refreshed.evidence()
    assert evidence == refreshed.evidence()
    assert evidence["files"][str(configuration)]["sha256"] == hashlib.sha256(b"").hexdigest()
    incumbent.files["core_key"].write_bytes(b"")
    with pytest.raises(PlanError):
        refreshed.evidence()


@pytest.mark.parametrize("name", ["core_config", "core_key"])
def test_same_content_replacement_between_recorded_stat_and_read_refuses(incumbent, monkeypatch, name):
    import fleet_manager.komodo_incumbent as inputs

    path = incumbent.files[name]
    original = path.read_bytes(), path.stat().st_ino
    replacement = path.with_suffix(".replacement")
    descriptor = create_private_file(replacement)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(original[0])
    attribute = "read_private_file" if name == "core_key" else "_bytes"
    reader = getattr(inputs, attribute)
    changed = []

    def swapping(source, *arguments, **options):
        if source == path and not changed:
            replacement.replace(path)
            changed.append(True)
        return reader(source, *arguments, **options)

    monkeypatch.setattr(inputs, attribute, swapping)
    with pytest.raises(PlanError, match="changed during observation"):
        incumbent.material.evidence()
    assert changed and path.read_bytes() == original[0] and path.stat().st_ino != original[1]


@pytest.mark.parametrize(("service", "name"), [
    ("core", "core.key"), ("periphery", "periphery.key"),
    ("periphery", "core.pub"), ("core", "periphery.pub"),
])
def test_required_key_file_overlays_refuse_even_when_the_overlay_source_is_retained(
    incumbent, service, name,
):
    overlay = incumbent.material.keys.parent / "overlay"
    descriptor = create_private_file(overlay)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"retained-overlay-fixture-material")
    observed = decode(incumbent.material.encoded)
    observed["mounts"][service].append({
        "Type": "bind", "Source": str(overlay),
        "Destination": "/config/keys/" + name, "RW": False,
    })
    with pytest.raises(PlanError, match="key-file overlays"):
        replace(incumbent.material, encoded=canonical(observed),
                retained_files=(*incumbent.material.retained_files, overlay))


@pytest.mark.parametrize("change", [
    None, "time", "labels", "mountpoint", "options", "missing", "reverse", "duplicate",
])
def test_native_volume_readback_is_exact_and_never_reinterprets_mountpoints(incumbent, change):
    from agent_procutil.owned_process import run_caller_owned

    actual = deepcopy(incumbent.volumes)
    if change == "time":
        actual[0]["CreatedAt"] = "recreated-volume"
    elif change == "labels":
        actual[0]["Labels"] = {}
    elif change == "mountpoint":
        actual[0]["Mountpoint"] = str(incumbent.files["core_config"].parent)
    elif change == "options":
        actual[0]["Options"] = {"device": "unselected"}
    elif change == "reverse":
        actual.reverse()
    elif change == "duplicate":
        actual[1] = deepcopy(actual[0])
    calls = []

    def invoke(argv, **options):
        calls.append(argv)
        child = (
            "import json, sys; assert sys.argv[1:] == " + repr(argv[1:])
            + "; json.dump(" + repr([] if change == "missing" else actual) + ", sys.stdout)"
        )
        return run_caller_owned([sys.executable, "-I", "-c", child, *argv[1:]], **options)

    native = object.__new__(KomodoNative)
    native.native_failure = None
    native.tooling = SimpleNamespace(
        docker=Path(sys.executable), client_config=incumbent.files["core_config"].parent,
        run=invoke, check_client=lambda: None,
    )
    if change in (None, "reverse"):
        native.verify_storage(incumbent.material, deadline=time.monotonic() + 10)
    else:
        with pytest.raises(PlanError, match="volume changed"):
            native.verify_storage(incumbent.material, deadline=time.monotonic() + 10)
    assert len(calls) == 1
    assert calls[0][-4:] == ["volume", "inspect", "original-database", "original-keys"]


def test_native_apply_refuses_incumbent_even_with_a_fixed_declaration(incumbent):
    native = object.__new__(KomodoNative)
    for value in (declaration(), {**declaration(), "profile": "preserve-incumbent"}):
        with pytest.raises(PlanError, match="cannot apply"):
            native._apply(KomodoDeclaration.from_dict(value), incumbent.material,
                          profile=Path("never-opened"), expected=b"", deadline=time.monotonic() + 10)
    with pytest.raises(PlanError, match="cannot withdraw"):
        native._retire(incumbent.material, ["a" * 64], deadline=time.monotonic() + 10,
                       checkpoint=lambda: pytest.fail("no incumbent native effect is admitted"))


@pytest.mark.parametrize("change", [None, "missing", "duplicate", "foreign"])
def test_native_incumbent_network_inspection_uses_exact_selected_names(incumbent, change):
    from agent_procutil.owned_process import run_caller_owned

    topology, _ = _two_networks()
    material = replace(incumbent.material, topology=canonical(topology))
    actual = list(deepcopy(topology["networks"]).values())
    if change == "missing":
        actual.pop()
    elif change == "duplicate":
        actual[1] = deepcopy(actual[0])
    elif change == "foreign":
        actual[1]["Name"] = "unselected-network"
    calls = []

    def invoke(argv, **options):
        calls.append(argv)
        child = ("import json, sys; assert sys.argv[1:] == " + repr(argv[1:])
                 + "; json.dump(" + repr(actual) + ", sys.stdout)")
        return run_caller_owned([sys.executable, "-I", "-c", child, *argv[1:]], **options)

    native = object.__new__(KomodoNative)
    native.native_failure = None
    native.tooling = SimpleNamespace(
        docker=Path(sys.executable), client_config=incumbent.files["core_config"].parent,
        run=invoke, check_client=lambda: None,
    )
    if change is None:
        assert native.network(KomodoDeclaration.from_dict({**declaration(), "profile": "preserve-incumbent"}),
                              material, deadline=time.monotonic() + 10) == {"networks": topology["networks"]}
    else:
        with pytest.raises(PlanError, match="network evidence"):
            native.network(KomodoDeclaration.from_dict({**declaration(), "profile": "preserve-incumbent"}),
                           material, deadline=time.monotonic() + 10)
    assert len(calls) == 1
    assert calls[0][-4:] == ["network", "inspect", "original-egress", "original-management"]


@pytest.mark.parametrize("scenario", [
    "adopt-repeat-status", "update", "stopped", "mount-drift", "credential-drift",
    "adopt-two-networks", "lost-terminal-two-networks",
    "native-drift", "missing", "missing-adoption", "private-key-drift", "lost-terminal", "global-trust",
    "expired-during-selection",
    "refreshed-order", "failed-repeat-running", "failed-repeat-stopped",
    "failed-lost-terminal", "failed-pending-terminal", "failed-native-drift",
    "failed-recovered-converge", "failed-recovered-settle",
    "failed-recovered-foreign-request", "failed-recovered-changed-evidence",
    "failed-unstarted-converge", "failed-unstarted-settle",
    "failed-unstarted-foreign-network",
    "failed-unstarted-keys-converge", "failed-unstarted-keys-settle",
    "failed-unstarted-keys-native-to-repair", "failed-unstarted-keys-repair-to-native",
    "failed-unstarted-keys-missing-outcome", "failed-unstarted-keys-corrupt-outcome",
    "failed-unstarted-keys-missing-terminal-outcome", "failed-unstarted-keys-corrupt-terminal-outcome",
    "lost-terminal-missing-outcome", "lost-terminal-corrupt-outcome",
    "lost-terminal-committed-missing-outcome", "lost-terminal-committed-corrupt-outcome",
    "lost-terminal-fixed-replaced-container",
])
def test_composed_incumbent_owner_keeps_exact_originals_and_never_applies(
    incumbent, keys, monkeypatch, scenario,
):
    """Real admission, SQLite and HTTP; native observations/state lock are fixtures."""
    material = incumbent.material
    if scenario.endswith("two-networks"):
        topology, network_containers = _two_networks()
        incumbent.files["core_config"].write_bytes(b'init_admin_username = "original-admin"\n')
        incumbent.files["admin_username"].unlink()
        material = replace(material, topology=canonical(topology),
                           admin_username=KomodoConfigUsername(incumbent.files["core_config"]))
    runtime = material.keys.parent / "host-runtime"
    client = material.keys.parent / "native-client"
    private_directory(runtime)
    private_directory(client)
    descriptor = create_private_file(client / "config.json")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"{}")
    if sys.platform != "linux":
        original_stat = Path.stat

        def private_stat(path, *arguments, **options):
            info = original_stat(path, *arguments, **options)
            if path in (runtime, client):
                fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                fields["st_mode"] &= ~0o077
                return SimpleNamespace(**fields)
            return info

        monkeypatch.setattr(Path, "stat", private_stat)
        monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
    executable = material.keys.parent / "docker-fixture"
    executable.write_bytes(b"not-executed")
    executable.chmod(0o700)
    tooling = KomodoTooling(executable, client, "daemon-a", lambda *a, **k: None)
    for name in ("core", "periphery"):
        (material.keys / (name + ".pub")).write_text(PUBLIC_PEM[name])
    state_record = [None]

    @contextmanager
    def damaged_outcome(path, *, missing):
        original = path.read_bytes()
        if missing:
            path.unlink()
        else:
            path.write_bytes(b"{}")
        try:
            yield
        finally:
            if not path.exists():
                descriptor = create_private_file(path)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(original)
            else:
                path.write_bytes(original)

    @contextmanager
    def opened(host_runtime, *, daemon, project, existing_only=False):
        root = host_runtime / digest({"daemon": daemon, "project": project})
        private_directory(root)

        def profile(raw):
            if "services" in decode(raw):
                private_directory(root / "deployments")
                slot = root / "deployments" / digest({"profile": raw.decode("utf-8")})
                private_directory(slot)
                path = slot / "compose.json"
            else:
                path = root / "incumbent-observation.json"
            if not path.exists():
                descriptor = create_private_file(path)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
            if path.read_bytes() != raw:
                raise PlanError("Komodo retained immutable profile changed")
            return path

        def retain(value):
            reference = digest(value)
            directory = root / "receipts"
            private_directory(directory)
            path = directory / (reference + ".json")
            raw = canonical(value)
            if not path.exists():
                descriptor = create_private_file(path)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
            if path.read_bytes() != raw:
                raise PlanError("Komodo retained immutable outcome changed")
            return reference

        yield SimpleNamespace(
            root=root, read=lambda: deepcopy(state_record[0]),
            save=lambda value: state_record.__setitem__(0, deepcopy(value)),
            check=lambda: None, profile=profile, retain=retain,
        )

    monkeypatch.setattr(KomodoState, "open", opened)

    async def exercise():
        requests = []
        token = "original-fixture-local-token"
        recovered = False

        async def handler(request):
            requests.append(request.path)
            body = await request.json()
            if request.path == "/auth/login/LoginLocalUser":
                assert body == {name.removeprefix("admin_"): value.decode()
                                for name, value in material.credential_bytes().items()}
                return web.json_response({"data": {"jwt": token}})
            assert request.headers.get("authorization") == token
            return web.json_response({
                "/read/GetVersion": {
                    "version": "9.0.0" if scenario.startswith("failed-") and not recovered else "2.0.0",
                },
                "/read/GetServer": {
                    "id": "original-server", "name": "example-host",
                    "config": {"enabled": True, "address": (
                        "" if scenario == "global-trust" else "http://periphery:8120")},
                    "info": {"public_key": "" if scenario == "global-trust" else PUBLIC_DER["periphery"]},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "docker_connected": True,
                    "public_key": PUBLIC_DER["periphery"],
                },
            }[request.path])

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            desired = declaration()
            desired.update(profile="preserve-incumbent", core_port=server.port)
            desired["images"]["mongo"]["version"] = "7.0.45"
            unstarted = scenario.startswith("failed-unstarted-")
            fixed = unstarted or scenario == "lost-terminal-fixed-replaced-container"
            if fixed:
                desired.pop("profile")
                desired["images"]["mongo"]["version"] = "8.0.0"
                paths = {"keys": material.keys, "admin_username": material.admin_username,
                         "admin_password": material.admin_password}
                for name in DATA_NAMES:
                    if name not in paths:
                        paths[name] = material.keys.parent / name
                        private_directory(paths[name])
                for name in SECRET_NAMES:
                    if name not in paths:
                        paths[name] = material.keys.parent / name
                        descriptor = create_private_file(paths[name])
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write((name + "-fixed-fixture-material").encode())
                fixed_material = KomodoMaterialization(material.docker_socket, **paths)
                if unstarted and "-keys-" not in scenario:
                    for name in ("core", "periphery"):
                        (material.keys / (name + ".pub")).unlink()
            project = desired["project"] + "_default"
            containers = {}
            images = {}
            for number, name in enumerate(("core", "mongo", "periphery"), 1):
                identity = "sha256:" + desired["images"][name]["image"].split("sha256:")[1]
                images[name] = KomodoImage(identity, (), (), (), "")
                containers[name] = {
                    "Id": str(number) * 64, "Image": identity,
                    "Config": {"User": "", "Env": ["ORIGINAL_SETTING=retained"],
                               "Labels": {"original-owner": "example"}},
                    "Mounts": deepcopy(incumbent.mounts[name]),
                    "HostConfig": {
                        "Privileged": False, "CapAdd": [], "PidMode": "", "NetworkMode": project,
                        "Memory": desired["resources"][name]["memory_bytes"], "NanoCpus": 500000000,
                        "PortBindings": {"9120/tcp": [
                            {"HostIp": "127.0.0.1", "HostPort": str(server.port)},
                        ]} if name == "core" else {},
                    },
                    "State": {"Status": "running", "Running": True, "Paused": False,
                              "Restarting": False, "Dead": False, "Pid": number,
                              "StartedAt": "original-start"},
                    "NetworkSettings": {"Networks": {
                        project: {"NetworkID": "d" * 64, "EndpointID": "endpoint-" + name},
                    }},
                }
            if scenario == "global-trust":
                containers["core"]["Config"]["Env"] = [
                    "KOMODO_PERIPHERY_PUBLIC_KEY=file:/config/keys/periphery.pub",
                ]
            network = {
                "Id": "d" * 64, "Name": project, "Driver": "bridge", "Scope": "local",
                "Internal": False, "Attachable": False, "Ingress": False, "ConfigOnly": False,
                "Options": {}, "ConfigFrom": {"Network": ""},
                "Containers": {item["Id"]: {"EndpointID": "endpoint-" + name}
                               for name, item in containers.items()},
            }
            if scenario.endswith("two-networks"):
                for name, item in containers.items():
                    observed = network_containers[name]
                    item["Id"] = observed["Id"]
                    item["HostConfig"]["NetworkMode"] = observed["HostConfig"]["NetworkMode"]
                    item["NetworkSettings"] = deepcopy(observed["NetworkSettings"])
                network = {"networks": deepcopy(topology["networks"])}
            selection = KomodoSelection(
                KomodoDeclaration.from_dict(desired), "fleet-a", "install-a", 1, "policy-1",
                1900001000, "e" * 64, runtime, tooling, fixed_material if fixed else material,
                adoption=() if fixed else tuple(
                    (name, native_fingerprint(item)) for name, item in containers.items()),
                adopted_public_identity=None if fixed else identity_digest(material),
                adopted_network=None if fixed else network_fingerprint(network),
            )
            if fixed:
                created_containers = deepcopy(containers)
                created_network = deepcopy(network)
                containers.clear()
                network = None
            if scenario == "missing-adoption":
                with pytest.raises(PlanError, match="requires exact incumbent"):
                    replace(selection, adoption=(), adopted_public_identity=None)
                with pytest.raises(PlanError, match="code-resolved"):
                    replace(selection, declaration=KomodoDeclaration.from_dict(declaration()))
                return
            current = [selection]
            harness = Harness(keys)
            native_calls = []

            def apply(*arguments, **options):
                nonlocal network
                native_calls.append("apply")
                if not fixed:
                    return
                profile = decode(options["expected"])
                for name, item in created_containers.items():
                    service = profile["services"][name]
                    item["Config"].update(
                        Labels=service["labels"],
                        Env=[key + "=" + value for key, value in service["environment"].items()],
                        Entrypoint=[], Cmd=[value.replace("$$", "$")
                                            for value in service.get("command", [])],
                    )
                    item["Mounts"] = [
                        {"Type": "bind", "Source": mount["source"], "Destination": mount["target"],
                         "RW": not mount["read_only"]}
                        for mount in service["volumes"]
                    ] + [
                        {"Type": "bind", "Source": str(getattr(fixed_material, secret)),
                         "Destination": "/run/secrets/" + secret, "RW": False}
                        for secret in service.get("secrets", [])
                    ]
                    if unstarted:
                        item["State"].update(Status="created", Running=False, Pid=0, StartedAt="")
                        item["NetworkSettings"]["Networks"][project].update(NetworkID="", EndpointID="")
                containers.update(created_containers)
                network = {**created_network,
                           "Containers": {} if unstarted else created_network["Containers"], "Labels": {
                    "io.fleet-manager.owner": selection.owner,
                    "com.docker.compose.project": desired["project"],
                    "com.docker.compose.network": "default",
                }}

            native = SimpleNamespace(
                tooling=tooling, native_failure=None,
                prerequisites=lambda *a, **k: deepcopy(images),
                verify_host=lambda *a, **k: None,
                inspect=lambda *a, **k: deepcopy(containers),
                network=lambda *a, **k: deepcopy(network),
                _apply=apply,
            )
            owner = KomodoOwner(current=lambda: current[0], admission=harness.owner,
                                authority="gateway-a", clock=harness.clock)
            owner.native = native
            revision = 0

            def repair():
                monkeypatch.setattr("fleet_manager.komodo_repair.KomodoNative", lambda tooling: native)
                return KomodoRepairOwner(
                    resolve=lambda *a: KomodoRepairSelection(
                        selection, replace(selection, release_digest="f" * 64),
                        request.request_digest, "9" * 64, "transition-a", 1, 1900001000),
                    admission=harness.owner, authority="gateway-a", clock=harness.clock,
                )

            async def invoke(request, status=False, failed=False):
                nonlocal revision
                revision += 1
                harness.current = policy(revision=revision, enrollments=(actor(operations=(
                    OperationGrant("manager.converge", "host-a", 1, request.request_digest),
                )),))
                permit = harness.admit(request)
                operation = owner.inspect if status else owner.settle_failed if failed else owner.converge
                return await asyncio.to_thread(operation, request, permit)

            request = intent(deployment=selection.deployment_digest)
            if scenario.startswith("failed-"):
                with pytest.raises(PlanError, match="network attachment" if unstarted else "Core version"):
                    await invoke(request)
                assert state_record[0]["attempt"]["native_settled"] is True
                if scenario != "failed-repeat-running" and not unstarted:
                    containers["core"]["State"].update(Status="exited", Running=False, Pid=0)
                reconcile = NodeJournal.reconcile
                settled = []
                if scenario in (
                    "failed-lost-terminal", "failed-pending-terminal",
                    "failed-recovered-converge", "failed-recovered-settle",
                    "failed-recovered-foreign-request", "failed-recovered-changed-evidence",
                    "failed-unstarted-converge", "failed-unstarted-settle",
                    "failed-unstarted-foreign-network",
                    "failed-unstarted-keys-converge", "failed-unstarted-keys-settle",
                    "failed-unstarted-keys-native-to-repair", "failed-unstarted-keys-repair-to-native",
                    "failed-unstarted-keys-missing-outcome", "failed-unstarted-keys-corrupt-outcome",
                    "failed-unstarted-keys-missing-terminal-outcome",
                    "failed-unstarted-keys-corrupt-terminal-outcome",
                ):
                    def lost_failure(journal, original, outcome, **options):
                        if scenario == "failed-lost-terminal" or "terminal-outcome" in scenario:
                            settled.append(reconcile(journal, original, outcome, **options))
                        raise PlanError("fixture-lost-failure-receipt")

                    monkeypatch.setattr(NodeJournal, "reconcile", lost_failure)
                    with pytest.raises(PlanError, match="fixture-lost-failure"):
                        if scenario.endswith("repair-to-native"):
                            await asyncio.to_thread(
                                repair().reconcile, harness.identity(), request,
                                role="client", installation="client-a", failed=True,
                            )
                        else:
                            await invoke(request, failed=True)
                    assert state_record[0]["failed"] is not None
                    monkeypatch.setattr(NodeJournal, "reconcile", reconcile)
                if unstarted:
                    original_failed = deepcopy(state_record[0]["failed"])
                    if scenario.endswith("-outcome"):
                        before = deepcopy(state_record[0])
                        path = runtime / selection.resource / "receipts" / (digest(original_failed) + ".json")
                        journal = NodeJournal(
                            runtime / selection.resource / "node.sqlite",
                            authority="gateway-a", installation="install-a", target="host-a")
                        original_receipt = journal.inspect(request)
                        assert original_receipt.to_dict()["status"] == (
                            "failed" if "terminal-outcome" in scenario else "unknown")
                        with damaged_outcome(path, missing="missing" in scenario):
                            status = await invoke(request, status=True)
                            assert "retained-terminal-outcome-unavailable" in status["blockers"]
                            assert not status["converged"] and state_record[0] == before
                            with pytest.raises(PlanError, match="immutable|outcome"):
                                await invoke(request)
                            assert state_record[0] == before
                            assert journal.inspect(request) == original_receipt
                        receipt = await invoke(request)
                        assert receipt.to_dict()["status"] == "failed"
                        assert state_record[0]["attempt"] is None
                        assert state_record[0]["failed"] == original_failed
                        assert native_calls == ["apply"] and not requests
                        return
                    if scenario.endswith(("native-to-repair", "repair-to-native")):
                        before = deepcopy(state_record[0])
                        for name, item in containers.items():
                            item["State"].update(
                                Status="running", Running=True, Pid=1, StartedAt="recovered-start")
                            item["NetworkSettings"]["Networks"][project].update(
                                NetworkID=network["Id"], EndpointID="endpoint-" + name)
                        network["Containers"] = {
                            item["Id"]: {"EndpointID": "endpoint-" + name}
                            for name, item in containers.items()
                        }
                        recovered = True
                        for failed in (False, True):
                            with pytest.raises(PlanError, match="terminal decision.*owner"):
                                if scenario.endswith("native-to-repair"):
                                    await asyncio.to_thread(
                                        repair().reconcile, harness.identity(), request,
                                        role="client", installation="client-a", failed=failed,
                                    )
                                else:
                                    await invoke(request, failed=failed)
                            assert state_record[0] == before
                        if scenario.endswith("repair-to-native"):
                            receipt = await asyncio.to_thread(
                                repair().reconcile, harness.identity(), request,
                                role="client", installation="client-a", failed=True,
                            )
                        else:
                            receipt = await invoke(request)
                        assert receipt.to_dict()["status"] == "failed"
                        assert receipt.to_dict()["result_ref"] == digest(original_failed)
                        assert state_record[0]["failed"] == original_failed
                        assert state_record[0]["attempt"] is None
                        assert native_calls == ["apply"] and not requests
                        return
                    if scenario == "failed-unstarted-foreign-network":
                        containers["core"]["NetworkSettings"]["Networks"][project]["NetworkID"] = "9" * 64
                        for failed in (False, True):
                            with pytest.raises(PlanError, match="network attachment"):
                                await invoke(request, failed=failed)
                        assert state_record[0]["attempt"] is not None
                        assert state_record[0]["failed"] == original_failed
                        assert native_calls == ["apply"] and not requests
                        return
                    receipt = await invoke(request, failed=scenario.endswith("-settle"))
                    assert receipt.to_dict()["status"] == "failed"
                    assert state_record[0]["attempt"] is None
                    assert state_record[0]["failed"] == original_failed
                    assert await invoke(request) == receipt
                    status = await invoke(request, status=True)
                    assert status["accepted"] == "failed" and not status["converged"]
                    assert "native-network-custody-changed-or-unproved" not in status["blockers"]
                    if "-keys-" in scenario:
                        assert status["blockers"] == ["owned-core-not-running"]
                    assert native_calls == ["apply"] and not requests
                    return
                if scenario.startswith("failed-recovered-"):
                    original_failed = deepcopy(state_record[0]["failed"])
                    recovered = True
                    containers["core"]["State"].update(Status="running", Running=True, Pid=1)
                    if scenario in (
                        "failed-recovered-foreign-request", "failed-recovered-changed-evidence",
                    ):
                        if scenario == "failed-recovered-foreign-request":
                            state_record[0]["attempt"]["failure_outcome"]["request_digest"] = "0" * 64
                        else:
                            state_record[0]["failed"]["reason"] = "changed-evidence"
                        with pytest.raises(PlanError):
                            await invoke(request)
                        assert state_record[0]["attempt"] is not None
                        assert state_record[0]["good"] is None and native_calls == []
                        return
                    receipt = await invoke(request, failed=scenario == "failed-recovered-settle")
                    assert receipt.to_dict()["status"] == "failed"
                    assert receipt.to_dict()["result_ref"] == digest(original_failed)
                    assert state_record[0]["attempt"] is None
                    assert state_record[0]["failed"] == original_failed
                    assert state_record[0]["good"] is None and native_calls == []
                    assert await invoke(request) == receipt
                    return
                if not settled:
                    settled.append(await invoke(request, failed=True))
                original_failed = deepcopy(state_record[0]["failed"])
                if scenario == "failed-native-drift":
                    containers["core"]["Config"]["Env"].append("UNSELECTED_SETTING=changed")
                    with pytest.raises(PlanError, match="replaced or changed"):
                        await invoke(request)
                else:
                    receipt = await invoke(request)
                    assert receipt == settled[0] and receipt.to_dict()["status"] == "failed"
                    assert await invoke(request) == receipt
                    result = await invoke(request, status=True)
                    assert result["accepted"] == "failed" and result["converged"] is False
                    assert result["native_mutations_performed"] is False
                assert state_record[0]["failed"] == original_failed and native_calls == []
                return
            if scenario == "expired-during-selection":
                now = [harness.clock()]
                owner.clock = lambda: now[0]
                original_digest = KomodoSelection.deployment_digest.fget

                def advancing_digest(selected):
                    result = original_digest(selected)
                    now[0] = selected.valid_until
                    return result

                monkeypatch.setattr(KomodoSelection, "deployment_digest", property(advancing_digest))
                with pytest.raises(PlanError, match="current selected owner"):
                    owner._selected(request)
                assert state_record[0] is None and native_calls == [] and requests == []
                return
            if scenario.startswith("lost-terminal"):
                reconcile = NodeJournal.reconcile

                def lost_reply(*arguments, **options):
                    if "committed" in scenario:
                        reconcile(*arguments, **options)
                    raise PlanError("fixture-lost-terminal-receipt")

                monkeypatch.setattr(NodeJournal, "reconcile", lost_reply)
                with pytest.raises(PlanError, match="fixture-lost-terminal"):
                    await invoke(request)
                assert state_record[0]["attempt"]["native_settled"] is True
                monkeypatch.setattr(NodeJournal, "reconcile", reconcile)
                if scenario == "lost-terminal-fixed-replaced-container":
                    before = deepcopy(state_record[0])
                    before_http = len(requests)
                    previous = containers["core"]["Id"]
                    containers["core"]["Id"] = "9" * 64
                    network["Containers"][containers["core"]["Id"]] = network["Containers"].pop(previous)
                    with pytest.raises(PlanError, match="replaced or changed"):
                        await invoke(request)
                    assert state_record[0] == before and native_calls == ["apply"]
                    assert len(requests) == before_http
                    return
                if scenario.endswith("-outcome"):
                    before = deepcopy(state_record[0])
                    path = runtime / selection.resource / "receipts" / (digest(before["good"]) + ".json")
                    journal = NodeJournal(
                        runtime / selection.resource / "node.sqlite",
                        authority="gateway-a", installation="install-a", target="host-a")
                    original_receipt = journal.inspect(request)
                    assert original_receipt.to_dict()["status"] == (
                        "succeeded" if "committed" in scenario else "unknown")
                    with damaged_outcome(path, missing="missing" in scenario):
                        status = await invoke(request, status=True)
                        assert "retained-terminal-outcome-unavailable" in status["blockers"]
                        assert not status["converged"] and state_record[0] == before
                        with pytest.raises(PlanError, match="immutable|outcome"):
                            await invoke(request)
                        assert state_record[0] == before and journal.inspect(request) == original_receipt
                    receipt = await invoke(request)
                    assert receipt.to_dict()["status"] == "succeeded"
                    assert state_record[0]["attempt"] is None
                    assert state_record[0]["good"] == before["good"] and native_calls == []
                    return
            receipt = await invoke(request)
            assert receipt.to_dict()["status"] == "succeeded"
            original = deepcopy(state_record[0])
            if scenario == "refreshed-order":
                observed = decode(material.encoded)
                for mounts in observed["mounts"].values():
                    mounts.reverse()
                observed["volumes"].reverse()
                current[0] = replace(selection, material=replace(material, encoded=canonical(observed)))
                assert current[0].deployment_digest == selection.deployment_digest
            if scenario in ("adopt-repeat-status", "lost-terminal", "global-trust", "refreshed-order",
                            "adopt-two-networks", "lost-terminal-two-networks"):
                assert await invoke(request) == receipt
                result = await invoke(request, status=True)
                assert result["converged"] and result["currently_healthy"]
                assert result["native_mutations_performed"] is False
                assert state_record[0]["good"]["fixed_profile"] is False
                assert state_record[0]["good"]["selection"]["declaration"]["images"]["mongo"]["version"] == "7.0.45"
                assert len(requests) == (20 if scenario.startswith("lost-terminal") else 15)
            else:
                if scenario == "update":
                    revised = selection.declaration.to_dict()
                    revised["revision"] = "config-2"
                    current[0] = replace(
                        selection, declaration=KomodoDeclaration.from_dict(revised),
                        compatible_with=(selection.deployment_digest,),
                    )
                elif scenario == "stopped":
                    containers["core"]["State"]["Running"] = False
                elif scenario == "mount-drift":
                    containers["mongo"]["Mounts"][0]["Name"] = "replaced-volume"
                elif scenario == "credential-drift":
                    material.admin_password.write_bytes(b"changed-original-login-password")
                elif scenario == "private-key-drift":
                    incumbent.files["core_key"].write_bytes(b"changed-original-private-key")
                elif scenario == "native-drift":
                    containers["core"]["Config"]["Env"].append("UNSELECTED_SETTING=changed")
                elif scenario == "missing":
                    containers.clear()
                retry = intent("retry-1", deployment=current[0].deployment_digest)
                with pytest.raises(PlanError):
                    await invoke(retry)
                assert state_record[0] == original
                journal = NodeJournal(
                    runtime / digest({"daemon": tooling.daemon_id, "project": desired["project"]})
                    / "node.sqlite", authority="gateway-a", installation="install-a", target="host-a",
                )
                assert journal.inspect(retry) is None
            assert native_calls == []

    asyncio.run(exercise())
