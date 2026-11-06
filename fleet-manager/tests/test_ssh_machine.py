import asyncio
import builtins
from copy import deepcopy
from importlib.resources import files
import json
import hashlib
import os
from pathlib import Path
import signal
import sys
import time
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
    from fleet_manager import continuity_bootstrap, gateway_service_caller

    original = gateway_service_caller.ServiceCaller
    monkeypatch.setattr(continuity_bootstrap, "ServiceCaller", original)
    monkeypatch.setattr(
        gateway_service_caller, "ServiceCaller", lambda **_options: SimpleNamespace(),
    )
    return original


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


def machine_config(root):
    value, policy, directory = configured(root)
    value["version"] = 2
    value.pop("access_token_file")
    value["authentication"] = {
        "profile": "machine-key-v1", "principal": "caller-a",
        "audience": "https://fleet.example.test", "resource": "service-a",
    }
    return value, policy, directory


def continuity_config(root, *, gateway_origin=None, gateway_ca=None, audience=None):
    from fleet_manager import continuity_bootstrap, private_state
    from test_gateway_enrollment import GRANT

    value, policy, directory = machine_config(root)
    Path(value.pop("proof_key_file")).unlink()
    (root / "access").unlink()
    value["version"] = 3
    value["gateway_origin"] = gateway_origin or value["gateway_origin"]
    value["gateway_ca"] = gateway_ca
    value["authentication"]["audience"] = audience or value["authentication"]["audience"]
    source = root / "continuity-source" / "selected.json"
    state = root / "continuity-state"
    private_state.private_directory(source.parent)
    projection = {
        "schema": "fleet-manager.continuity-projection", "version": 1,
        "provider": "local-projection-v1", "authority": "authority-a",
        "audience": value["authentication"]["audience"],
        "delegation": {
            "id": "grant-a", "grant_sha256": hashlib.sha256(GRANT.encode()).hexdigest(),
            "grant_expires_at": int(time.time()) + 1800,
            "scope": {
                "source": "source-a", "account": "account-a", "container": "container-a",
                "harness": "harness-a", "gateway": "gateway-a",
                "destination": value["gateway_origin"], "machine": value["machine"],
                "principal": value["authentication"]["principal"], "generation": 1,
                "slots": [value["authentication"]["resource"]], "methods": ["GET", "POST"],
                "expires_at": int(time.time()) + 3600,
            },
        },
        "grant": GRANT, "gateway_ca": gateway_ca,
    }
    with os.fdopen(private_state.create_private_file(source), "wb") as stream:
        stream.write(canonical(projection))
    retained = continuity_bootstrap._state(state, projection, apply=True)
    value["credential_source"] = {
        "provider": "local-projection-v1", "source": str(source),
        "harness": "harness-a", "state_root": str(state),
    }
    return value, policy, directory, retained["request"]["public_key"]


@pytest.mark.parametrize("change", [
    {"version": 2}, {"proof_key_file": "unused"}, {"access_token_file": "unused"},
    {"credential_source": None}, {"credential_source": {}},
    *({"credential_source": {
        "provider": "local-projection-v1", "source": "unused", "harness": "harness-a",
        "state_root": "unused", **bad,
    }} for bad in (
        {"provider": "arbitrary-module"}, {"source": "relative"}, {"state_root": "relative"},
        {"harness": ""}, {"command": "ignored"},
    )),
])
def test_continuity_config_refuses_mixed_or_invalid_references(tmp_path, monkeypatch, change):
    from fleet_manager import continuity_bootstrap

    value, _, _, _ = continuity_config(tmp_path)
    if isinstance(change.get("credential_source"), dict) and change["credential_source"]:
        selected = dict(change["credential_source"])
        for name in ("source", "state_root"):
            if selected.get(name) == "unused":
                selected[name] = value["credential_source"][name]
        change = change | {"credential_source": selected}
    monkeypatch.setattr(
        continuity_bootstrap, "machine_caller_reference",
        lambda *_args, **_kwargs: pytest.fail("invalid config read the retained identity"),
    )
    with pytest.raises(PlanError):
        ssh_machine.prepare(SSHMachineConfig.from_dict(value | change))
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["config_d"]).exists()


@pytest.mark.parametrize("kind", ["source", "state_root", "lease-in-identity"])
def test_continuity_lease_cannot_own_provider_inputs(tmp_path, kind):
    value, _, _, _ = continuity_config(tmp_path)
    if kind == "lease-in-identity":
        value["state_dir"] = str(Path(value["credential_source"]["state_root"]) / "lease")
    else:
        value["credential_source"][kind] = str(Path(value["state_dir"]) / kind)
    with pytest.raises(PlanError, match="exclude input references|must be separate"):
        SSHMachineConfig.from_dict(value)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("configure", [configured, machine_config, continuity_config])
def test_all_credential_modes_refuse_writer_owned_inputs_before_key_reads(tmp_path, monkeypatch, configure):
    from fleet_manager import credential_references, continuity_bootstrap

    value = configure(tmp_path)[0]
    original = Path(
        value["credential_source"]["source"] if value["version"] == 3 else value["proof_key_file"],
    )
    selected = Path(value["config_d"]) / "retained-input"
    selected.parent.mkdir(parents=True)
    selected.write_bytes(original.read_bytes())
    if value["version"] == 3:
        value["credential_source"]["source"] = str(selected)
    else:
        value["proof_key_file"] = str(selected)
    monkeypatch.setattr(
        credential_references, "read_credential_reference",
        lambda *_args: pytest.fail("writer-owned input was read as a credential"),
    )
    monkeypatch.setattr(
        continuity_bootstrap, "machine_caller_reference",
        lambda *_args, **_kwargs: pytest.fail("writer-owned input reached its provider"),
    )
    before = selected.read_bytes()
    with pytest.raises(ValueError, match="outside local writer scopes"):
        ssh_machine.prepare(SSHMachineConfig.from_dict(value))
    assert selected.read_bytes() == before
    assert not Path(value["state_dir"]).exists()


@pytest.mark.parametrize("missing", ["MACHINE_REFERENCE_CAPABILITY", "machine_caller_reference"])
def test_older_continuity_provider_refuses_before_identity_or_lease(tmp_path, monkeypatch, missing):
    from fleet_manager import continuity_bootstrap

    value, _, _, _ = continuity_config(tmp_path)
    monkeypatch.delattr(continuity_bootstrap, missing)
    monkeypatch.setattr(
        continuity_bootstrap, "_state",
        lambda *_args, **_kwargs: pytest.fail("older provider read the retained identity"),
    )
    with pytest.raises(PlanError, match="lacks the machine-reference-v1 capability"):
        ssh_machine.prepare(SSHMachineConfig.from_dict(value))
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["config_d"]).exists()


@pytest.mark.parametrize("public", [
    b"", b"\xff", b"ssh-rsa invalid", b"ssh-ed25519 invalid", b"x" * 257,
])
def test_malformed_local_public_key_refuses_before_lease_or_network(tmp_path, monkeypatch, public):
    value, _, _ = machine_config(tmp_path)
    Path(value["public_key_file"]).write_bytes(public)
    monkeypatch.setattr(
        SSHDirectoryClient, "watch",
        lambda *_args: pytest.fail("malformed local public key opened the directory watch"),
    )
    with pytest.raises((ValueError, UnicodeError)):
        asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value)))
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["config_d"]).exists()


@pytest.mark.parametrize("failure", ["file", "unowned", "lease-directory", "missing-parent"])
def test_existing_consumer_state_preflight_refuses_before_network_or_writes(tmp_path, failure):
    from fleet_manager.private_state import create_private_file, private_directory
    import os

    value, _, _ = machine_config(tmp_path)
    root = Path(value["state_dir"])
    if failure == "missing-parent":
        value["state_dir"] = str(root / "missing-parent" / "state")
    elif failure == "file":
        with os.fdopen(create_private_file(root), "wb") as stream:
            stream.write(b"retained owner state")
    else:
        private_directory(root)
        if failure == "unowned":
            (root / "retained").write_bytes(b"retained owner state")
        else:
            (root / ssh_machine.LEASE).mkdir()
    before = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    with pytest.raises(PlanError):
        ssh_machine.prepare(SSHMachineConfig.from_dict(value))
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not Path(value["config_d"]).exists()
    if failure == "missing-parent":
        assert not root.exists()


@pytest.mark.parametrize("reference", [
    "policy_path", "identity_file", "public_key_file", "proof_key_file",
])
def test_lease_cannot_overwrite_an_input_reference(tmp_path, reference):
    from fleet_manager.private_state import create_private_file, private_directory
    import os

    value, _, _ = machine_config(tmp_path)
    state = Path(value["state_dir"])
    private_directory(state)
    retained = Path(value[reference]).read_bytes()
    path = state / ssh_machine.LEASE
    with os.fdopen(create_private_file(path), "wb") as stream:
        stream.write(retained)
    value[reference] = str(path)
    with pytest.raises(PlanError, match="exclude input references"):
        ssh_machine.prepare(SSHMachineConfig.from_dict(value))
    assert path.read_bytes() == retained
    assert not Path(value["config_d"]).exists()


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


@pytest.mark.parametrize("change", [
    {"access_token_file": "unused"},
    {"version": 1},
    {"authentication": None},
    {"authentication": {}},
    {"authentication": {"profile": "machine-key-v1"}},
    *({"authentication": {
        "profile": "machine-key-v1", "principal": "caller-a",
        "audience": "https://fleet.example.test", "resource": "service-a", **bad,
    }} for bad in (
        {"profile": "token"}, {"principal": ""}, {"resource": []},
        {"audience": ""}, {"audience": "with spaces"}, {"audience": "x" * 513},
        {"token": "unused"},
    )),
])
def test_machine_config_refuses_mixed_incomplete_or_invalid_authentication_before_effects(
    tmp_path, monkeypatch, change,
):
    from fleet_manager import credential_references

    value, _, _ = machine_config(tmp_path)
    monkeypatch.setattr(
        credential_references, "read_credential_reference",
        lambda _path: pytest.fail("invalid config read credentials"),
    )
    with pytest.raises(PlanError):
        asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value | change)))
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "ssh").exists()


@pytest.mark.parametrize("failure", ["missing", "malformed", "public", "curve", "rsa", "jwk"])
def test_machine_key_refuses_before_lease_or_profile_and_never_loads_tokens(
    tmp_path, monkeypatch, failure,
):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fleet_manager import credential_references
    from fleet_manager.gateway_contract import GatewayError

    value, _, _ = machine_config(tmp_path)
    path = Path(value["proof_key_file"])
    if failure == "missing":
        path.unlink()
    elif failure == "malformed":
        path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nsynthetic-invalid")
    elif failure == "public":
        path.write_bytes(ECKey.generate_key("P-256").get_op_key("verify").public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    elif failure == "jwk":
        path.write_bytes(canonical(ECKey.generate_key("P-256").as_dict(private=True)))
    else:
        key = (
            ECKey.generate_key("P-384").get_op_key("sign") if failure == "curve"
            else rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
        path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    monkeypatch.setattr(
        credential_references, "load_service_credentials",
        lambda **_options: pytest.fail("machine mode fell back to tokens"),
    )
    with pytest.raises(GatewayError, match="credential-(key|reference)-unavailable"):
        asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value)))
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "ssh").exists()


def test_v1_keeps_external_pem_and_fresh_token_callback(tmp_path, monkeypatch, bounded_caller):
    from fleet_manager import gateway_service_caller

    value, _, _ = configured(tmp_path)
    observed = []
    monkeypatch.setattr(gateway_service_caller, "ServiceCaller", bounded_caller)

    async def watch(client, stop, _accept):
        assert not client.caller._machine
        assert not isinstance(client.caller._key, ECKey)
        observed.append(await client.caller._access())
        Path(value["access_token_file"]).write_text("synthetic-rotated", encoding="ascii")
        observed.append(await client.caller._access())
        stop.set()

    monkeypatch.setattr(SSHDirectoryClient, "watch", watch)
    asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value)))
    assert observed == ["synthetic-access", "synthetic-rotated"]


@pytest.mark.parametrize("credential_mode", ["pem", "continuity"])
def test_machine_consumer_real_tls_proofs_and_local_writer(
    tmp_path, monkeypatch, bounded_caller, credential_mode,
):
    from aiohttp import web
    from fleet_manager import credential_references, gateway_service_caller
    from fleet_manager.gateway_service_connector import ServiceConnector
    from fleet_manager.machine_identity import MachineIdentityVerifier, PROFILE
    from gateway_service_transport_fixtures import Rig
    from test_runtime_identity import AUDIENCE

    value, policy, directory = machine_config(tmp_path)
    gateway_root = tmp_path / "gateway"
    gateway_root.mkdir()
    rig = Rig(gateway_root)
    rig.h.document["version"] = 4
    rig.h.document["identity"] = {
        "profile": PROFILE, "audience": AUDIENCE,
        "trust_valid_until": rig.h.clock.value + 86400,
    }
    rig.h.document["enrollment"] = {"mappings": [
        {"principal": principal, "kind": kind, "resource": "service-a",
         "key_source": principal, "expires_at": rig.h.clock.value + 600}
        for principal, kind in (("service-a", "service"), ("caller-a", "caller"))
    ]}
    value["authentication"]["audience"] = AUDIENCE
    value["namespace"] = "/api"
    value["gateway_ca"] = rig.ca
    if credential_mode == "pem":
        Path(value["proof_key_file"]).write_bytes(rig.h.keys[3].get_op_key("sign").private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
    Path(tmp_path / "access").unlink()
    monkeypatch.setattr(
        credential_references, "load_service_credentials",
        lambda **_options: pytest.fail("machine mode acquired a token source"),
    )
    callers, verified, received, applied = [], [], [], []

    def complete(revision):
        assert verified and received
        assert key(2) in (tmp_path / "authorization" / "authorized_keys.gateway").read_text()
        applied.append(revision)

    def compose(**options):
        caller = bounded_caller(**options)
        callers.append(caller)
        return caller

    if credential_mode == "pem":
        monkeypatch.setattr(gateway_service_caller, "ServiceCaller", compose)
    else:
        from fleet_manager import continuity_bootstrap

        monkeypatch.setattr(gateway_service_caller, "ServiceCaller", bounded_caller)
        reference = continuity_bootstrap.machine_caller_reference

        def compose_reference(*args, **kwargs):
            prepared = reference(*args, **kwargs)
            callers.append(prepared.caller)
            return prepared

        monkeypatch.setattr(continuity_bootstrap, "machine_caller_reference", compose_reference)
    verify = MachineIdentityVerifier.verify

    def observe(verifier, principal, proof, **context):
        identity = verify(verifier, principal, proof, **context)
        if identity.kind == "caller":
            verified.append((identity, context))
        return identity

    monkeypatch.setattr(MachineIdentityVerifier, "verify", observe)

    async def handle(request):
        body = await request.read()
        received.append((request.method, request.raw_path, body))
        if request.path == "/directory":
            if request.query["after"] == "0":
                return web.Response(body=update(policy, directory).encoded)
            await asyncio.Event().wait()
        return web.Response(body=body)

    rig.backends[0].handle = handle

    async def exercise():
        stop, owner = asyncio.Event(), None
        try:
            await rig.start()
            value["gateway_origin"] = rig.origin
            if credential_mode == "continuity":
                selected, _, _, public = continuity_config(
                    tmp_path, gateway_origin=rig.origin, gateway_ca=rig.ca, audience=AUDIENCE,
                )
                selected["namespace"] = "/api"
                value.clear()
                value.update(selected)
                binding = next(
                    item for item in rig.h.document["bindings"] if item["reference"] == "keys/caller-a"
                )
                binding["jwk"] = public
                rig.h.document["manifest"]["revision"] += 1
                rig.save()
                await rig.state.reload()
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin, backend_origin=rig.origins[0],
                proof_key=rig.h.keys[1], gateway_ca=rig.ca,
                machine_principal="service-a", machine_audience=AUDIENCE,
            )
            task = asyncio.create_task(connector.connect_once())
            rig.tasks.append(task)
            await asyncio.wait_for(connector.connected.wait(), 3)
            config = SSHMachineConfig.from_dict(value)
            owner = asyncio.create_task(ssh_machine.run(config, stop=stop, on_applied=complete))
            await reached(lambda: applied == [directory["revision"]], owner)
            assert key(2) in (tmp_path / "authorization" / "authorized_keys.gateway").read_text()
            identity, context = verified[0]
            assert identity.subject == "caller-a" and identity.audience == AUDIENCE
            assert context == {
                "method": "GET", "target_uri": rig.origin + "/api/directory?after=0&wait=25",
                "kind": "caller", "resource": "service-a",
                "content_digest": hashlib.sha256(b"").hexdigest(),
            }
            assert received[0] == ("GET", "/directory?after=0&wait=25", b"")
            caller = callers[0]
            assert isinstance(caller._key, ECKey) and caller._access is None
            response = await caller.request(
                "POST", "/api/write?opaque=%2f", body=b"synthetic-intent",
            )
            assert response.status == 200 and response.body == b"synthetic-intent"
            assert [context for _identity, context in verified if context["method"] == "POST"] == [{
                "method": "POST", "target_uri": rig.origin + "/api/write?opaque=%2f",
                "kind": "caller", "resource": "service-a",
                "content_digest": hashlib.sha256(b"synthetic-intent").hexdigest(),
            }]
            assert [item for item in received if item[0] == "POST"] == [
                ("POST", "/write?opaque=%2f", b"synthetic-intent"),
            ]
        finally:
            stop.set()
            try:
                if owner is not None:
                    await asyncio.wait_for(owner, 5)
            finally:
                await rig.close()
        lease = ssh_machine._lease(tmp_path / "state")
        lease.acquire()
        lease.release()

    asyncio.run(exercise())


@pytest.mark.parametrize("field", ["config_d", "ssh_config", "authorization_dir"])
@pytest.mark.parametrize("suffix", ["", "nested/output", "nested/../output"])
@pytest.mark.parametrize("configure", [configured, machine_config])
def test_output_scope_cannot_populate_the_lease_only_state_tree(tmp_path, field, suffix, configure):
    value, _, _ = configure(tmp_path)
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
@pytest.mark.parametrize("configure", [configured, machine_config])
def test_writer_derived_scope_is_checked_before_credentials_or_lease(tmp_path, scope, configure):
    from agent_ssh import gateway_profile, ssh_profile

    value, _, _ = configure(tmp_path)
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


@pytest.mark.parametrize("legacy_state", [False, True])
def test_older_local_adapter_refuses_before_credentials_or_state(tmp_path, monkeypatch, capsys, legacy_state):
    value, _, _ = configured(tmp_path)
    value["proof_key_file"] = str(tmp_path / "missing-proof")
    path = tmp_path / "client.json"
    path.write_bytes(canonical(value))
    adapter = SimpleNamespace(
        apply_update=lambda *_a, **_kw: pytest.fail("old adapter was invoked"),
    )
    if legacy_state:
        adapter.validate_consumer_state = lambda *_a, **_kw: pytest.fail("partial adapter was invoked")
    monkeypatch.setitem(sys.modules, "agent_ssh.gateway_directory", adapter)
    assert ssh_machine.serve(path) == 1
    assert "requires the ssh-client extra" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("change", ["policy", "controller", "authorization"])
@pytest.mark.parametrize("configure", [configured, machine_config])
def test_local_policy_is_checked_before_state_or_credentials(tmp_path, monkeypatch, change, configure):
    value, _, _ = configure(tmp_path)
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
        applied = []

        def complete(revision):
            assert authorized.exists()
            if revision == 2:
                assert key(2) in authorized.read_text()
                assert any("Host worker-b" in path.read_text()
                           for path in (tmp_path / "ssh" / "config.d").glob("*.conf"))
            applied.append(revision)

        async def wait(_client, after, *, seconds=25):
            positions.append(after)
            return await incoming.get()

        monkeypatch.setattr(SSHDirectoryClient, "wait", wait)
        stop = asyncio.Event()
        owner = asyncio.create_task(ssh_machine.run(
            SSHMachineConfig.from_dict(value), stop=stop, on_applied=complete,
        ))
        try:
            await reached(lambda: positions == [0], owner)
            assert not applied
            await incoming.put(update(policy, first))
            await reached(authorized.exists, owner)
            assert key(2) not in authorized.read_text()
            await incoming.put(update(policy, full))
            await reached(lambda: key(2) in authorized.read_text(), owner)
            assert positions[:2] == [0, 1]
            assert applied == [1, 2]
            assert any("Host worker-b" in path.read_text()
                       for path in (tmp_path / "ssh" / "config.d").glob("*.conf"))
        finally:
            stop.set()
            await asyncio.wait_for(owner, 5)
        before = authorized.read_bytes(), authorized.stat().st_mtime_ns
        stop = asyncio.Event()
        positions.clear()
        owner = asyncio.create_task(ssh_machine.run(
            SSHMachineConfig.from_dict(value), stop=stop, on_applied=complete,
        ))
        try:
            await incoming.put(update(policy, full))
            await reached(lambda: positions == [0, 2], owner)
            assert (authorized.read_bytes(), authorized.stat().st_mtime_ns) == before
            assert applied == [1, 2, 2]
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
            await ssh_machine.run(
                SSHMachineConfig.from_dict(value),
                on_applied=lambda _revision: pytest.fail("failed local write reported readiness"),
            )
        lease = ssh_machine._lease(tmp_path / "state")
        lease.acquire()
        lease.release()
    asyncio.run(exercise())


def test_failed_completion_observer_settles_watch_without_advancing_notification(tmp_path, monkeypatch):
    value, policy, directory = configured(tmp_path)
    positions = []

    async def wait(_client, after, *, seconds=25):
        positions.append(after)
        return update(policy, directory)

    def failed(revision):
        assert revision == directory["revision"]
        assert (tmp_path / "authorization" / "authorized_keys.gateway").exists()
        raise RuntimeError("selected-host-publication-failed")

    monkeypatch.setattr(SSHDirectoryClient, "wait", wait)
    with pytest.raises(RuntimeError, match="selected-host-publication-failed"):
        asyncio.run(ssh_machine.run(SSHMachineConfig.from_dict(value), on_applied=failed))
    assert positions == [0]
    lease = ssh_machine._lease(tmp_path / "state")
    lease.acquire()
    lease.release()


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
        "configuration": {"schema": "fleet-manager.ssh-client", "versions": [1, 2, 3]},
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
