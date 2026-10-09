"""The final-context authority is opt-in, launch-bound, and independently read."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge import preference_attestation as a
from agent_bridge.container_preference_launch import (
    bind_launch_purpose, container_child_argv, verify_launch_receipt,
)
from agent_bridge.session_host.preference_spawn import spawn_attested
from agent_bridge.session_preferences import validate_receipt
from test_target_preferences import calls, client, options


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch):
    for key in (
        "AGENT_BRIDGE_ACP_MODEL", "AGENT_CODESPACES_ACP_MODEL",
        "AGENT_BRIDGE_ACP_EFFORT", "AGENT_CODESPACES_ACP_EFFORT",
        "AGENT_BRIDGE_ACP_CONTEXT", "AGENT_CODESPACES_ACP_CONTEXT",
        "AGENT_BRIDGE_MODEL_PROPAGATE", "AGENT_CODESPACES_MODEL_PROPAGATE",
        "COPILOT_PROVIDER_BASE_URL", "COPILOT_MODEL", "COPILOT_OFFLINE",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def sealed(monkeypatch):
    monkeypatch.setattr(a, "process_start", lambda pid: "99")
    expected = {
        "platform": "linux", "uid": 1000, "namespace": "a" * 64,
        "cwd": "c" * 64, "digest": a.component_digest(), "nonce": "secret", "mode": "defaults",
        "target": "e" * 64,
    }
    receipt = {
        "version": 2, "child_pid": 321, "status": "resolved",
        "values": {"model": "target", "reasoning_effort": "medium", "context": "long_context"},
        "sources": dict.fromkeys(("model", "reasoning_effort", "context"), "target-settings"),
        "authority": {
            "kind": "wrapper-v1", "digest": expected["digest"], "mode": "defaults", "start": "99",
            "target": expected["target"],
            "space": {key: expected[key] for key in ("platform", "uid", "namespace", "cwd")},
        },
    }
    receipt["authority"]["space"]["home"] = "b" * 64
    return expected, {"proof": {"nonce": "secret"}, "receipt": receipt}


def test_sealed_receipt_reaches_frontend_without_binding_secret(sealed):
    expected, frame = sealed
    receipt = a.verify_frame(frame, expected, 321)
    assert validate_receipt(receipt, 321) == receipt
    assert "secret" not in json.dumps(receipt)
    assert validate_receipt(receipt, 322) is None


@pytest.mark.parametrize("path,value", [
    (("proof", "nonce"), "other"),
    (("receipt", "version"), 1),
    (("receipt", "child_pid"), 322),
    (("receipt", "authority", "digest"), "d" * 64),
    (("receipt", "authority", "mode"), "selection"),
    (("receipt", "authority", "start"), "100"),
    (("receipt", "authority", "target"), "f" * 64),
    (("receipt", "authority", "space", "uid"), 1001),
    (("receipt", "authority", "space", "namespace"), "d" * 64),
    (("receipt", "authority", "space", "cwd"), "d" * 64),
])
def test_mismatched_launch_or_execution_space_refuses(sealed, path, value):
    expected, frame = sealed
    cursor = frame
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValueError, match="mismatch"):
        a.verify_frame(frame, expected, 321)


@pytest.mark.parametrize("status", ["missing", "error"])
def test_missing_defaults_fail_fresh_but_not_confirmed_selection(sealed, status):
    expected, frame = sealed
    frame["receipt"].update(status=status, values={}, sources={})
    with pytest.raises(ValueError, match="missing or unreadable"):
        a.verify_frame(frame, expected, 321)
    expected["mode"] = frame["receipt"]["authority"]["mode"] = "selection"
    assert a.verify_frame(frame, expected, 321)["status"] == status


def test_provider_native_model_is_not_an_unrelated_default(sealed):
    expected, frame = sealed
    receipt = frame["receipt"]
    receipt["provider_selected"] = True
    receipt["values"].pop("model")
    receipt["sources"].pop("model")
    assert a.verify_frame(frame, expected, 321)["provider_selected"]


@pytest.mark.parametrize("value", ["", "wrong", True, None])
def test_invalid_space_shape_is_not_frontend_authority(sealed, value):
    _, frame = sealed
    frame["receipt"]["authority"]["space"]["namespace"] = value
    assert validate_receipt(frame["receipt"], 321) is None


def test_wrapper_requires_provider_capability_and_preserves_prefix():
    command = f"export AUTH_MODE=relay; cd /workspace; {a.LAUNCH_TOKEN} copilot --acp --stdio"
    prepared = {
        "acp_command": command, "remote_env": "/workspace/auth input",
        "preference_wrapper": {"version": 1, "launcher": a.LAUNCH_TOKEN},
        "name": "example", "user": "runner", "execution_instance": "instance",
    }
    target = {"name": "example", "user": "runner"}
    argv = container_child_argv(target, prepared, ["/workspace/plugin"], copilot_args=["--model", "local"])
    assert argv[0] == a.SHELL_MARKER
    assert argv[2:4] == ["bash", "-lc"]
    assert argv[-1].startswith(". '/workspace/auth input'; rm -f '/workspace/auth input'; ")
    assert command in argv[-1]
    assert argv[-1].endswith(" --plugin-dir=/workspace/plugin --model local")
    assert bind_launch_purpose(argv, "target-settings", preserving=False)[1] == "defaults"
    assert bind_launch_purpose(argv, "target-settings", preserving=True)[1] == "selection"
    assert bind_launch_purpose(argv, "caller-settings", preserving=False)[1] == "selection"
    prepared.pop("preference_wrapper")
    with pytest.raises(ValueError, match="capability"):
        container_child_argv(target, prepared, [])


def test_caller_arguments_cannot_enable_authority():
    argv = container_child_argv(
        {"acp_command": "copilot --acp"}, {}, [],
        copilot_args=["--model", "explicit", a.LAUNCH_TOKEN],
    )
    assert argv[:2] == ["bash", "-lc"]


def test_boolean_provider_version_is_not_a_capability():
    with pytest.raises(ValueError, match="capability"):
        container_child_argv(
            {"acp_command": f"{a.LAUNCH_TOKEN} copilot"},
            {"preference_wrapper": {"version": True, "launcher": a.LAUNCH_TOKEN}}, [],
        )


@pytest.mark.parametrize("name", ["AGENT_BRIDGE_ACP_MODEL", "AGENT_CODESPACES_ACP_MODEL"])
def test_explicit_compatibility_model_does_not_require_inherited_defaults(monkeypatch, name):
    monkeypatch.setenv(name, "explicit")
    argv = [a.SHELL_MARKER, "descriptor", "bash", "-lc", "command"]
    assert bind_launch_purpose(argv, "target-settings", preserving=False)[1] == "selection"


@pytest.mark.parametrize("command", [
    f"{a.LAUNCH_TOKEN} copilot; {a.LAUNCH_TOKEN} copilot", "copilot --acp",
])
def test_capability_requires_exact_final_token(command):
    with pytest.raises(ValueError, match="exactly one"):
        container_child_argv({}, {
            "acp_command": command,
            "preference_wrapper": {"version": 1, "launcher": a.LAUNCH_TOKEN},
        }, [])


def test_prepare_launch_uses_own_binding_and_fixed_isolated_component(monkeypatch, tmp_path):
    bundle = tmp_path / "session-host-test.pyz"
    bundle.write_bytes(b"fixture")
    monkeypatch.setattr(sys, "argv", [str(bundle)])
    monkeypatch.setattr(a, "namespace_context", lambda: {
        "platform": "linux", "uid": 1000, "namespace": "a" * 64,
    })
    monkeypatch.setattr(a, "user_uid", lambda user: 1000)
    descriptor = json.dumps({"user": "runner", "instance": "instance"})
    args = [a.SHELL_MARKER, "defaults", descriptor, "bash", "-lc",
            f"export PROXY=x; {a.LAUNCH_TOKEN} copilot"]
    argv, env, expected, reader, writer = a.prepare_launch(
        args, {a.NONCE_ENV: "caller", a.FD_ENV: "invalid", "PROXY": "preserved"}, str(tmp_path),
    )
    try:
        assert argv[:2] == ["bash", "-lc"]
        assert "export PROXY=x; exec " in argv[-1]
        assert "-I -S" in argv[-1]
        assert env["PROXY"] == "preserved"
        assert env[a.NONCE_ENV] != "caller"
        assert env[a.NONCE_ENV] == expected["nonce"]
        assert int(env[a.FD_ENV]) == writer.fileno()
    finally:
        reader.close()
        writer.close()


@pytest.mark.asyncio
async def test_rejected_frame_reaps_owned_child_without_launch_success(monkeypatch):
    reader = SimpleNamespace(
        settimeout=lambda timeout: None, close=lambda: None,
        shutdown=lambda how: None,
        sendall=lambda data: pytest.fail("a rejected child must not receive exec consent"),
    )
    writer = SimpleNamespace(close=lambda: None, fileno=lambda: 10)
    monkeypatch.setattr(
        "agent_bridge.session_host.preference_spawn.prepare_launch",
        lambda *args: (["bash"], {}, {}, reader, writer),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.preference_spawn.read_frame",
        lambda *args: {"receipt": {}},
    )
    child = SimpleNamespace(pid=321, returncode=None, terminate=lambda: None, wait=AsyncMock())
    spawn = AsyncMock(return_value=child)
    with pytest.raises(ValueError, match="binding"):
        await spawn_attested([], "/workspace", {}, spawn)
    child.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_attested_defaults_are_verified_not_caller_defaults(sealed, monkeypatch, tmp_path):
    prefs = tmp_path / ".copilot" / "settings.json"
    prefs.parent.mkdir()
    prefs.write_text(json.dumps({"model": "caller", "effortLevel": "high"}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _, frame = sealed
    value, events = client(target_preferences=frame["receipt"])
    await value._apply_model_config(options())
    assert calls(value) == {"model": "target", "reasoning_effort": "medium"}
    application = next(data for kind, data in events if kind == "preference_application")
    assert application["effective"]["model"] == "target"
    assert any(item["config"] in {"context", "context_tier"} for item in application["context_failures"])


@pytest.mark.asyncio
async def test_explicit_and_resume_choices_precede_new_attested_defaults(sealed):
    _, frame = sealed
    value, _ = client(target_preferences=frame["receipt"], model_override="requested", effort_override="high")
    await value._apply_model_config(options(context=True))
    assert calls(value)["model"] == "requested"
    assert calls(value)["reasoning_effort"] == "high"
    value, _ = client(target_preferences=frame["receipt"], confirmed_preferences={"model": "saved"})
    await value._apply_model_config(options(), resuming=True)
    assert calls(value) == {"model": "saved"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native inherited-FD/exec proof is Linux-only")
@pytest.mark.guard
@pytest.mark.asyncio
@pytest.mark.parametrize("mode,settings", [
    ("defaults", True), ("defaults", False), ("selection", False),
])
async def test_real_bundle_final_context_exec_without_native_model(monkeypatch, tmp_path, mode, settings):
    from agent_bridge.session_host.bundle import build_session_host_bundle
    from agent_bridge.session_host.launcher import _spawn_child

    home = tmp_path / "target-home"
    home.mkdir()
    if settings:
        prefs = home / ".copilot" / "settings.json"
        prefs.parent.mkdir()
        prefs.write_text(json.dumps({"model": "target", "effortLevel": "medium"}))
    bundle, _ = build_session_host_bundle(tmp_path / "bundles")
    monkeypatch.setattr(sys, "argv", [str(bundle)])
    script = "import os,json;print(json.dumps({'pid':os.getpid(),'leak':any(k.startswith('AGENT_BRIDGE_PREFERENCE_') for k in os.environ)}))"
    program = " ".join(shlex.quote(part) for part in (sys.executable, "-c", script))
    command = f"export HOME={shlex.quote(str(home))}; {a.LAUNCH_TOKEN} {program}"
    descriptor = json.dumps({"user": str(os.geteuid()), "instance": "instance-fixture"})
    argv = [a.SHELL_MARKER, mode, descriptor, "bash", "-lc", command]
    if mode == "defaults" and not settings:
        with pytest.raises(ValueError, match="missing or unreadable"):
            await spawn_attested(argv, str(tmp_path), {}, _spawn_child)
        return
    child, receipt = await spawn_attested(argv, str(tmp_path), {}, _spawn_child)
    stdout, _ = await asyncio.wait_for(child.communicate(), 10)
    output = json.loads(stdout)
    assert output == {"pid": child.pid, "leak": False}
    assert receipt["child_pid"] == child.pid
    assert receipt["authority"]["space"]["home"] == a.path_digest(home)
    assert validate_receipt(receipt, child.pid) == receipt


def test_selected_instance_binding_is_not_hostname_equivalence(sealed):
    _, frame = sealed
    descriptor = {"user": "runner", "instance": "instance-one"}
    args = [a.SHELL_MARKER, "defaults", json.dumps(descriptor), "bash", "-lc", "command"]
    frame["receipt"]["authority"]["target"] = a.target_digest(descriptor)
    verify_launch_receipt(args, frame["receipt"], "instance-one")
    with pytest.raises(ValueError, match="instance changed"):
        verify_launch_receipt(args, frame["receipt"], "instance-two")
    with pytest.raises(ValueError, match="did not confirm"):
        verify_launch_receipt(args, None, "instance-one")


def test_explicit_local_profile_does_not_need_an_unrelated_default_file(sealed):
    expected, frame = sealed
    frame["receipt"].update(
        status="missing", values={"model": "local"}, sources={"model": "launch-profile"},
    )
    assert a.verify_frame(frame, expected, 321)["values"] == {"model": "local"}


def test_zipapp_dispatch_propagates_refused_exec_status(monkeypatch):
    from agent_bridge import preference_exec
    from agent_bridge.session_host import launcher

    monkeypatch.setattr(preference_exec, "main", lambda argv: 78)
    with pytest.raises(SystemExit) as error:
        launcher.main(["--preference-exec", "--", "program"])
    assert error.value.code == 78


@pytest.mark.parametrize("component", [
    "agent_bridge.session_host.preference_spawn",
    "agent_bridge.session_host.launcher",
    "agent_bridge.session_host.host",
])
def test_verifier_dispatcher_and_receipt_emitter_are_digest_bound(monkeypatch, component):
    from importlib.util import find_spec

    original = find_spec
    before = a.component_digest()

    def changed(name):
        spec = original(name)
        if name == component:
            source = spec.loader.get_source(name)
            return SimpleNamespace(loader=SimpleNamespace(get_source=lambda ignored: source + "\n# changed\n"))
        return spec

    monkeypatch.setattr("importlib.util.find_spec", changed)
    assert a.component_digest() != before


def test_digest_covers_the_staged_host_role_closure():
    from agent_bridge.session_host.bundle import _AGENT_BRIDGE_MODULES

    names = set()
    for path in _AGENT_BRIDGE_MODULES:
        name = "agent_bridge." + path.removesuffix(".py").replace("/", ".")
        names.add(name.removesuffix(".__init__"))
    assert set(a.AUTHORITY_MODULES) == names | {"agent_procutil"}


@pytest.mark.parametrize("consent", [b"\x01", b""])
def test_exec_component_requires_consent_and_drops_binding_environment(monkeypatch, consent):
    from agent_bridge import preference_exec as e

    descriptor = {"user": "runner", "instance": "instance"}
    for key, value in {
        a.FD_ENV: "99", a.NONCE_ENV: "launch-secret",
        a.DIGEST_ENV: a.component_digest(), a.MODE_ENV: "selection",
        a.TARGET_ENV: a.target_digest(descriptor), "PROXY_MODE": "preserved",
    }.items():
        monkeypatch.setenv(key, value)
    channel = SimpleNamespace(
        settimeout=lambda timeout: None, sendall=lambda data: frames.append(json.loads(data)),
        recv=lambda size: consent,
    )
    class Channel:
        def __enter__(self):
            return channel

        def __exit__(self, *args):
            return None

    frames, executed = [], []
    monkeypatch.setattr(e.socket, "socket", lambda **kwargs: Channel())
    monkeypatch.setattr(e, "namespace_context", lambda: {
        "platform": "linux", "uid": 1000, "namespace": "a" * 64,
    })
    monkeypatch.setattr(e, "process_start", lambda pid: "99")
    monkeypatch.setattr(e, "execution_settings", lambda *args: {
        "child_pid": os.getpid(), "status": "missing", "values": {}, "sources": {},
    })
    monkeypatch.setattr(e.os, "execvpe", lambda *args: executed.append(args))
    assert e.main(["program", "--model", "explicit"]) == (0 if consent else 78)
    assert frames[0]["proof"]["nonce"] == "launch-secret"
    assert bool(executed) == bool(consent)
    if consent:
        environment = executed[0][2]
        assert environment["PROXY_MODE"] == "preserved"
        assert not set(environment) & {
            a.FD_ENV, a.NONCE_ENV, a.DIGEST_ENV, a.MODE_ENV, a.TARGET_ENV,
        }


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux kernel execution binding")
@pytest.mark.guard
@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["nonce", "user", "cwd"])
async def test_real_bundle_refuses_binding_fault_before_program_exec(monkeypatch, tmp_path, fault):
    from agent_bridge.session_host.bundle import build_session_host_bundle
    from agent_bridge.session_host.launcher import _spawn_child

    home = tmp_path / "home"
    prefs = home / ".copilot" / "settings.json"
    prefs.parent.mkdir(parents=True)
    prefs.write_text(json.dumps({"model": "target", "effortLevel": "medium"}))
    other = tmp_path / "other"
    other.mkdir()
    bundle, _ = build_session_host_bundle(tmp_path / "bundles")
    monkeypatch.setattr(sys, "argv", [str(bundle)])
    program = " ".join(shlex.quote(part) for part in (sys.executable, "-c", "raise RuntimeError('exec must not be reached')"))
    prefix = f"export HOME={shlex.quote(str(home))}; "
    if fault == "nonce":
        prefix += f"export {a.NONCE_ENV}=wrong; "
    if fault == "cwd":
        prefix += f"cd {shlex.quote(str(other))}; "
    uid = os.geteuid() + (1 if fault == "user" else 0)
    descriptor = json.dumps({"user": str(uid), "instance": "instance-fixture"})
    argv = [a.SHELL_MARKER, "defaults", descriptor, "bash", "-lc",
            prefix + a.LAUNCH_TOKEN + " " + program]
    with pytest.raises(ValueError, match="mismatch"):
        await spawn_attested(argv, str(tmp_path), {}, _spawn_child)
