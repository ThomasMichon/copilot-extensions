"""Isolated real-process receiving controls; not native Copilot acceptance."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
import zipfile

from agent_procutil import no_window_kwargs
import pytest

from agent_remote_driver.session_host.bundle import build_session_host_bundle
from agent_remote_driver.session_host import bundle
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host import launcher, legacy_launcher
from agent_remote_driver.session_host.startup_admission import CONTEXT_ENV, StartupAdmissionServer

_CHILD = """
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if "id" in request:
        result={"method":request["method"],"params":request.get("params"),
                "sessionId":"fixture-native"}
        print(json.dumps({"jsonrpc":"2.0","id":request["id"],"result":result}),flush=True)
"""


@pytest.mark.parametrize("legacy", [False, True])
def test_compact_archive_rebuild_is_byte_identical(tmp_path, monkeypatch, legacy):
    monkeypatch.setattr(zipfile.time, "localtime", lambda _: (2001, 1, 1, 0, 0, 0, 0, 1, -1))
    first, source = build_session_host_bundle(tmp_path / "first", legacy=legacy)
    expected = first.read_bytes()
    first.unlink()
    monkeypatch.setattr(zipfile.time, "localtime", lambda _: (2026, 1, 1, 0, 0, 0, 0, 1, -1))
    rebuilt, rebuilt_source = build_session_host_bundle(tmp_path / "first", legacy=legacy)
    assert (rebuilt_source, rebuilt.read_bytes()) == (source, expected)


def _relocate(tmp_path: Path, *, legacy: bool = False) -> Path:
    archive, _digest = build_session_host_bundle(tmp_path / "build", legacy=legacy)
    receiver = tmp_path / "receiver"
    receiver.mkdir()
    relocated = receiver / archive.name
    shutil.copyfile(archive, relocated)
    shutil.rmtree(tmp_path / "build")
    return relocated


@pytest.mark.parametrize("legacy", [False, True])
def test_relocated_compact_recovery_codec_roundtrips_complete_record(tmp_path, legacy):
    archive = _relocate(tmp_path, legacy=legacy)
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from agent_remote_driver.headed.recovery import RecoveryDescriptor, RecoveryRecord, RecoveryStep
from agent_remote_driver.headed.recovery_codec import encode_recovery_record, decode_recovery_record
descriptor = RecoveryDescriptor(
    "owner", "worktree", "revision", "source", "source-revision", "same-conversation",
    "environment", "boot", "scope", "incarnation", 1,
)
record = RecoveryRecord(
    "operation", descriptor,
    (RecoveryStep("retire_intent", "owner-evidence", ("unknown-action",)),),
    ("native-connection-lost",),
)
restored = decode_recovery_record(encode_recovery_record(record))
assert restored == record
assert restored.unknown_effects == ("unknown-action",)
assert restored.status()["effects_wired"] is False
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(archive)], cwd=archive.parent,
        capture_output=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


async def _close_frontend(client):
    await asyncio.wait_for(client.close(), 3)


def _prior_components(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    fixtures = root / "plugins/agent-bridge/contract/fixtures/session-host/prior-runtime-dev150"
    commit = "4ed08dcdd0e72377b95a67d6bd22aec819bf7fec"
    package = ModuleType("_driver_dev150_frontend")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name, blob in (
        ("protocol", "dde0d3566ce5d0cd92a49d8f6438dd907d533993"),
        ("client", "c9e88467f09e4438a214efa432733b59352eefd1"),
        ("host", "de3aed3a2d57a98767c8f69c3ebe6d59eb5d52a4"),
    ):
        frozen = json.loads((fixtures / (name + "-source.json")).read_text(encoding="utf-8"))
        captured, raw = frozen["captured_from"], frozen["source_text"].encode("utf-8")
        assert captured["commit"] == commit and captured["plugin_version"] == "0.4.0-dev150"
        assert captured["protocol_generation"] == 1 and captured["source_git_blob"] == blob
        assert captured["source_path"] == f"plugins/agent-bridge/src/agent_bridge/session_host/{name}.py"
        assert hashlib.sha256(raw).hexdigest() == captured["source_sha256"]
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == blob
        module = ModuleType(package.__name__ + "." + name)
        module.__package__ = package.__name__
        monkeypatch.setitem(sys.modules, module.__name__, module)
        exec(compile(raw, commit + ":" + captured["source_path"], "exec"), module.__dict__)
        setattr(package, name, module)
    return package


@pytest.mark.parametrize("component", ["client", "host"])
def test_prior_components_refuse_corrupted_frozen_source(monkeypatch, component):
    read_text = Path.read_text

    def corrupted(path, *args, **kwargs):
        body = read_text(path, *args, **kwargs)
        if path.name == component + "-source.json":
            frozen = json.loads(body)
            frozen["source_text"] += "\n"
            return json.dumps(frozen)
        return body

    monkeypatch.setattr(Path, "read_text", corrupted)
    with pytest.raises(AssertionError):
        _prior_components(monkeypatch)
    assert "_driver_dev150_frontend." + component not in sys.modules


@pytest.mark.asyncio
@pytest.mark.parametrize("frontend", ["current", "prior-runtime-dev150"])
async def test_frontend_close_preserves_abort_recovery_and_bounds_prior(monkeypatch, frontend):
    client_type = (
        SessionHostClient if frontend == "current"
        else _prior_components(monkeypatch).client.SessionHostClient
    )

    async def stalled():
        await asyncio.Future()

    transport = SimpleNamespace(abort=Mock())
    writer = SimpleNamespace(close=Mock(), wait_closed=stalled, transport=transport)
    client = client_type(asyncio.StreamReader(), writer)
    if frontend == "current":
        await _close_frontend(client)
        transport.abort.assert_called_once_with()
    else:
        with pytest.raises(asyncio.TimeoutError):
            await _close_frontend(client)
    writer.close.assert_called_once_with()


def test_consumers_reach_the_same_core_through_canonical_build_owners(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "tools"))
    monkeypatch.syspath_prepend(str(root / "libs" / "installer-engine"))
    import build_python_artifacts as builder
    from installer_dependency_policy import _anchor_roots

    canonical = root / "libs" / "copilot-driver-core"
    for consumer in ("agent-remote-driver", "agent-bridge", "agent-worktrees"):
        closure = dict(builder.resolve_vendored_libs(root / "plugins" / consumer))
        assert closure["copilot-driver-core"] == canonical
    assert canonical in _anchor_roots()


def test_core_mux_execution_preserves_literal_arguments_without_worktrees(tmp_path):
    from agent_procutil import direct_python

    interpreter, environment = direct_python(sys.executable)
    literal = 'first line\n"quoted" & literal $value'
    program = """
import json,sys
from agent_remote_driver.mux_creation import run_or_join
result=run_or_join([], binary=sys.executable, session="unused-fixture", prepare=lambda: [
    sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", sys.argv[1],
])
assert result.returncode == 0, result.stderr
assert json.loads(result.stdout) == [sys.argv[1]]
assert not any(name == "agent_worktrees" or name.startswith("agent_worktrees.") for name in sys.modules)
"""
    result = subprocess.run(
        [interpreter, "-I", "-c", program, literal],
        cwd=tmp_path, env={**os.environ, **environment}, capture_output=True,
        text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_receiving_archive_publication_stays_on_the_selected_filesystem(tmp_path, monkeypatch):
    destination = tmp_path / "selected volume" / "runtime"
    replace = os.replace
    publications = []

    def same_volume(source, target):
        source, target = Path(source), Path(target)
        if not source.is_relative_to(destination):
            raise OSError(errno.EXDEV, "simulated distinct temporary filesystem")
        publications.append((source, target))
        return replace(source, target)

    monkeypatch.setattr(bundle.os, "replace", same_volume)
    archive, digest = build_session_host_bundle(destination)
    with zipfile.ZipFile(archive) as contents:
        assert contents.testzip() is None
        assert contents.read("__main__.py")
    assert build_session_host_bundle(destination) == (archive, digest)
    assert len(publications) == 1
    assert list(destination.iterdir()) == [archive]


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_host_state_publication_retries_transient_windows_readers(tmp_path, monkeypatch, winerror):
    from agent_remote_driver.session_host import host_state

    path = tmp_path / "state.json"
    path.write_text('{"state":"running"}', encoding="utf-8")
    selected_os = SimpleNamespace(**vars(os))
    attempts, delays = [], []

    def replace(source, target):
        attempts.append(source)
        assert json.loads(path.read_text(encoding="utf-8")) == {"state": "running"}
        if len(attempts) == 1:
            error = PermissionError("fixture reader holds the old record")
            error.winerror = winerror
            raise error
        os.replace(source, target)

    selected_os.replace = replace
    monkeypatch.setattr(host_state, "os", selected_os)
    monkeypatch.setattr(host_state, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(host_state, "time", SimpleNamespace(sleep=delays.append))
    assert host_state.write_host_state(path, {"state": "child_exited"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"state": "child_exited"}
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert delays == [.01] and list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("platform,winerror,attempts", [
    ("win32", 5, 20), ("linux", 5, 1), ("win32", 87, 1),
])
def test_host_state_publication_refuses_persistent_or_unrelated_errors(
    tmp_path, monkeypatch, platform, winerror, attempts,
):
    from agent_remote_driver.session_host import host_state

    path = tmp_path / "state.json"
    original = b'{"state":"running"}'
    path.write_bytes(original)
    selected_os = SimpleNamespace(**vars(os))
    error = PermissionError("fixture publication refused")
    error.winerror = winerror
    replace = Mock(side_effect=error)
    selected_os.replace = replace
    delays = []
    monkeypatch.setattr(host_state, "os", selected_os)
    monkeypatch.setattr(host_state, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(host_state, "time", SimpleNamespace(sleep=delays.append))
    with pytest.raises(PermissionError) as failure:
        host_state.write_host_state(path, {"state": "child_exited"})
    assert failure.value is error and replace.call_count == attempts
    assert len(delays) == attempts - 1
    assert path.read_bytes() == original and list(tmp_path.iterdir()) == [path]


def test_legacy_receiving_artifact_declares_and_enforces_only_legacy_support(tmp_path):
    archive, _ = build_session_host_bundle(tmp_path / "legacy", legacy=True)
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--support-json"],
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    support = json.loads(result.stdout)
    assert support["transports"] == ["tcp-frame"]
    assert support["lifetime_policies"] == ["legacy"]
    marker = tmp_path / "must-not-start"
    for options in (["--lifetime-policy", "acp"], ["--ws-port", "0"]):
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(archive), *options, "--",
             sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            capture_output=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 2 and not marker.exists()


def test_compact_receiving_artifact_never_claims_or_launches_optional_sdk(tmp_path):
    archive, _ = build_session_host_bundle(tmp_path / "compact")
    support = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--support-json"],
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert support.returncode == 0, support.stderr
    assert json.loads(support.stdout)["headless_backends"] == ["native-acp"]
    marker = tmp_path / "native-must-not-start"
    refused = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--cwd", str(tmp_path), "--backend", "sdk", "--",
         sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        cwd=tmp_path, capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert refused.returncode != 0 and not marker.exists()
    assert "sdk_backend" in refused.stderr


@pytest.mark.parametrize("program,expected,native_exit", [
    ("raise SystemExit(17)", 17, 17),
    pytest.param(
        "import os,signal;os.kill(os.getpid(),signal.SIGTERM)", 143, -15,
        marks=pytest.mark.skipif(os.name != "posix", reason="Native POSIX signal exit"),
    ),
])
def test_retained_headed_executable_propagates_actual_native_exit(
    tmp_path, program, expected, native_exit,
):
    from agent_procutil._interpreter import physical_executable
    from agent_remote_driver.headed.launcher import prepare_headed_command

    python = physical_executable(getattr(sys, "_base_executable", sys.executable))
    state_file = tmp_path / "headed.json"
    command = prepare_headed_command(
        [python, "-I", "-c", program, "--session-id", "fixture-native",
         "--log-dir", "--resume=literal"],
        session_id="fixture-native", attempt_directory=tmp_path / "attempt",
        state_file=state_file, cwd=str(tmp_path),
    )
    result = subprocess.run(
        command, cwd=tmp_path, env={**os.environ, "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == expected, result.stdout + result.stderr
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["child_exit_code"] == native_exit and state["state"] == "child_exited"


@pytest.mark.parametrize("before,after,experimental", [
    (["--log-dir", "--experimental"], [], False),
    ([], ["--", "--experimental", "literal"], False),
    (["--log-dir", "--"], ["--", "literal"], False),
    (["--experimental"], ["--", "literal"], True),
])
def test_retained_headed_extension_options_respect_native_boundaries(
    tmp_path, before, after, experimental,
):
    from agent_procutil._interpreter import physical_executable
    from agent_remote_driver.headed.launcher import prepare_headed_command

    python = physical_executable(getattr(sys, "_base_executable", sys.executable))
    command = prepare_headed_command(
        [python, "-I", "-c", "import json,sys; print(json.dumps(sys.argv[1:]))",
         "--session-id", "fixture-native", *before, *after],
        session_id="fixture-native", attempt_directory=tmp_path / "attempt",
        state_file=tmp_path / "headed.json", cwd=str(tmp_path),
    )
    result = subprocess.run(
        command, cwd=tmp_path, env={**os.environ, "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    plugin_index = actual.index("--plugin-dir")
    plugin = actual[plugin_index + 1]
    assert Path(plugin).is_dir()
    assert actual == [
        "--session-id", "fixture-native", *before, "--plugin-dir", plugin,
        *([] if experimental else ["--experimental"]), *after,
    ]


def test_legacy_entrypoint_defaults_to_its_declared_lifetime(monkeypatch, capsys):
    assert legacy_launcher.main(["--support-json"]) == 0
    declaration = json.loads(capsys.readouterr().out)
    assert declaration["transports"] == ["tcp-frame"]
    assert declaration["lifetime_policies"] == ["legacy"]
    execution = AsyncMock()
    monkeypatch.setattr(legacy_launcher, "run_host", execution)
    assert legacy_launcher.main(["--", "inert-fixture"]) == 0
    execution.assert_awaited_once()
    assert execution.call_args.kwargs["lifetime_policy"] == "legacy"
    assert execution.call_args.kwargs["ws_port"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", [{"lifetime_policy": "acp"}, {"ws_port": 0}])
async def test_legacy_api_refuses_unsupported_execution_before_policy_or_spawn(
    monkeypatch, unsupported,
):
    policy = Mock(side_effect=AssertionError("must refuse before policy execution"))
    execution = AsyncMock(side_effect=AssertionError("must not start Host"))
    monkeypatch.setattr(legacy_launcher, "require_path_hosting", policy)
    monkeypatch.setattr(launcher, "run_host", execution)
    with pytest.raises(ValueError, match="legacy|WebSocket"):
        await legacy_launcher.run_host(["must-not-start"], **unsupported)
    policy.assert_not_called()
    execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_core_api_refuses_unknown_lifetime_before_policy_or_spawn(monkeypatch):
    policy = Mock(side_effect=AssertionError("must refuse before policy execution"))
    spawn = AsyncMock(side_effect=AssertionError("must not create child"))
    monkeypatch.setattr(launcher, "_require_receiving_policy", policy)
    monkeypatch.setattr(launcher, "_spawn_child", spawn)
    with pytest.raises(ValueError, match="Unsupported Session Host lifetime"):
        await launcher.run_host(["must-not-start"], lifetime_policy="unknown")
    policy.assert_not_called()
    spawn.assert_not_awaited()


def test_relocated_core_support_and_preference_closure_without_bridge(tmp_path):
    archive = _relocate(tmp_path)
    with zipfile.ZipFile(archive) as contents:
        assert not any(name.startswith("agent_bridge/") for name in contents.namelist())
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--support-json"],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    declaration = json.loads(result.stdout)
    assert declaration["contract"] == "agent-remote-driver.session-host"
    assert declaration["automatic_idle_retirement"]["availability"] == "unavailable"
    assert declaration["transports"] == ["tcp-frame"]
    marker = tmp_path / "unsupported-child"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--ws-port", "0",
         "--cwd", str(tmp_path), "--", sys.executable, "-c",
         f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        capture_output=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode != 0 and not marker.exists()
    program = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from agent_remote_driver.preference_attestation import component_digest;"
        "assert len(component_digest())==64;"
        "assert not any(n=='agent_bridge' or n.startswith('agent_bridge.') for n in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", program, str(archive)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("change", ["string-modes", "boolean-wire", "missing-admission"])
def test_selected_core_rejects_malformed_or_unsupported_contract_before_host_spawn(
    tmp_path, monkeypatch, capsys, change,
):
    launcher.main(["--support-json"])
    declaration = json.loads(capsys.readouterr().out)
    if change == "string-modes":
        declaration["modes"] = "headless"
    elif change == "boolean-wire":
        declaration["protocol_version"] = True
    else:
        declaration.pop("startup_admission")
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(declaration),
    ))
    from agent_remote_driver.session_host import launcher_spawn
    spawn = Mock(side_effect=AssertionError("unsupported Host must not start"))
    monkeypatch.setattr(launcher_spawn, "spawn_host", spawn)
    with pytest.raises(RuntimeError, match="does not support"):
        launcher.launch_session_host(
            ["fixture-child-must-not-start"], state_dir=tmp_path, session_id="fixture-session",
            acquisition_launch=SimpleNamespace(session_id="fixture-session"),
        )
    spawn.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("run,reason", [
    (launcher.run_host, "requires its receiving owner"),
    (legacy_launcher.run_host, "cannot validate original local launch custody"),
])
async def test_unavailable_original_custody_refuses_before_child_creation(tmp_path, run, reason):
    with pytest.raises(ValueError, match=reason):
        await run(
            ["fixture-must-not-be-created"], cwd=str(tmp_path),
            env={"AGENT_BRIDGE_ORIGINAL_ACQUISITION": "unvalidated-fixture-context"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["accepted", "closed-before-exec", "caller-unavailable"])
async def test_relocated_core_requires_live_owner_at_actual_child_spawn(tmp_path, outcome):
    from agent_procutil._interpreter import physical_executable

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    archive = _relocate(tmp_path)
    state, marker = tmp_path / "state.json", tmp_path / "child-started"
    child = (
        "import os;from pathlib import Path;"
        f"assert {CONTEXT_ENV!r} not in os.environ;"
        f"Path({str(marker)!r}).touch();\n" + _CHILD
    )
    argv = [interpreter, "-I", "-S", "-u", "-c", child]
    checks = []

    def check(request):
        assert request["argv"] == argv
        assert request["cwd"] == str(tmp_path)
        assert request["session_id"] == "fixture-session"
        assert request["environment"] == {"FIXTURE_SESSION": "fixture-session"}
        checks.append(request)
        if outcome == "closed-before-exec" and len(checks) == 2:
            raise ValueError("fixture original epoch closed")

    admission = StartupAdmissionServer(check, environment_keys=("FIXTURE_SESSION",), timeout=10)
    if outcome == "caller-unavailable":
        admission.close()
    proc = subprocess.Popen(
        [interpreter, "-I", "-S", str(archive), "--state-file", str(state),
         "--cwd", str(tmp_path), "--session-id", "fixture-session", "--", *argv],
        env={**os.environ, CONTEXT_ENV: admission.context, "FIXTURE_SESSION": "fixture-session"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    client = None
    output = None
    try:
        if outcome != "caller-unavailable":
            admission.start(proc)
        deadline = time.monotonic() + 10
        while (
            (not state.exists() or outcome == "accepted" and not marker.exists())
            and proc.poll() is None and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.025)
        if outcome == "accepted":
            assert state.exists(), proc.communicate(timeout=1)[1]
            facts = json.loads(state.read_text())
            assert facts["pid"] == proc.pid and marker.exists()
            assert len(checks) == 2
            client = await SessionHostClient.connect(port=facts["port"])
            await client.attach(nonce=facts["nonce"].encode())
            await client.terminate()
        else:
            await asyncio.to_thread(proc.wait, timeout=5)
            assert proc.returncode != 0
            assert not state.exists() and not marker.exists()
            assert len(checks) == (2 if outcome == "closed-before-exec" else 0)
            output = await asyncio.to_thread(proc.communicate, timeout=5)
            if outcome == "closed-before-exec":
                # The caller's actual refusal reason reaches the Host's user.
                assert (
                    b"refused startup admission: ValueError: fixture original epoch closed"
                    in output[1]
                ), output[1]
    finally:
        admission.close()
        if client is not None:
            await client.close()
        if proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait, timeout=5)
        if output is None:
            proc.communicate(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("frontend", ["current", "prior-runtime-dev150"])
@pytest.mark.parametrize("legacy,reapable", [(False, True), (True, False), (True, True)])
async def test_relocated_core_reconnects_and_stops_exact_child(
    tmp_path, monkeypatch, legacy, reapable, frontend,
):
    from agent_procutil._interpreter import physical_executable
    from agent_remote_driver.session_host.osutil import pid_alive

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    client_type = (
        SessionHostClient if frontend == "current"
        else _prior_components(monkeypatch).client.SessionHostClient
    )
    archive = _relocate(tmp_path, legacy=legacy)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    policy_context = json.dumps({
        "schema": "agent-bridge.receiving-policy", "version": 1,
        "marketplaceId": "fixture",
    })
    state = tmp_path / "state.json"
    nonce = "fixture-only-nonce"
    proc = subprocess.Popen(
        [
            interpreter, "-I", "-S", str(archive),
            "--cwd", str(workspace), "--state-file", str(state),
            "--repository-policy-context", policy_context,
            "--unexpected-reap-seconds", "0.02",
            "--", interpreter, "-I", "-S", "-u", "-c", _CHILD,
        ],
        env={**os.environ, "AGENT_BRIDGE_SESSION_HOST_NONCE": nonce,
             "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    client = None
    try:
        for _ in range(200):
            if state.exists():
                facts = json.loads(state.read_text(encoding="utf-8"))
                break
            if proc.poll() is not None:
                _stdout, stderr = proc.communicate()
                pytest.fail(stderr.decode("utf-8", "replace"))
            await asyncio.sleep(0.025)
        else:
            pytest.fail("relocated Host did not publish readiness")
        assert facts["pid"] == proc.pid
        assert facts["lifetime_policy"] == ("legacy" if legacy else "acp")
        client = await client_type.connect(port=facts["port"])
        hello = await client.attach(nonce=nonce.encode())
        assert hello.child_pid == facts["child_pid"]
        cursor = 0
        literal = 'first line\n"quoted" & literal $value'
        for identifier, method, params in (
            (1, "initialize", {"protocolVersion": 1}),
            (2, "session/new", {"cwd": str(workspace), "mcpServers": []}),
            (3, "session/prompt", {"sessionId": "fixture-native",
                                   "prompt": [{"type": "text", "text": literal}]}),
        ):
            await client.write((json.dumps({
                "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
            }) + "\n").encode())
            cursor, frame = await asyncio.wait_for(anext(client.frames()), 5)
            assert json.loads(frame)["result"]["params"] == params
            await client.ack(cursor)
        await client.detach(reapable=reapable)
        await _close_frontend(client)
        client = None
        if legacy and reapable:
            await asyncio.to_thread(proc.wait, timeout=5)
            assert proc.returncode == 0, proc.communicate(timeout=5)
            assert not pid_alive(hello.child_pid)
            return
        await asyncio.sleep(0.1)
        assert proc.poll() is None
        client = await client_type.connect(port=facts["port"])
        restored = await client.attach(cursor, nonce=nonce.encode())
        assert restored.child_pid == hello.child_pid
        assert restored.max_seq == cursor
        await client.write((json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "session/load",
            "params": {"sessionId": "fixture-native", "cwd": str(workspace), "mcpServers": []},
        }) + "\n").encode())
        next_cursor, frame = await asyncio.wait_for(anext(client.frames()), 5)
        assert next_cursor == cursor + 1
        assert json.loads(frame)["result"]["sessionId"] == "fixture-native"
        await client.ack(next_cursor)
        await client.terminate()
        assert [frame async for frame in client.frames()] == []
        if legacy:
            for _ in range(200):
                if json.loads(state.read_text(encoding="utf-8"))["state"] == "child_exited":
                    break
                await asyncio.sleep(.025)
            else:
                pytest.fail(
                    "Legacy Host did not publish its child exit: "
                    + json.dumps({
                        "state": json.loads(state.read_text(encoding="utf-8")),
                        "child_alive": pid_alive(hello.child_pid), "host_exit": proc.poll(),
                    })
                )
            assert not pid_alive(hello.child_pid)
            assert proc.poll() is None
            return
        assert not client.child_alive
        await asyncio.to_thread(proc.wait, timeout=5)
        assert proc.returncode == 0
        assert not pid_alive(hello.child_pid)
    finally:
        try:
            if client is not None:
                await _close_frontend(client)
        finally:
            if proc.poll() is None:
                proc.kill()
                await asyncio.to_thread(proc.wait, timeout=5)
            _stdout, stderr = proc.communicate(timeout=5)
            if stderr:
                print(stderr.decode("utf-8", "replace"))


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("idle_detach", [False, True])
async def test_current_frontend_reconnects_to_exact_prior_host(
    tmp_path, monkeypatch, idle_detach,
):
    from agent_procutil._interpreter import physical_executable

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    prior = _prior_components(monkeypatch)
    servers, handlers = [], []
    start_server = asyncio.start_server

    async def owned_server(callback, *args, **kwargs):
        async def owned_front(reader, writer):
            handlers.append(asyncio.current_task())
            try:
                await callback(reader, writer)
            finally:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 3)

        server = await start_server(owned_front, *args, **kwargs)
        servers.append(server)
        return server

    # The historical close does not await its listener or frontend handlers.
    monkeypatch.setattr(prior.host, "asyncio", SimpleNamespace(
        **{**vars(asyncio), "start_server": owned_server},
    ))
    child = await asyncio.create_subprocess_exec(
        interpreter, "-I", "-S", "-u", "-c", _CHILD,
        cwd=tmp_path, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    host, client = None, None
    try:
        host = prior.host.SessionHost(child, nonce="fixture-only-nonce")
        port = await asyncio.wait_for(host.serve(), 5)
        client = await asyncio.wait_for(SessionHostClient.connect(port=port), 5)
        hello = await asyncio.wait_for(client.attach(nonce=b"fixture-only-nonce"), 5)
        assert hello.child_pid == child.pid and hello.max_seq == 0
        cursor = 0
        literal = 'first line\n"quoted" & literal $value'
        for identifier, method, params in (
            (1, "initialize", {"protocolVersion": 1}),
            (2, "session/new", {"cwd": str(tmp_path), "mcpServers": []}),
            (3, "session/prompt", {"sessionId": "fixture-native",
                                   "prompt": [{"type": "text", "text": literal}]}),
        ):
            await client.write((json.dumps({
                "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
            }) + "\n").encode())
            next_cursor, frame = await asyncio.wait_for(anext(client.frames()), 5)
            assert next_cursor == cursor + 1
            assert json.loads(frame)["result"] == {
                "method": method, "params": params, "sessionId": "fixture-native",
            }
            cursor = next_cursor
            if identifier != 3:
                await client.ack(cursor)
        # A lost attach cursor must replay this retained frame and fail below.
        assert host.ack_cursor == cursor - 1 and host.buffered_seqs == [cursor]
        await client.detach(reapable=False)
        await _close_frontend(client)
        client = None
        await asyncio.sleep(.1)
        assert child.returncode is None and host.child_alive
        client = await asyncio.wait_for(SessionHostClient.connect(port=port), 5)
        restored = await asyncio.wait_for(client.attach(cursor, nonce=b"fixture-only-nonce"), 5)
        assert restored.child_pid == child.pid and restored.max_seq == cursor
        params = {"sessionId": "fixture-native", "cwd": str(tmp_path), "mcpServers": []}
        await client.write((json.dumps({
            "jsonrpc": "2.0", "id": 4, "method": "session/load", "params": params,
        }) + "\n").encode())
        next_cursor, frame = await asyncio.wait_for(anext(client.frames()), 5)
        assert next_cursor == cursor + 1
        assert json.loads(frame)["result"]["params"] == params
        await client.ack(next_cursor)
        if idle_detach:
            await client.detach(reapable=True)
        else:
            await client.terminate()
        assert await asyncio.wait_for(anext(client.frames(), None), 5) is None
        await asyncio.wait_for(child.wait(), 5)
        assert child.returncode is not None and not host.child_alive
    finally:
        try:
            if client is not None:
                await _close_frontend(client)
        finally:
            try:
                if host is not None:
                    await asyncio.wait_for(host.close(), 5)
            finally:
                if child.returncode is None:
                    child.kill()
                await asyncio.wait_for(child.communicate(), 5)
                for server in servers:
                    server.close()
                    await asyncio.wait_for(server.wait_closed(), 5)
                if handlers:
                    await asyncio.wait_for(asyncio.gather(*handlers), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["owner", "server"])
async def test_legacy_host_cancellation_still_propagates_and_retires_child(
    tmp_path, monkeypatch, source,
):
    from agent_procutil._interpreter import physical_executable
    from agent_remote_driver.session_host.osutil import pid_alive

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    state = tmp_path / "state.json"
    ready = asyncio.Event()
    cancelled = asyncio.Event()
    if source == "server":
        async def cancel_server(self):
            await cancelled.wait()
            raise asyncio.CancelledError

        monkeypatch.setattr(launcher.SessionHost, "serve_forever", cancel_server)
    task = asyncio.create_task(launcher.run_host(
        [interpreter, "-I", "-S", "-u", "-c", _CHILD],
        cwd=str(tmp_path), state_file=state, ready=ready, lifetime_policy="legacy",
    ))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        child_pid = json.loads(state.read_text(encoding="utf-8"))["child_pid"]
        assert pid_alive(child_pid)
        if source == "owner":
            task.cancel()
        else:
            cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert not pid_alive(child_pid)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
