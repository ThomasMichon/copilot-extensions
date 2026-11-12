"""First-touch application construction through the existing build/install owners."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
import pytest
from websockets.asyncio.client import connect

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.osutil import kill_pid, pid_alive, process_start_time

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "plugins" / "agent-remote-driver"


def _run(arguments, *, cwd, env, deadline):
    result = subprocess.run(
        list(map(str, arguments)), cwd=cwd, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=max(0.01, deadline - time.monotonic()),
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _frontdoor(slot, *arguments, command="agent-remote-driver"):
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        assert shell is not None
        return [
            shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(slot / f"{command}.ps1"), *arguments,
        ]
    return [str(slot / command), *arguments]


def _prepare_application(
    inputs, profile, *, monkeypatch, uv, interpreter, deadline, replacement=False,
):
    import build_python_artifacts as builder
    import materialize_main as materializer
    import local_install_profile as profiles
    install_source = profiles.source_inputs
    engine = install_source.engine

    source, artifacts = inputs / "plugins" / "agent-remote-driver", inputs / "wheels"
    source.mkdir(parents=True)
    artifacts.mkdir()
    shutil.copytree(
        SOURCE, source, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "*.egg-info",
        ),
    )
    entry = source / "src" / "agent_remote_driver_app" / "__init__.py"
    entry.write_text(
        entry.read_text(encoding="utf-8")
        + f"\n_fixture_generation = {2 if replacement else 1}\n"
        "_fixture_driver_main = main\n"
        "def main():\n"
        "    import json, os, sys\n"
        "    from pathlib import Path\n"
        "    output = os.environ.get('DRIVER_FIXTURE_GENERATION_OUTPUT')\n"
        "    if output:\n"
        "        Path(output).write_text(json.dumps({\n"
        "            'pid': os.getpid(), 'prefix': sys.prefix, 'app': __file__,\n"
        "            'core': sys.modules[_fixture_driver_main.__module__].__file__,\n"
        "            'generation': _fixture_generation,\n"
        "        }), encoding='utf-8')\n"
        "    return _fixture_driver_main()\n",
        encoding="utf-8",
    )
    materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=SOURCE, dest_consumer_dir=source, canonical_root=ROOT,
    )
    with monkeypatch.context() as patch:
        patch.setattr(builder, "REPO", inputs)
        artifact = builder.build_source_artifacts(
            identity={"plugin": "agent-remote-driver"}, source_dir=source,
            dependencies=builder.resolve_vendored_libs(source),
            out_dir=artifacts, python=interpreter, toolchain=None,
        )
    project = builder.tomllib.loads((source / "pyproject.toml").read_text())["project"]
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        "agent-remote-driver", project["version"], "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version(project["version"], "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    (inputs / install_source.MANIFEST).write_text(json.dumps({
        "schema": install_source.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"agent-remote-driver": {
            "payload": "plugins/agent-remote-driver", "wheels": "wheels",
            "source": identity.document(), "artifact": artifact,
        }},
    }), encoding="utf-8")
    prepared = profiles.prepare_registry_lock(
        inputs, profile, uv=uv, deadline=deadline,
        prepare_profile=True, component="agent-remote-driver",
    )
    assert prepared["install_ready"] is True
    return Path(prepared["snapshot"]) / "applications" / "agent-remote-driver"


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["unchanged", "replacement", "rollback"])
async def test_fresh_application_installs_reuses_and_runs_without_caller_sources(
    tmp_path, monkeypatch, record_testsuite_property, selection,
):
    sys.path.insert(0, str(ROOT / "tools"))
    import local_install_profile as profiles
    from governed_feed_trust import _governed_feed_configured
    engine = profiles.source_inputs.engine

    if not _governed_feed_configured():
        pytest.skip("native profile qualification requires the configured governed feed")
    uv = shutil.which("uv")
    assert uv is not None
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    started = time.monotonic()
    deadline = started + 300
    inputs, installation = tmp_path / "source-inputs", tmp_path / "installation"
    application = _prepare_application(
        inputs, tmp_path / "profile", monkeypatch=monkeypatch, uv=uv,
        interpreter=interpreter, deadline=deadline,
    )
    timings = {"package_prepare_seconds": time.monotonic() - started}
    declaration = json.loads((application / "application.json").read_text())
    construction = declaration["construction"]
    slot = installation / "versions" / construction["runtime_version"]
    shutil.copytree(application, slot)
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    env.pop(declaration["root_environment"], None)
    env.update(HOME=str(home), USERPROFILE=str(home),
               UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON_DOWNLOADS="never",
               UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1")
    install = _frontdoor(slot, command="install")
    install_started = time.monotonic()
    stage = _run([*install, "--stage"], cwd=caller, env=env, deadline=deadline)
    assert json.loads(stage.stdout)["state"] == "staged"
    assert not (installation / "current-version").exists()
    result = _run(install, cwd=caller, env=env, deadline=deadline)
    assert json.loads(result.stdout)["state"] == "installed"
    assert (installation / "current-version").read_text().strip() == slot.name
    timings["staged_first_install_seconds"] = time.monotonic() - install_started
    completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
    completions = {slot: completion}
    selected_slot = slot
    replacement_slot = None
    replace_live = selection != "unchanged"
    if replace_live:
        replacement_started = time.monotonic()
        replacement_inputs = tmp_path / "replacement-inputs"
        replacement = _prepare_application(
            replacement_inputs, tmp_path / "replacement-profile", monkeypatch=monkeypatch,
            uv=uv, interpreter=interpreter, deadline=deadline, replacement=True,
        )
        incoming = json.loads((replacement / "application.json").read_text())["construction"]
        replacement_slot = installation / "versions" / incoming["runtime_version"]
        assert replacement_slot != slot
        shutil.copytree(replacement, replacement_slot)
        shutil.rmtree(replacement_inputs)
        timings["replacement_prepare_seconds"] = time.monotonic() - replacement_started
    shutil.rmtree(inputs)
    global_bin = home / ".local" / "bin"
    launchers = {path: path.read_bytes() for path in global_bin.iterdir()}
    assert launchers

    def assert_current(expected_generation):
        for marker in ("current-version", "last-known-good"):
            assert (installation / marker).read_text().strip() == selected_slot.name
        for generation, marker in completions.items():
            assert (generation / engine.runtime.COMPLETE_MARKER).read_bytes() == marker
        for path, content in launchers.items():
            assert path.read_bytes() == content
        support = _run(
            _frontdoor(global_bin, "--support-json"), cwd=caller, env=env, deadline=deadline,
        )
        assert json.loads(support.stdout)["contract"] == "agent-remote-driver.session-host"
        assert "acp.v1" in json.loads(support.stdout)["transports"]
        selected_python = engine.runtime.resolve_python(installation)
        assert selected_python == engine.runtime.slot_python(installation, selected_slot.name)
        assert selected_python is not None
        _run([
            selected_python, "-I", "-c",
            "import json,sys;from pathlib import Path;import agent_remote_driver_app as app;"
            "assert getattr(app,'_fixture_generation',None) == json.loads(sys.argv[2]);"
            "assert Path(app.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())",
            selected_slot, json.dumps(expected_generation),
        ], cwd=caller, env=env, deadline=deadline)

    assert_current(1)

    def replace_while_live():
        nonlocal selected_slot, install
        assert replacement_slot is not None
        assert (installation / "current-version").read_text().strip() == slot.name
        incoming_install = _frontdoor(replacement_slot, command="install")
        replacement_started = time.monotonic()
        stage = _run([*incoming_install, "--stage"], cwd=caller, env=env, deadline=deadline)
        assert json.loads(stage.stdout)["state"] == "staged"
        assert (installation / "current-version").read_text().strip() == slot.name
        result = _run(incoming_install, cwd=caller, env=env, deadline=deadline)
        assert json.loads(result.stdout)["state"] == "installed"
        assert (installation / "current-version").read_text().strip() == replacement_slot.name
        assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion
        completions[replacement_slot] = (replacement_slot / engine.runtime.COMPLETE_MARKER).read_bytes()
        selected_slot, install = replacement_slot, incoming_install
        assert_current(2)
        timings["live_replacement_seconds"] = time.monotonic() - replacement_started

    def rollback_while_live():
        nonlocal selected_slot, install
        assert selected_slot == replacement_slot
        assert_current(2)
        rollback_started = time.monotonic()
        install = _frontdoor(slot, command="install")
        result = _run(install, cwd=caller, env=env, deadline=deadline)
        assert json.loads(result.stdout)["state"] == "installed"
        selected_slot = slot
        assert_current(1)
        timings["live_rollback_seconds"] = time.monotonic() - rollback_started

    previous_identity = None
    modes = ("cold", "warm", "restored") if selection == "rollback" else ("cold", "warm")
    for mode in modes:
        if mode == "warm":
            repeat_started = time.monotonic()
            repeat = _run(install, cwd=caller, env=env, deadline=deadline)
            assert json.loads(repeat.stdout)["state"] == "installed"
            assert (selected_slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completions[selected_slot]
            timings["retained_install_seconds"] = time.monotonic() - repeat_started
        directory = tmp_path / mode
        directory.mkdir()
        on_live = None
        if replace_live and mode == "cold":
            on_live = replace_while_live
        elif selection == "rollback" and mode == "warm":
            on_live = rollback_while_live
        expected_slot = selected_slot
        observed = await _exercise_installed_host(
            global_bin, caller, env, interpreter, directory, deadline,
            expected_slot=expected_slot,
            expected_generation=2 if expected_slot == replacement_slot else 1,
            on_live=on_live,
        )
        timings[mode + "_launch_to_ready_seconds"] = observed["launch_to_ready_seconds"]
        if mode == "cold":
            timings["cold_delivery_to_ready_seconds"] = observed["ready_at"] - started
        assert observed["child_identity"] != previous_identity
        previous_identity = observed["child_identity"]
        for generation, marker in completions.items():
            assert (generation / engine.runtime.COMPLETE_MARKER).read_bytes() == marker
        assert (installation / "current-version").read_text().strip() == selected_slot.name
    report = json.dumps(timings, sort_keys=True)
    property_name = {
        "unchanged": "driver_application_readiness",
        "replacement": "driver_application_live_replacement",
        "rollback": "driver_application_live_rollback",
    }[selection]
    record_testsuite_property(property_name, report)
    print(property_name + ": " + report, flush=True)


async def _exercise_installed_host(
    slot, caller, env, interpreter, directory, deadline, *,
    expected_slot, expected_generation, on_live=None,
):
    state = directory / "host-state.json"
    generation_output = directory / "host-generation.json"
    child = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':r.get('params')}),flush=True)\n"
    )
    launch_started = time.monotonic()
    process = subprocess.Popen(
        _frontdoor(slot, "--cwd", str(caller), "--state-file", str(state), "--ws-port", "0",
                   "--", interpreter, "-I", "-S", "-u", "-c", child),
        cwd=caller, env={**env, "DRIVER_FIXTURE_GENERATION_OUTPUT": str(generation_output)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    client, child_pid = None, None
    try:
        while not state.exists() and process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.025)
        assert state.exists()
        facts = json.loads(state.read_text())
        child_pid = facts["child_pid"]
        client = await SessionHostClient.connect(port=facts["port"])
        hello = await client.attach(nonce=facts["nonce"].encode())
        ready_at = time.monotonic()
        assert hello.child_pid == child_pid
        generation = json.loads(generation_output.read_text(encoding="utf-8"))
        assert generation["pid"] == facts["pid"]
        assert generation["generation"] == expected_generation
        assert Path(generation["prefix"]).samefile(expected_slot / ".venv")
        for module in ("app", "core"):
            assert Path(generation[module]).resolve().is_relative_to(
                (expected_slot / ".venv").resolve(),
            )
        child_identity = (child_pid, process_start_time(child_pid))
        assert child_identity[1] is not None
        literal = 'first line\n"quoted" & literal $value'
        await client.write((json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {"text": literal},
        }) + "\n").encode())
        cursor, frame = await asyncio.wait_for(anext(client.frames()), 5)
        assert json.loads(frame)["result"] == {"text": literal}
        await client.ack(cursor)
        if on_live is not None:
            host_identity = process_start_time(facts["pid"])
            assert host_identity is not None
            on_live()
            assert process.poll() is None
            assert process_start_time(facts["pid"]) == host_identity
            assert (child_pid, process_start_time(child_pid)) == child_identity
            await client.write((json.dumps({
                "jsonrpc": "2.0", "id": "after-selection", "method": "session/prompt",
                "params": {"text": literal},
            }) + "\n").encode())
            cursor, frame = await asyncio.wait_for(anext(client.frames()), 5)
            assert json.loads(frame) == {
                "jsonrpc": "2.0", "id": "after-selection", "result": {"text": literal},
            }
            await client.ack(cursor)
        await client.close()
        client = None
        websocket = facts["websocket"]
        async with connect(
            websocket["url"], proxy=None,
            subprotocols=["acp.v1", "bearer." + websocket["nonce"]],
        ) as connection:
            await connection.send(json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "session/prompt", "params": {"text": literal},
            }))
            frame = json.loads(await asyncio.wait_for(connection.recv(), 5))
            assert frame == {"jsonrpc": "2.0", "id": 2, "result": {"text": literal}}
        for _ in range(100):
            client = await SessionHostClient.connect(port=facts["port"])
            try:
                restored = await client.attach(nonce=facts["nonce"].encode())
            except ConnectionError:
                await client.close()
                await asyncio.sleep(0.01)
            else:
                break
        else:
            pytest.fail("WS failed to release the installed Host controller")
        assert restored.child_pid == child_pid
        await client.terminate()
        await asyncio.to_thread(process.wait, timeout=10)
        assert process.returncode == 0 and not pid_alive(child_pid)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.terminate()
            await client.close()
        if process.poll() is None:
            kill_pid(process.pid, force=True)
            await asyncio.to_thread(process.wait, timeout=5)
        process.communicate(timeout=5)
        assert child_pid is None or not pid_alive(child_pid)
    return {
        "ready_at": ready_at, "launch_to_ready_seconds": ready_at - launch_started,
        "child_identity": child_identity,
    }
