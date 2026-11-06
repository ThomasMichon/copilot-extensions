"""Selected hosting, real TLS ownership and the inert dependency-free client."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from functools import lru_cache
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
from types import SimpleNamespace

import aiohttp
import pytest

from fleet_manager import gateway_host as host
from fleet_manager import gateway_host_runtime, gateway_host_start
from fleet_manager.__main__ import main
from fleet_manager.gateway_enrollment import SCHEMA
from fleet_manager.planning import PlanError
from fleet_manager.private_state import create_private_file, private_directory
from gateway_fixtures import Fixtures
from test_machine_identity import MachineHarness


@lru_cache(maxsize=1)
def _tls_material():
    with tempfile.TemporaryDirectory(prefix="gateway-tls-") as directory:
        root = Path(directory)
        Fixtures(root)
        return (root / "tls.pem").read_bytes(), (root / "tls-key.pem").read_bytes()


def selected(root):
    private_directory(root)
    certificate, key = _tls_material()
    (root / "tls.pem").write_bytes(certificate)
    with os.fdopen(create_private_file(root / "tls-key.pem"), "wb") as stream:
        stream.write(key)
    policy = MachineHarness().document
    policy["identity"]["trust_valid_until"] = int(time.time()) + 3600
    for mapping in policy["enrollment"]["mappings"]:
        mapping["expires_at"] = int(time.time()) + 1800
    host.write_private(root / "policy.json", {
        "schema": SCHEMA, "version": 1, "policy": policy, "delegations": [],
    })
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    listener = {
        "schema": "fleet-manager.gateway-service-listener", "version": 1,
        "fleet": "fleet-a", "gateway": "gateway-a", "host": "127.0.0.1", "port": port,
        "origin": f"https://localhost:{port}", "certificate": str(root / "tls.pem"),
        "key": str(root / "tls-key.pem"), "remote": False,
    }
    host.write_private(root / "listener.json", listener)
    selection = {
        "schema": "fleet-manager.gateway-host", "version": 1, "role": "gateway",
        "policy": str(root / "policy.json"), "tls": str(root / "listener.json"),
        "journal": str(root / "data" / "operations.sqlite"),
        "authority": "authority-a", "installation": "installation-a",
    }
    host.write_private(root / host.CONFIG, selection)
    return selection


@pytest.mark.parametrize("command", ["__source-setup", "__source-start"])
def test_unselected_callbacks_refuse_without_importing_hosting_or_creating_state(tmp_path, monkeypatch, command):
    import builtins

    root = tmp_path / "not-created"
    monkeypatch.setenv("FLEET_MANAGER_HOME", str(root))
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"agent_procutil", "aiohttp", "zdd"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert main([command]) == 1
    assert not root.exists()
    assert main([command, "--unexpected"]) == 1


def test_lost_existing_host_selection_is_not_a_silent_client_downgrade(tmp_path):
    root = tmp_path / "installation"
    private_directory(root)
    private_directory(root / host.STATE)
    with pytest.raises(PlanError, match="configuration is missing"):
        host.optional_selection(root)


@pytest.mark.parametrize("change", [
    {"role": "ssh-client"}, {"policy": "relative.json"}, {"version": True},
    {"journal": 1}, {"unknown": "field"}, {"authority": "not an identifier"},
])
def test_explicit_selection_refuses_unsupported_inputs_without_host_state(tmp_path, change):
    root = tmp_path / "installation"
    selection = selected(root)
    host.write_private(root / host.CONFIG, selection | change)
    with pytest.raises(PlanError):
        host.optional_selection(root)
    assert not (root / host.STATE).exists()
    assert not Path(selection["journal"]).exists()


def test_host_preflight_uses_existing_enrollment_journal_without_publishing_a_floor(tmp_path):
    root = tmp_path / "installation"
    selected(root)
    listener, ledger = host.prepare(root, host.optional_selection(root))
    assert listener.gateway == "gateway-a"
    import sqlite3

    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM enrollment_floors").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM operations").fetchone() == (0,)


@pytest.mark.parametrize("reference", ["certificate", "key"])
def test_host_preflight_refuses_cwd_dependent_tls_before_state_creation(tmp_path, monkeypatch, reference):
    from fleet_manager.planning import decode

    root = tmp_path / "i"
    selection = selected(root)
    path = Path(selection["tls"])
    listener = decode(path.read_bytes())
    listener[reference] = Path(listener[reference]).name
    host.write_private(path, listener)
    monkeypatch.chdir(root)
    assert Path(listener[reference]).is_file()
    with pytest.raises(PlanError, match="TLS references require absolute ordinary paths"):
        host.prepare(root, selection)
    with pytest.raises(PlanError, match="TLS references require absolute ordinary paths"):
        host.startup_digest(selection)
    assert not Path(selection["journal"]).exists()
    assert not (root / host.STATE).exists()


def test_candidate_preflight_refuses_prior_policy_without_republishing_or_enrolling(tmp_path):
    from fleet_manager.gateway_service_policy import ServiceAuthority
    from fleet_manager.planning import decode

    root = tmp_path / "installation"
    selection = selected(root)
    policy_path = Path(selection["policy"])
    policy = decode(policy_path.read_bytes())
    policy["policy"]["manifest"]["revision"] = 3
    host.write_private(policy_path, policy)
    listener, ledger = host.prepare(root, selection)
    owner = ServiceAuthority(
        fleet=listener.fleet, gateway=listener.gateway, source=policy_path.read_bytes,
        enrollment_journal=ledger, clock=lambda: int(time.time()),
    )
    owner.reload()
    before = ledger.path.read_bytes()
    policy["policy"]["manifest"]["revision"] = 2
    host.write_private(policy_path, policy)
    with pytest.raises(PlanError, match="stale enrollment policy"):
        host.prepare(root, selection)
    assert ledger.path.read_bytes() == before


def test_indirect_journal_preflight_does_not_create_target_state(tmp_path):
    root = tmp_path / "i"
    selection = selected(root)
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = root / "alias"
    try:
        alias.symlink_to(physical, target_is_directory=True)
    except OSError:
        pytest.skip("native directory symlinks are unavailable")
    selection["journal"] = str(alias / "data" / "operations.sqlite")
    with pytest.raises(PlanError, match="ordinary directory"):
        host.prepare(root, selection)
    assert not (physical / "data").exists()


def test_only_selected_gateway_extra_enables_existing_installer_callbacks(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "tools"))
    from runtime_application_project import install_callbacks

    assert install_callbacks("fleet-manager", extras=("gateway-host",)) == (
        ["__source-setup"], ["__source-start"],
    )
    for extras in ((), ("service-gateway",), ("ssh-client",), ("auth",)):
        assert install_callbacks("fleet-manager", extras=extras) == ([], [])


def ssh_selected(root):
    from test_ssh_machine import machine_config

    private_directory(root)
    value, policy, directory = machine_config(root)
    local = root.parent / ("ssh-local-" + root.name)
    local.mkdir()
    value.update(
        state_dir=str(local / "state"), authorization_dir=str(local / "authorization"),
        config_d=str(local / "ssh" / "config.d"), ssh_config=str(local / "ssh" / "config"),
    )
    host.write_private(root / "ssh-client.json", value)
    selection = {
        "schema": "fleet-manager.gateway-host", "version": 2, "role": "ssh-client",
        "configuration": str(root / "ssh-client.json"),
        "authority": "authority-a", "installation": "installation-a",
    }
    host.write_private(root / host.CONFIG, selection)
    return selection, value, policy, directory


@pytest.mark.parametrize("change", [
    {"version": 1}, {"version": True}, {"role": "gateway"},
    {"configuration": "relative"}, {"policy": "unselected"},
])
def test_ssh_host_selection_refuses_unsupported_or_mixed_forms_before_effects(tmp_path, change):
    selection, value, _, _ = ssh_selected(tmp_path / "i")
    host.write_private(tmp_path / "i" / host.CONFIG, selection | change)
    with pytest.raises(PlanError):
        host.optional_selection(tmp_path / "i")
    assert not Path(value["state_dir"]).exists()
    assert not (tmp_path / "i" / host.STATE).exists()


def test_ssh_host_preflight_is_inert_and_keeps_provider_token_renewal(tmp_path):
    from test_ssh_machine import configured

    root = tmp_path / "i"
    selection, _, _, _ = ssh_selected(root)
    config, _ = host.prepare(root, host.optional_selection(root))
    assert config.to_dict()["version"] == 2
    token_value, _, _ = configured(root)
    value = config.to_dict() | {
        "version": 1, "access_token_file": token_value["access_token_file"],
    }
    value.pop("authentication")
    host.write_private(Path(selection["configuration"]), value)
    host.prepare(root, selection)
    before = host.startup_digest(selection)
    Path(value["access_token_file"]).write_text("synthetic-renewed", encoding="ascii")
    assert host.startup_digest(selection) == before
    assert not Path(value["state_dir"]).exists()
    assert not (root / host.STATE).exists()
    assert not Path(value["config_d"]).exists()
    value["state_dir"] = str(root / host.STATE / "lease")
    host.write_private(Path(selection["configuration"]), value)
    with pytest.raises(PlanError, match="separate from host lifecycle state"):
        host.prepare(root, selection)
    assert not (root / host.STATE).exists()


@pytest.mark.parametrize("scope", ["config", "fragments", "authorization", "sources", "locks"])
def test_ssh_host_state_is_outside_all_writer_and_derived_scopes(tmp_path, scope):
    from agent_ssh import gateway_profile, ssh_profile

    root = tmp_path / (
        gateway_profile._SOURCE_DIR if scope == "sources"
        else ssh_profile._LOCK_DIR_NAME if scope == "locks" else "i"
    )
    selection, value, _, _ = ssh_selected(root)
    value["state_dir"] = str(tmp_path / "consumer-lease")
    if scope == "config":
        value["ssh_config"] = str(root / host.STATE / gateway_host_start.PENDING)
    elif scope == "fragments":
        value["config_d"] = str(root / host.STATE / "config.d")
    elif scope == "authorization":
        value["authorization_dir"] = str(root / host.STATE / "authorization")
    else:
        value["config_d"] = str(tmp_path / "config.d")
    host.write_private(Path(selection["configuration"]), value)
    with pytest.raises(ValueError, match="outside local writer scopes"):
        host.prepare(root, selection)
    assert not (root / host.STATE).exists()
    assert not Path(value["state_dir"]).exists()


@pytest.mark.parametrize("destination", [
    host.CONFIG, "ssh-client.json", "current-version", "last-known-good",
    "versions/candidate/.venv/config",
])
def test_ssh_host_preflight_preserves_installation_metadata_from_writer_scope(tmp_path, destination):
    root = tmp_path / "i"
    selection, value, _, _ = ssh_selected(root)
    path = root / destination
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained installation metadata")
    host.write_private(Path(selection["configuration"]), value | {"ssh_config": str(path)})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="outside local writer scopes"):
        host.prepare(root, selection)
    assert path.read_bytes() == before
    assert not (root / host.STATE).exists()
    assert not Path(value["state_dir"]).exists()


@pytest.mark.parametrize("reference", [
    "configuration", "policy_path", "identity_file", "public_key_file", "proof_key_file",
])
def test_ssh_host_preflight_protects_external_input_references_from_writers(tmp_path, reference):
    root = tmp_path / "i"
    selection, value, _, _ = ssh_selected(root)
    original = Path(selection[reference] if reference == "configuration" else value[reference])
    external = tmp_path / "external-input"
    external.write_bytes(original.read_bytes())
    if reference == "configuration":
        selection["configuration"] = str(external)
    else:
        value[reference] = str(external)
    value["ssh_config"] = str(external)
    host.write_private(Path(selection["configuration"]), value)
    before = external.read_bytes()
    with pytest.raises(ValueError, match="outside local writer scopes"):
        host.prepare(root, selection)
    assert external.read_bytes() == before
    assert not (root / host.STATE).exists()
    assert not Path(value["state_dir"]).exists()


def test_ssh_host_preflight_keeps_consumer_lease_outside_installed_slots(tmp_path):
    root = tmp_path / "i"
    selection, value, _, _ = ssh_selected(root)
    slot = root / "versions" / "candidate"
    host.write_private(Path(selection["configuration"]), value | {"state_dir": str(slot)})
    with pytest.raises(PlanError, match="separate from host lifecycle state"):
        host.prepare(root, selection)
    assert not slot.exists()
    assert not (root / host.STATE).exists()


@pytest.mark.parametrize("failure", ["missing", "empty", "invalid-utf8", "overlong"])
def test_ssh_host_token_preflight_refuses_before_lifetime_or_writer_state(tmp_path, failure):
    from fleet_manager.gateway_contract import GatewayError
    from test_ssh_machine import configured

    root = tmp_path / "i"
    selection, value, _, _ = ssh_selected(root)
    token_value, _, _ = configured(root)
    value = value | {"version": 1, "access_token_file": token_value["access_token_file"]}
    value.pop("authentication")
    token = Path(value["access_token_file"])
    if failure == "missing":
        token.unlink()
    else:
        token.write_bytes({
            "empty": b" \n", "invalid-utf8": b"\xff", "overlong": b"x" * 8193,
        }[failure])
    host.write_private(Path(selection["configuration"]), value)
    with pytest.raises(GatewayError):
        host.prepare(root, selection)
    assert not (root / host.STATE).exists()
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["config_d"]).exists()


@pytest.mark.parametrize("reference", ["identity_file", "public_key_file", "gateway_ca"])
def test_ssh_host_preflight_reads_all_fingerprinted_references_before_lifetime_state(tmp_path, reference):
    root = tmp_path / "i"
    selection, value, _, _ = ssh_selected(root)
    value[reference] = str(root / "absent-reference")
    host.write_private(Path(selection["configuration"]), value)
    with pytest.raises((OSError, ValueError)):
        host.prepare(root, selection)
    assert not (root / host.STATE).exists()
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["config_d"]).exists()


def test_ssh_host_refuses_config_replacement_during_preparation_before_consumer(tmp_path, monkeypatch):
    from fleet_manager import ssh_machine

    root = tmp_path / "i"
    selection, value, _, _ = ssh_selected(root)
    private_directory(root / host.STATE)
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "runtime_custody", lambda _root: nullcontext(slot))
    nonce = "3" * 32
    host.write_private(root / host.STATE / gateway_host_start.PENDING, {
        "generation": slot.name, "token": nonce,
    })
    original = host.prepare

    def replaced(root, selected):
        prepared = original(root, selected)
        host.write_private(Path(selected["configuration"]), value | {
            "gateway_origin": "https://other.example.test",
        })
        return prepared

    async def forbidden(*_args, **_kwargs):
        pytest.fail("changed startup inputs opened the SSH consumer")

    monkeypatch.setattr(host, "prepare", replaced)
    monkeypatch.setattr(ssh_machine, "run", forbidden)
    with pytest.raises(PlanError, match="inputs changed before admission"):
        asyncio.run(gateway_host_runtime.run(root, nonce))
    assert not (root / host.STATE / host.RUNTIME).exists()
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["config_d"]).exists()


def test_unconverged_ssh_host_releases_consumer_and_retention_on_deselection(tmp_path, monkeypatch):
    from fleet_manager import ssh_machine
    from fleet_manager.ssh_directory import SSHDirectoryClient
    from test_ssh_machine import reached

    root = tmp_path / "i"
    _, value, _, _ = ssh_selected(root)
    private_directory(root / host.STATE)
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    retained = {"released": False}

    @contextmanager
    def custody(_root):
        try:
            yield slot
        finally:
            retained["released"] = True

    monkeypatch.setattr(host, "runtime_custody", custody)
    nonce = "4" * 32
    host.write_private(root / host.STATE / gateway_host_start.PENDING, {
        "generation": slot.name, "token": nonce,
    })

    async def exercise():
        entered, settled = asyncio.Event(), asyncio.Event()

        async def wait(*_args, **_kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()

        monkeypatch.setattr(SSHDirectoryClient, "wait", wait)
        task = asyncio.create_task(gateway_host_runtime.run(root, nonce))
        try:
            await reached(entered.is_set, task)
            assert not retained["released"]
            (root / "current-version").write_text("successor", encoding="utf-8")
            await asyncio.wait_for(task, 5)
            assert settled.is_set() and retained["released"]
            assert not (root / host.STATE / host.RUNTIME).exists()
            lease = ssh_machine._lease(Path(value["state_dir"]))
            lease.acquire()
            lease.release()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize("held", [False, True])
def test_ssh_host_waits_for_complete_directory_and_settles_owned_consumer(tmp_path, monkeypatch, denied, held):
    from fleet_manager import ssh_machine
    from fleet_manager.ssh_directory import SSHDirectoryClient
    from test_gateway_ssh_provider import key
    from test_ssh_machine import reached, update

    root = tmp_path / "i"
    selection, value, policy, directory = ssh_selected(root)
    private_directory(root / host.STATE)
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "runtime_custody", lambda _root: nullcontext(slot))
    nonce = "2" * 32
    host.write_private(root / host.STATE / gateway_host_start.PENDING, {
        "generation": slot.name, "token": nonce,
    })

    async def exercise():
        incoming, entered = asyncio.Queue(), asyncio.Event()
        positions = []
        attempted = asyncio.Event()
        incumbent = None
        if held:
            original_lease = ssh_machine._lease
            incumbent = original_lease(Path(value["state_dir"]))
            incumbent.acquire()

            def observed_lease(path):
                lease = original_lease(path)
                original_acquire = lease.acquire

                def acquire():
                    attempted.set()
                    return original_acquire()

                return SimpleNamespace(acquire=acquire, release=lease.release)

            monkeypatch.setattr(ssh_machine, "_lease", observed_lease)

        async def wait(_client, after, *, seconds=25):
            positions.append(after)
            entered.set()
            return await incoming.get()

        monkeypatch.setattr(SSHDirectoryClient, "wait", wait)
        if denied:
            def refuse(_update):
                raise PermissionError("selected-writer-refused")

            original = ssh_machine._consumer

            def selected_consumer(config):
                original(config)
                return refuse

            monkeypatch.setattr(ssh_machine, "_consumer", selected_consumer)
        task = asyncio.create_task(gateway_host_runtime.run(root, nonce))
        try:
            if held:
                await reached(attempted.is_set, task)
                assert not entered.is_set() and not (root / host.STATE / host.RUNTIME).exists()
                incumbent.release()
                incumbent = None
            await reached(entered.is_set, task)
            assert gateway_host_start.inspect(root, selection)["state"] == "launch-unresolved"
            assert not (root / host.STATE / host.RUNTIME).exists()
            await incoming.put(update(policy, directory))
            if denied:
                with pytest.raises(PermissionError, match="selected-writer-refused"):
                    await asyncio.wait_for(task, 5)
                assert not (root / host.STATE / host.RUNTIME).exists()
                assert positions == [0]
                return
            await reached((root / host.STATE / host.RUNTIME).exists, task)
            status = await asyncio.to_thread(gateway_host_start.inspect, root, selection)
            assert status["role"] == "ssh-client" and status["ready"] and status["selected"]
            assert status["revision"] == directory["revision"]
            assert key(2) in Path(value["authorization_dir"], "authorized_keys.gateway").read_text()
            record = gateway_host_start._record(root)
            result = await asyncio.to_thread(gateway_host_start._call, record, "stop")
            assert not result["ready"]
            await asyncio.wait_for(task, 5)
            assert positions == [0, directory["revision"]]
        finally:
            if incumbent is not None:
                incumbent.release()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            lease = ssh_machine._lease(Path(value["state_dir"]))
            lease.acquire()
            lease.release()

    asyncio.run(exercise())


@pytest.mark.parametrize("cleanup_confirmed", [False, True])
def test_failed_spawn_keeps_unresolved_launch_until_native_cleanup(tmp_path, monkeypatch, cleanup_confirmed):
    import agent_procutil

    root = tmp_path / "i"
    selection = selected(root)
    slot = root / "versions" / "incoming"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)

    def failed(*args, **kwargs):
        raise agent_procutil.DaemonSpawnError(
            "fixture interrupted launch", cleanup_confirmed=cleanup_confirmed, process_created=True,
        )

    monkeypatch.setattr(agent_procutil, "spawn_daemon", failed)
    with pytest.raises(agent_procutil.DaemonSpawnError):
        gateway_host_start.ensure(root, selection)
    assert (gateway_host_start.pending_launch(root) is None) is cleanup_confirmed
    if not cleanup_confirmed:
        with pytest.raises(PlanError, match="launch outcome is unresolved"):
            gateway_host_start.stop(root, selection)


@pytest.mark.parametrize("interrupted", [False, True])
def test_late_launcher_preserves_published_host_after_another_caller_reuses_it(
    tmp_path, monkeypatch, interrupted,
):
    import agent_procutil

    root = tmp_path / "i"
    selection = selected(root)
    slot = root / "versions" / "incoming"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)
    state = {"now": time.monotonic(), "spawned": False, "published": False}

    class Process:
        def terminate(self):
            pytest.fail("A published, reused host is no longer launcher-owned")

    def spawn(command, **options):
        state["nonce"] = options["env_overrides"]["FLEET_MANAGER_HOST_NONCE"]
        state["spawned"] = True
        return Process()

    def clock():
        if state["spawned"] and not state["published"]:
            state["published"] = True
            nonce = state["nonce"]
            identity = {
                "generation": slot.name, "pid": 1, "birth": "1",
                "selection": host.selection_digest(selection),
            }
            gateway_host_runtime._publish(root, slot, nonce, identity, 1)
            state["adopted"] = gateway_host_start.ensure(root, selection)
            state["now"] += 100
            if interrupted:
                raise KeyboardInterrupt
        return state["now"]

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    monkeypatch.setattr(gateway_host_start, "_probe", lambda value: {
        "ready": True, "startup": host.startup_digest(selection),
    })
    monkeypatch.setattr(
        gateway_host_start, "time", SimpleNamespace(monotonic=clock, sleep=lambda _: None),
    )
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            gateway_host_start.ensure(root, selection)
    else:
        assert gateway_host_start.ensure(root, selection)["reused"] is False
    assert state["adopted"]["reused"] is True
    assert gateway_host_start._record(root)["token"] == state["nonce"]
    assert gateway_host_start.pending_launch(root) is None


@pytest.mark.parametrize("retiring", [False, True])
def test_new_generation_preserves_unresolved_predecessor_launch(tmp_path, monkeypatch, retiring):
    import agent_procutil

    root = tmp_path / "i"
    selection = selected(root)
    private_directory(root / host.STATE)
    slot = root / "versions" / "incoming"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)
    pending = {"generation": "predecessor", "token": "1" * 32}
    if retiring:
        pending["retiring"] = True
    path = root / host.STATE / gateway_host_start.PENDING
    host.write_private(path, pending)
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("A new generation must not replay an unsettled launch")

    monkeypatch.setattr(agent_procutil, "spawn_daemon", forbidden)
    with pytest.raises(PlanError, match="launch outcome is unresolved"):
        gateway_host_start.ensure(root, selection)
    assert path.read_bytes() == before
    assert not (root / host.STATE / host.RUNTIME).exists()
    assert gateway_host_start.inspect(root, selection)["state"] == "launch-unresolved"
    with pytest.raises(PlanError, match="launch outcome is unresolved"):
        gateway_host_start.stop(root, selection)
    with pytest.raises(PlanError, match="selection changed"):
        gateway_host_runtime._publish(root, root / "versions" / "predecessor", "1" * 32, {}, 1)
    assert path.read_bytes() == before


@pytest.mark.parametrize("changed", ["generation", "listener"])
def test_reuse_revalidates_selection_and_inputs_after_probe(tmp_path, monkeypatch, changed):
    root = tmp_path / "i"
    selection = selected(root)
    private_directory(root / host.STATE)
    slot = root / "versions" / "incoming"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)
    value = {"generation": slot.name, "selection": host.selection_digest(selection)}
    monkeypatch.setattr(gateway_host_start, "_record", lambda root: value)
    startup = host.startup_digest(selection)

    def probe(current):
        if changed == "generation":
            (root / "current-version").write_text("successor", encoding="utf-8")
        else:
            from fleet_manager.planning import decode

            path = Path(selection["tls"])
            host.write_private(path, decode(path.read_bytes()) | {
                "port": 1, "origin": "https://localhost:1",
            })
        return {"ready": True, "startup": startup}

    monkeypatch.setattr(gateway_host_start, "_probe", probe)
    with pytest.raises(PlanError, match="changed during readiness"):
        gateway_host_start.ensure(root, selection)


@pytest.mark.parametrize("exit_confirmed", [False, True])
def test_unpublished_retirement_stays_fenced_until_native_exit(tmp_path, monkeypatch, exit_confirmed):
    import agent_procutil

    root = tmp_path / "i"
    selection = selected(root)
    slot = root / "versions" / "incoming"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)
    state = {}

    class Process:
        def poll(self):
            raise OSError("fixture startup observation failed")

        def terminate(self):
            assert gateway_host_start.pending_launch(root)["retiring"] is True
            assert gateway_host_start.inspect(root, selection)["state"] == "launch-unresolved"
            with pytest.raises(PlanError, match="launch admission changed"):
                gateway_host_start.require_launch(root, slot.name, state["nonce"])
            with pytest.raises(PlanError, match="launch outcome is unresolved"):
                gateway_host_start.stop(root, selection)

        def wait(self, **kwargs):
            if not exit_confirmed:
                raise TimeoutError("fixture native exit remains unknown")
            return 0

    def spawn(command, **options):
        state["nonce"] = options["env_overrides"]["FLEET_MANAGER_HOST_NONCE"]
        return Process()

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    with pytest.raises(OSError if exit_confirmed else TimeoutError):
        gateway_host_start.ensure(root, selection)
    if exit_confirmed:
        assert gateway_host_start.pending_launch(root) is None
    else:
        assert gateway_host_start.pending_launch(root)["retiring"] is True


def test_stale_publication_cannot_overwrite_a_ready_successor(tmp_path, monkeypatch):
    from zdd.cutover_lock import CutoverLockedError

    root = tmp_path / "i"
    selection = selected(root)
    private_directory(root / host.STATE)
    predecessor, successor = root / "versions" / "old", root / "versions" / "new"
    (root / "current-version").write_text(predecessor.name, encoding="utf-8")
    old_token, new_token = "1" * 32, "2" * 32
    pending = root / host.STATE / gateway_host_start.PENDING
    host.write_private(pending, {"generation": predecessor.name, "token": old_token})
    entered, resume = threading.Event(), threading.Event()
    original_write = host.write_private
    runtime = root / host.STATE / host.RUNTIME
    identity = {"pid": 1, "birth": "1", "selection": host.selection_digest(selection)}

    def paused_write(path, value):
        if path == runtime and value["generation"] == predecessor.name:
            entered.set()
            assert resume.wait(5)
        original_write(path, value)

    monkeypatch.setattr(host, "write_private", paused_write)
    with ThreadPoolExecutor(max_workers=1) as workers:
        publication = workers.submit(
            gateway_host_runtime._publish, root, predecessor, old_token,
            identity | {"generation": predecessor.name}, 1,
        )
        try:
            assert entered.wait(5)
            (root / "current-version").write_text(successor.name, encoding="utf-8")
            with pytest.raises(CutoverLockedError):
                gateway_host_start._lock(root, time.monotonic())
        finally:
            resume.set()
        publication.result(timeout=5)
    lock = gateway_host_start._lock(root, time.monotonic() + 5)
    try:
        host.write_private(pending, {"generation": successor.name, "token": new_token})
    finally:
        lock.release()
    gateway_host_runtime._publish(
        root, successor, new_token, identity | {"generation": successor.name}, 2,
    )
    with pytest.raises(PlanError, match="selection changed"):
        gateway_host_runtime._publish(
            root, predecessor, old_token, identity | {"generation": predecessor.name}, 1,
        )
    assert gateway_host_start._record(root)["generation"] == successor.name
    assert gateway_host_start.pending_launch(root) is None


@pytest.mark.parametrize("publication_delay", [0, 2])
@pytest.mark.parametrize("corruption", [
    "shape", "birth", "pid", "pid-boundary", "access-denied", "version", "empty", "oversized",
    "control-timeout", "control-refused",
])
def test_invalid_predecessor_evidence_does_not_veto_incoming_host(
    tmp_path, monkeypatch, publication_delay, corruption,
):
    import agent_procutil
    from zdd.cutover_lock import CutoverLockedError

    root = tmp_path / "i"
    selection = selected(root)
    private_directory(root / host.STATE)
    predecessor = {
        "schema": "fleet-manager.gateway-host-runtime",
        "version": True if corruption == "version" else 1,
        "generation": "predecessor", "selection": host.selection_digest(selection),
        "pid": {"pid": 1 << 64, "pid-boundary": 0xFFFFFFFF}.get(corruption, 1),
        "birth": "not-a-native-birth" if corruption == "birth" else "1",
        "port": 1, "token": "0" * 32,
    }
    if corruption.startswith("control-"):
        from zdd.diagnostics import process_start_time

        predecessor.update(pid=os.getpid(), birth=process_start_time(os.getpid()))

        def unavailable(*args, **kwargs):
            raise (
                TimeoutError("fixture control timed out") if corruption == "control-timeout"
                else ConnectionRefusedError("fixture control refused")
            )

        monkeypatch.setattr(gateway_host_start, "_call", unavailable)
    host.write_private(
        root / host.STATE / host.RUNTIME,
        {"unrecognized": "predecessor"} if corruption == "shape" else predecessor,
    )
    if corruption in {"empty", "oversized"}:
        (root / host.STATE / host.RUNTIME).write_bytes(
            b"" if corruption == "empty" else b"x" * (host.MAX_BYTES + 1),
        )
    slot = root / "versions" / "incoming"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)
    launched = []
    actual_record = gateway_host_start._record
    if corruption.startswith("control-"):
        with pytest.raises(PlanError, match="control is unavailable; native exit is not confirmed"):
            gateway_host_start.inspect(root, selection)
    if corruption == "access-denied":
        from zdd import process_exit

        def protected(*args, **kwargs):
            raise PermissionError("fixture protected predecessor")

        monkeypatch.setattr(process_exit, "open_process", protected)
        (root / "current-version").write_text(predecessor["generation"], encoding="utf-8")
        with pytest.raises(PermissionError):
            gateway_host_start.stop(root, selection)
        with pytest.raises(PermissionError):
            gateway_host_start.inspect(root, selection)
        (root / "current-version").write_text(slot.name, encoding="utf-8")
    if corruption == "version":
        with pytest.raises(host.RuntimeEvidenceError):
            actual_record(root)

    def admitted_record(root):
        try:
            unheld = gateway_host_start._lock(root, time.monotonic())
        except CutoverLockedError:
            return actual_record(root)
        else:
            unheld.release()
            pytest.fail("Readiness admission must serialize with runtime publication")

    monkeypatch.setattr(gateway_host_start, "_record", admitted_record)

    class Process:
        polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == publication_delay:
                publish(launched[0][1])
            return None

        def terminate(self):
            pytest.fail("A qualified incoming host must not be retired")

    def publish(options):
        host.write_private(root / host.STATE / host.RUNTIME, {
            "schema": "fleet-manager.gateway-host-runtime", "version": 1,
            "generation": slot.name, "selection": host.selection_digest(selection),
            "pid": 1, "birth": "1", "port": 1,
            "token": options["env_overrides"]["FLEET_MANAGER_HOST_NONCE"],
        })
        gateway_host_start.clear_launch(root, options["env_overrides"]["FLEET_MANAGER_HOST_NONCE"])

    def spawn(command, **options):
        launched.append((command, options))
        if not publication_delay:
            publish(options)
        return Process()

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    actual_probe = gateway_host_start._probe

    def probe(value):
        if value["generation"] == slot.name:
            return {"ready": True, "startup": host.startup_digest(selection)}
        if corruption == "access-denied":
            raise PermissionError("fixture protected predecessor")
        return actual_probe(value)

    monkeypatch.setattr(gateway_host_start, "_probe", probe)
    assert gateway_host_start.ensure(root, selection)["reused"] is False
    assert len(launched) == 1


def test_selected_host_serves_real_tls_and_confirms_exact_stop(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    selection = selected(root)
    private_directory(root / host.STATE)
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(host, "installed_slot", lambda root, current: slot)
    monkeypatch.setattr(host, "runtime_custody", lambda root: nullcontext(slot))
    nonce = "1" * 32
    host.write_private(root / host.STATE / gateway_host_start.PENDING, {
        "generation": slot.name, "token": nonce,
    })

    async def scenario():
        with pytest.raises(PlanError, match="launch outcome is unresolved"):
            await asyncio.to_thread(gateway_host_start.stop, root, selection)
        assert gateway_host_start.inspect(root, selection)["state"] == "launch-unresolved"
        assert not (root / host.STATE / host.RUNTIME).exists()
        task = asyncio.create_task(gateway_host_runtime.run(root, nonce))
        try:
            for _ in range(500):
                if (root / host.STATE / host.RUNTIME).exists() or task.done():
                    break
                await asyncio.sleep(.01)
            assert not task.done(), task.exception()
            status = await asyncio.to_thread(gateway_host_start.inspect, root, selection)
            assert status["selected"] and status["ready"]
            assert status["pid"] == os.getpid()
            assert (await asyncio.to_thread(gateway_host_start.ensure, root, selection))["reused"]
            from fleet_manager.planning import decode

            policy_path = Path(selection["policy"])
            policy = decode(policy_path.read_bytes())
            original_revision = status["revision"]
            policy["policy"]["manifest"]["revision"] += 1
            host.write_private(policy_path, policy)
            for _ in range(100):
                status = await asyncio.to_thread(gateway_host_start.inspect, root, selection)
                if status["revision"] != original_revision:
                    break
                await asyncio.sleep(.05)
            assert status["revision"] != original_revision
            assert status["selected"] and status["ready"]
            assert (await asyncio.to_thread(gateway_host_start.ensure, root, selection))["reused"]
            listener_path = Path(selection["tls"])
            listener = decode(listener_path.read_bytes())
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                changed_port = reservation.getsockname()[1]
            host.write_private(listener_path, listener | {
                "port": changed_port, "origin": f"https://localhost:{changed_port}",
            })
            runtime_path = root / host.STATE / host.RUNTIME
            retained_runtime = runtime_path.read_bytes()
            for failure in (TimeoutError, ConnectionRefusedError):
                import agent_procutil

                def unavailable(*args, **kwargs):
                    raise failure("fixture live control is unavailable")

                def forbidden(*args, **kwargs):
                    pytest.fail("Unknown current ownership must not create a replacement")

                with monkeypatch.context() as patch:
                    patch.setattr(gateway_host_start, "_call", unavailable)
                    patch.setattr(agent_procutil, "spawn_daemon", forbidden)
                    with pytest.raises(PlanError, match="ownership is unresolved"):
                        await asyncio.to_thread(gateway_host_start.ensure, root, selection)
                assert runtime_path.read_bytes() == retained_runtime
                assert (await asyncio.to_thread(gateway_host_start.inspect, root, selection))["pid"] == os.getpid()
            with pytest.raises(PlanError, match="requested readiness"):
                await asyncio.to_thread(gateway_host_start.ensure, root, selection)
            assert not (await asyncio.to_thread(gateway_host_start.inspect, root, selection))["selected"]
            host.write_private(listener_path, listener)
            replacement = Fixtures(root / "rotated")
            certificate, key = root / "tls.pem", root / "tls-key.pem"
            original_certificate, original_key = certificate.read_bytes(), key.read_bytes()
            certificate.write_bytes((replacement.root / "tls.pem").read_bytes())
            key.write_bytes((replacement.root / "tls-key.pem").read_bytes())
            with pytest.raises(PlanError, match="requested readiness"):
                await asyncio.to_thread(gateway_host_start.ensure, root, selection)
            certificate.write_bytes(original_certificate)
            key.write_bytes(original_key)
            context = ssl.create_default_context(cafile=str(root / "tls.pem"))
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=context), trust_env=False,
            ) as client:
                async with client.get(status["origin"] + "/_fleet/machine-challenge") as response:
                    assert response.status == 200
                    assert set(await response.json()) == {"nonce", "profile"}
            value = gateway_host_start._record(root)
            response = await asyncio.to_thread(gateway_host_start._call, value, "stop")
            assert response["ready"] is False
            await asyncio.wait_for(task, 5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
