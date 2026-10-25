import asyncio
import builtins
from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from joserfc.jwk import ECKey
import pytest
from single_instance_lease import AlreadyRunningError

from fleet_manager import __main__ as cli, ssh_machine
from fleet_manager.planning import PlanError, canonical
from fleet_manager.ssh_directory import DirectoryUpdate, SSHDirectoryClient
from fleet_manager.ssh_machine_config import SSHMachineConfig
from test_gateway_ssh_provider import fixtures, key


@pytest.fixture(autouse=True)
def bounded_caller(monkeypatch):
    from fleet_manager import gateway_service_caller

    monkeypatch.setattr(
        gateway_service_caller, "ServiceCaller", lambda **_options: SimpleNamespace(),
    )


def configured(root):
    policy, directory = fixtures()
    (root / "policy.json").write_bytes(canonical(policy.to_dict()))
    (root / "identity.pub").write_text(key(1), encoding="ascii")
    (root / "identity").write_text("fixture reference; never used for login", encoding="ascii")
    (root / "proof.pem").write_bytes(ECKey.generate_key("P-256").get_op_key("sign").private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    (root / "access").write_text("synthetic-access", encoding="ascii")
    value = {
        "schema": "fleet-manager.ssh-client", "version": 1,
        "state_dir": str(root / "state"), "gateway_origin": "https://gateway.example.test",
        "gateway_ca": None, "namespace": "/ssh",
        "proof_key_file": str(root / "proof.pem"), "access_token_file": str(root / "access"),
        "policy_path": str(root / "policy.json"), "machine": "worker-a", "environment": "linux",
        "gateway": "selected-gateway", "controller": "gateway-a",
        "identity_file": str(root / "identity"), "public_key_file": str(root / "identity.pub"),
        "authorization_dir": str(root / "authorization"),
        "config_d": str(root / "ssh" / "config.d"), "ssh_config": str(root / "ssh" / "config"),
    }
    return value, policy, directory


def update(policy, directory):
    return DirectoryUpdate.parse(canonical({
        "schema": "agent-ssh.service-directory", "version": 1,
        "policy": policy.to_dict(), "directory": directory,
    }))


async def reached(predicate, task):
    async def wait():
        while not predicate():
            if task.done():
                await task
                pytest.fail("SSH machine ended before the expected local effect")
            await asyncio.sleep(0.01)
    # This milestone includes the real writers' native Windows ACL helpers.
    await asyncio.wait_for(wait(), 30)


@pytest.mark.parametrize("field,bad", [
    ("version", True), ("environment", "other"), ("environment", []),
    ("machine", ""), ("namespace", "/"), ("namespace", "/ssh/"),
    ("namespace", "/ssh?other=1"), ("gateway_origin", "http://gateway.example.test"),
    ("state_dir", "relative"), ("policy_path", "relative"), ("identity_file", None),
    ("authorization_dir", "relative"), ("ssh_config", "relative"),
])
def test_strict_config_rejects_invalid_scope_before_effects(tmp_path, field, bad):
    value, _, _ = configured(tmp_path)
    value[field] = bad
    with pytest.raises((ValueError, TypeError)):
        SSHMachineConfig.from_dict(value)
    assert not (tmp_path / "state").exists()


def test_config_does_not_adopt_unknown_fields_or_missing_scope(tmp_path):
    value, _, _ = configured(tmp_path)
    with pytest.raises(PlanError):
        SSHMachineConfig.from_dict(value | {"command": ["arbitrary-executable"]})
    value.pop("config_d")
    with pytest.raises(PlanError):
        SSHMachineConfig.from_dict(value)


@pytest.mark.parametrize("field", ["config_d", "ssh_config", "authorization_dir"])
@pytest.mark.parametrize("suffix", ["", "nested/output", "nested/../output"])
def test_output_scope_cannot_populate_the_lease_only_state_tree(tmp_path, field, suffix):
    value, _, _ = configured(tmp_path)
    value[field] = str(tmp_path / "state" / suffix)
    with pytest.raises(PlanError, match="dedicated state directory"):
        SSHMachineConfig.from_dict(value)
    assert not (tmp_path / "state").exists()


def test_output_scope_allows_a_distinct_sibling_with_the_same_prefix(tmp_path):
    value, _, _ = configured(tmp_path)
    value["authorization_dir"] = str(tmp_path / "state-authorization")
    SSHMachineConfig.from_dict(value)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("scope", [
    "sources", "fragment-locks", "root-locks", "inside-sources",
    "inside-locks", "inside-fragments", "inside-authorization",
])
def test_writer_derived_scope_is_checked_before_credentials_or_lease(tmp_path, scope):
    from agent_ssh import gateway_profile, ssh_profile

    value, _, _ = configured(tmp_path)
    value["ssh_config"] = str(tmp_path / "root-ssh" / "config")
    destinations = {
        "sources": tmp_path / "ssh" / gateway_profile._SOURCE_DIR,
        "fragment-locks": tmp_path / "ssh" / ssh_profile._LOCK_DIR_NAME,
        "root-locks": tmp_path / "root-ssh" / ssh_profile._LOCK_DIR_NAME,
        "inside-sources": tmp_path / "ssh" / gateway_profile._SOURCE_DIR / "nested",
        "inside-locks": tmp_path / "ssh" / ssh_profile._LOCK_DIR_NAME / "nested",
        "inside-fragments": Path(value["config_d"]) / "nested",
        "inside-authorization": Path(value["authorization_dir"]) / "nested",
    }
    value["state_dir"] = str(destinations[scope])
    value["proof_key_file"] = str(tmp_path / "missing-proof")
    with pytest.raises(ValueError, match="dedicated state directory outside local writer scopes"):
        asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value)))
    assert not Path(value["state_dir"]).exists()


def test_older_local_adapter_refuses_before_credentials_or_state(tmp_path, monkeypatch, capsys):
    value, _, _ = configured(tmp_path)
    path = tmp_path / "client.json"
    path.write_bytes(canonical(value))
    monkeypatch.setitem(
        sys.modules, "agent_ssh.gateway_directory",
        SimpleNamespace(apply_update=lambda *_a, **_kw: pytest.fail("old adapter was invoked")),
    )
    assert ssh_machine.serve(path) == 1
    assert "requires the ssh-client extra" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("change", ["policy", "controller", "authorization"])
def test_local_policy_is_checked_before_state_or_credentials(tmp_path, monkeypatch, change):
    value, _, _ = configured(tmp_path)
    if change == "policy":
        document = json.loads((tmp_path / "policy.json").read_text())
        document["machines"][0]["role"] = "offline"
        (tmp_path / "policy.json").write_text(json.dumps(document))
    elif change == "controller":
        value["controller"] = "gateway-b"
    else:
        value["authorization_dir"] = None
    value["proof_key_file"] = str(tmp_path / "missing-proof")
    with pytest.raises(PlanError):
        asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value)))
    assert not (tmp_path / "state").exists()


def test_real_local_owners_apply_later_join_and_restart_catch_up(tmp_path, monkeypatch):
    value, policy, full = configured(tmp_path)
    first = deepcopy(full)
    first["machines"].pop()
    full["revision"] = 2
    authorized = tmp_path / "authorization" / "authorized_keys.gateway"

    async def exercise():
        incoming, positions = asyncio.Queue(), []

        async def wait(_client, after, *, seconds=25):
            positions.append(after)
            return await incoming.get()

        monkeypatch.setattr(SSHDirectoryClient, "wait", wait)
        stop = asyncio.Event()
        owner = asyncio.create_task(ssh_machine.run(SSHMachineConfig.from_dict(value), stop=stop))
        try:
            await incoming.put(update(policy, first))
            await reached(authorized.exists, owner)
            assert key(2) not in authorized.read_text()
            await incoming.put(update(policy, full))
            await reached(lambda: key(2) in authorized.read_text(), owner)
            assert positions[:2] == [0, 1]
            assert any("Host worker-b" in path.read_text()
                       for path in (tmp_path / "ssh" / "config.d").glob("*.conf"))
        finally:
            stop.set()
            await asyncio.wait_for(owner, 5)
        before = authorized.read_bytes(), authorized.stat().st_mtime_ns
        stop = asyncio.Event()
        positions.clear()
        owner = asyncio.create_task(ssh_machine.run(SSHMachineConfig.from_dict(value), stop=stop))
        try:
            await incoming.put(update(policy, full))
            await reached(lambda: positions == [0, 2], owner)
            assert (authorized.read_bytes(), authorized.stat().st_mtime_ns) == before
        finally:
            stop.set()
            await asyncio.wait_for(owner, 5)
    asyncio.run(exercise())


def test_local_owner_failure_is_not_hidden_or_advanced(tmp_path, monkeypatch):
    value, policy, directory = configured(tmp_path)

    async def exercise():
        async def wait(_client, after, *, seconds=25):
            assert after == 0
            return update(policy, directory)

        def denied(_update):
            raise PermissionError("selected-local-owner-denied")

        monkeypatch.setattr(SSHDirectoryClient, "wait", wait)
        monkeypatch.setattr(ssh_machine, "_consumer", lambda _config: denied)
        with pytest.raises(PermissionError, match="selected-local-owner-denied"):
            await ssh_machine.run(SSHMachineConfig.from_dict(value))
        lease = ssh_machine._lease(tmp_path / "state")
        lease.acquire()
        lease.release()
    asyncio.run(exercise())


def test_repeated_cancel_retains_lease_and_signal_handlers_until_watch_settles(
    tmp_path, monkeypatch,
):
    value, _, _ = configured(tmp_path)
    original = {selected: signal.getsignal(selected) for selected in (signal.SIGINT, signal.SIGTERM)}

    async def exercise():
        previous = {selected: signal.getsignal(selected) for selected in original}
        started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def watch(_client, stop, accept):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish.wait()

        monkeypatch.setattr(SSHDirectoryClient, "watch", watch)
        owner = asyncio.create_task(ssh_machine.run(
            SSHMachineConfig.from_dict(value), install_signals=True,
        ))
        try:
            await asyncio.wait_for(started.wait(), 5)
            owner.cancel()
            await asyncio.wait_for(cleaning.wait(), 5)
            for _ in range(3):
                owner.cancel()
                await asyncio.sleep(0)
                assert not owner.done()
            contender = ssh_machine._lease(tmp_path / "state")
            with pytest.raises(AlreadyRunningError):
                contender.acquire()
            assert all(signal.getsignal(selected) != handler for selected, handler in previous.items())
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
        assert all(signal.getsignal(selected) == handler for selected, handler in previous.items())
        contender = ssh_machine._lease(tmp_path / "state")
        contender.acquire()
        contender.release()
    asyncio.run(exercise())
    assert all(signal.getsignal(selected) == handler for selected, handler in original.items())


def test_already_stopped_owner_is_inert(tmp_path):
    value, _, _ = configured(tmp_path)

    async def exercise():
        stop = asyncio.Event()
        stop.set()
        await ssh_machine.run(SSHMachineConfig.from_dict(value), stop=stop)
    asyncio.run(exercise())
    assert not (tmp_path / "state").exists()


def test_foreground_role_uses_only_its_selected_config(tmp_path, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(ssh_machine, "serve", lambda path: called.append(path) or 0)
    assert cli.main(["serve", "--role", "ssh-client", "--config", str(tmp_path / "config")]) == 0
    assert called == [tmp_path / "config"]
    assert cli.main(["serve", "--role", "ssh-client", "--service-policy", "unused"]) == 1
    assert cli.main(["serve", "--role", "ssh-client", "--config", "unused",
                     "--tls-config", "unused"]) == 1
    assert len(called) == 1 and "requires only --config" in capsys.readouterr().err


def test_selected_runtime_declares_its_optional_contract_without_hosting():
    descriptor = json.loads(files("fleet_manager").joinpath(
        "ssh-client-contract.json",
    ).read_text("utf-8"))
    assert descriptor == {
        "schema": "fleet-manager.ssh-client-contract", "version": 1,
        "configuration": {"schema": "fleet-manager.ssh-client", "versions": [1]},
        "directory": {"schema": "agent-ssh.service-directory", "versions": [1]},
        "entrypoint": {"command": "serve", "role": "ssh-client"},
        "automatic_invocation": False,
    }


def test_missing_optional_host_dependency_is_an_explicit_failure(tmp_path, monkeypatch, capsys):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "gateway_service_channel":
            raise ImportError("fixture lacks optional hosting extra")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    assert ssh_machine.serve(tmp_path / "not-read") == 1
    assert "requires the ssh-client extra" in capsys.readouterr().err
