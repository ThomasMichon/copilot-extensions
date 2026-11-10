"""Isolated real-process receiving controls; not native Copilot acceptance."""

from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
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


def _relocate(tmp_path: Path) -> Path:
    archive, _digest = build_session_host_bundle(tmp_path / "build")
    receiver = tmp_path / "receiver"
    receiver.mkdir()
    relocated = receiver / archive.name
    shutil.copyfile(archive, relocated)
    shutil.rmtree(tmp_path / "build")
    return relocated


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
    spawn = Mock(side_effect=AssertionError("unsupported Host must not start"))
    monkeypatch.setattr(launcher, "spawn_instance", spawn)
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
    finally:
        admission.close()
        if client is not None:
            await client.close()
        if proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait, timeout=5)
        proc.communicate(timeout=5)


@pytest.mark.asyncio
async def test_relocated_core_reconnects_and_stops_exact_child(tmp_path):
    from agent_procutil._interpreter import physical_executable

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    archive = _relocate(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state.json"
    nonce = "fixture-only-nonce"
    proc = subprocess.Popen(
        [
            interpreter, "-I", "-S", str(archive),
            "--cwd", str(workspace), "--state-file", str(state),
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
        assert facts["lifetime_policy"] == "acp"
        client = await SessionHostClient.connect(port=facts["port"])
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
        await client.detach(reapable=True)
        await client.close()
        client = None
        await asyncio.sleep(0.1)
        assert proc.poll() is None
        client = await SessionHostClient.connect(port=facts["port"])
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
        assert not client.child_alive
        await asyncio.to_thread(proc.wait, timeout=5)
        assert proc.returncode == 0
        from agent_remote_driver.session_host.osutil import pid_alive

        assert not pid_alive(hello.child_pid)
    finally:
        if client is not None:
            await client.close()
        if proc.poll() is None:
            proc.kill()
            await asyncio.to_thread(proc.wait, timeout=5)
        proc.communicate(timeout=5)
