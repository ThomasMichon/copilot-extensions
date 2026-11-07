"""Normal Manager/engine handoff into the real Source application lifecycle."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import sys
import time

from aiohttp import web
import pytest

from fleet_manager import gateway_host as host
from fleet_manager.planning import canonical
from fleet_manager.private_state import create_private_file
from gateway_service_transport_fixtures import Rig
from test_client_distribution import PROJECT, run
from test_gateway_enrollment import EnrollmentHarness, GRANT
from test_runtime_identity import AUDIENCE
from test_ssh_machine import configured, update

pytestmark = [
    pytest.mark.client_distribution_full,
    pytest.mark.skipif(
        os.environ.get("COPILOT_EXTENSIONS_SSH_SETUP_NATIVE") != "1",
        reason="explicit normal SSH setup lane requires prepared engine dependency support",
    ),
]

def private_json(path, value):
    with os.fdopen(create_private_file(path), "wb") as stream:
        stream.write(canonical(value))


def prepared_cohort(tmp_path, monkeypatch, progress):
    from repository_ssh_cohort_fixture import prepare_cohort

    monkeypatch.syspath_prepend(str(PROJECT.parent / "tools"))
    from test_installer_wheel_acquisition import feeds

    servers = feeds.__wrapped__()
    try:
        return prepare_cohort(tmp_path, monkeypatch, next(servers), progress)
    finally:
        next(servers, None)


def test_normal_source_cohort_preparation(tmp_path_factory, monkeypatch, capfd):
    def progress(message):
        with capfd.disabled():
            print(message, flush=True)

    cohort = prepared_cohort(tmp_path_factory.mktemp("cohort"), monkeypatch, progress)
    assert set(cohort) == {
        "worktree-manager", "engine-base", "agent-worktrees", "fleet-manager", "updated",
    }


def test_normal_manager_engine_ssh_first_touch_repeat_update_and_stop(
    tmp_path_factory, monkeypatch, capfd,
):
    assert os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1", (
        "normal SSH application lifetime requires the explicit ephemeral native owner"
    )
    def progress(message):
        with capfd.disabled():
            print(message, flush=True)

    if sys.platform == "linux":
        from agent_procutil.linux_daemon_scope import caller_unit

        progress("native lifetime prerequisites: " + json.dumps({
            "caller_cleanup_unit": caller_unit() is not None,
            "runtime_bus": (Path(os.environ["XDG_RUNTIME_DIR"]) / "bus").is_socket(),
            "native_user_bus": (Path("/run/user") / str(os.getuid()) / "bus").is_socket(),
            "bus_address_selected": bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
        }, sort_keys=True))
    tmp_path = tmp_path_factory.mktemp("n")
    locator = os.environ.get("COPILOT_EXTENSIONS_SSH_SETUP_COHORT")
    assert locator, "prepare the complete Source cohort before the indivisible native lifecycle"
    cohort = {key: Path(value) for key, value in json.loads(Path(locator).read_text()).items()}
    monkeypatch.syspath_prepend(str(PROJECT.parent / "tools"))
    import local_install_profile as profiles

    def application_path(component):
        snapshot = cohort[component]
        selected = profiles.source_inputs.select_project(
            snapshot, {"updated": "fleet-manager", "engine-base": "agent-worktrees"}.get(
                component, component,
            ),
        )
        return snapshot / selected.project["application"]

    application, updated = application_path("fleet-manager"), application_path("updated")
    root, home, inputs, harness = (tmp_path / name for name in ("i", "home", "inputs", "harness"))
    for directory in (home, inputs, harness):
        directory.mkdir(mode=0o700)
    value, policy, directory = configured(inputs)
    Path(value.pop("proof_key_file")).unlink()
    Path(value.pop("access_token_file")).unlink()
    value.update(version=3, namespace="/api", authentication={
        "profile": "machine-key-v1", "principal": "new-caller",
        "audience": AUDIENCE, "resource": "service-a",
    })
    request, source = inputs / "request.json", inputs / "continuity.json"
    url = "https://example.invalid/team/harness.git"
    state = home / (".fleet-continuity-" + hashlib.sha256(
        json.dumps([url, "harness-a"], separators=(",", ":")).encode(),
    ).hexdigest())
    clean = {
        name: item for name, item in os.environ.items()
        if not name.startswith("UV_")
        and name not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "FLEET_MANAGER_HOST_NONCE"}
    }
    clean.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        LOCALAPPDATA=str(home / "local"), APPDATA=str(home / "roaming"),
        UV_OFFLINE="1", UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never", UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        FLEET_MANAGER_HOME=str(root), AGENT_WORKTREES_STATUS_MONITOR="0",
    )
    if os.name == "nt":
        clean.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    def install_engine(component):
        source = profiles.source_inputs.select_project(cohort[component], "agent-worktrees")
        construction = json.loads(
            (application_path(component) / "application.json").read_text(),
        )["construction"]
        with monkeypatch.context() as installation:
            for name in set(os.environ) - clean.keys():
                installation.delenv(name)
            for name, item in clean.items():
                installation.setenv(name, item)
            profiles.source_inputs.install_application(
                cohort[component], "agent-worktrees", source.payload,
                home / ".agent-worktrees", uv=Path(shutil.which("uv")),
                base_python=Path(sys.executable), health_module="agent_worktrees",
                expected_version=construction["runtime_version"],
                expected_hash=construction["payload_hash"],
            )
        engine_root = home / ".agent-worktrees"
        slot = engine_root / "versions" / (engine_root / "current-version").read_text().strip()
        python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        observed = run([
            profiles.source_inputs.engine._native_retained_path(python), "-I", "-B", "-c",
            "import importlib.util,json,sys;from pathlib import Path;import agent_worktrees;"
            "sdk=importlib.util.find_spec('fleet_manager');"
            "print(json.dumps({'prefix':sys.prefix,'engine':agent_worktrees.__file__,"
            "'sdk':sdk.origin if sdk else None,'paths':sys.path}))",
        ], cwd=home, env=clean, timeout=15)
        assert observed.returncode == 0, observed.stdout + observed.stderr
        facts = json.loads(observed.stdout)
        assert Path(facts["prefix"]).resolve() == (slot / ".venv").resolve()
        assert Path(facts["engine"]).resolve().is_relative_to(slot.resolve())
        assert (facts["sdk"] is not None) is (component == "agent-worktrees")
        if facts["sdk"] is not None:
            assert Path(facts["sdk"]).resolve().is_relative_to(slot.resolve())
        assert all(
            not Path(path).resolve().is_relative_to(PROJECT.parent.resolve())
            for path in facts["paths"] if path
        )

    install_engine("engine-base")
    manager_snapshot = cohort["worktree-manager"]
    manager_source = profiles.source_inputs.select_project(manager_snapshot, "worktree-manager")
    installed = run(
        [sys.executable, "-I", "-B", str(manager_source.payload / "scripts" / "runtime_install.py"),
         "--root", str(home / ".worktree-manager"), "--install-source", str(manager_snapshot),
         "--", "--help"],
        cwd=home, env=clean, timeout=60,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    launcher = home / ".local" / "bin" / (
        "worktree-manager.ps1" if os.name == "nt" else "worktree-manager"
    )
    assert launcher.is_file()
    front_door = (
        [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(launcher)]
        if os.name == "nt" else [str(launcher)]
    )
    inert = run([*front_door, "setup", "--apply"], cwd=home, env=clean, timeout=30)
    assert inert.returncode == 0, inert.stdout + inert.stderr
    assert not state.exists() and not root.exists()

    def git(*arguments):
        result = run(["git", "-C", str(harness), *arguments], env=clean)
        assert result.returncode == 0, result.stderr

    (harness / ".agent-worktrees").mkdir()
    declaration = {
        "version": 3,
        "entrypoints": {
            "windows": {"runtime": "powershell", "script": "setup.ps1"},
            "posix": {"runtime": "bash", "script": "setup.sh"},
        },
        "contexts": {"harness-a": {
            "provider": "local-projection-v1", "source": str(source), "ssh_setup": str(request),
        }},
    }
    (harness / ".agent-worktrees" / "repository-setup.json").write_bytes(canonical(declaration))
    writer = (
        "import json,pathlib,sys;r=json.loads(pathlib.Path(sys.argv[1]).read_text());"
        "pathlib.Path(r['result_file']).write_text(json.dumps("
        "{'version':1,'status':'ready','message':'Synthetic repository core ready'}))"
    )
    (harness / "setup.sh").write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I -c " + shlex.quote(writer) + ' "$2"\n',
    )
    (harness / "setup.ps1").write_text(
        "param([string]$RequestFile)\n& '" + sys.executable.replace("'", "''")
        + "' -I -c '" + writer.replace("'", "''") + "' $RequestFile\nexit $LASTEXITCODE\n",
    )
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("remote", "add", "origin", url)
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic setup", "--no-verify")

    def selected_request(application):
        current_snapshot = next(
            parent for parent in application.parents if (parent / "install-source.json").is_file()
        )
        construction = json.loads((application / "application.json").read_text())["construction"]
        provenance = json.loads((current_snapshot / "install-source.json").read_text())
        return {
            "schema": "fleet-manager.continuity-ssh-setup", "version": 1,
            "snapshot": str(current_snapshot), "root": str(root),
            "selection": {
                name: provenance[name] for name in ("source_commit", "source_tree")
            } | {name: construction[name] for name in ("runtime_version", "payload_hash")},
            "authority": "authority-a", "installation": "installation-a", "ssh": value,
        }

    async def manager():
        started = time.monotonic()
        result = await asyncio.to_thread(
            run, [*front_door, "setup", "--apply", "--repository", url,
                  "--repository-target", str(harness),
                  "--trust-repository-setup", "--context", "harness-a"],
            cwd=home, env=clean, timeout=90,
        )
        progress(f"normal Manager/engine setup: {time.monotonic() - started:.2f}s")
        return result

    async def control(action):
        version = (root / "current-version").read_text().strip()
        python = root / "versions" / version / ".venv" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        result = await asyncio.to_thread(
            run, [str(python), "-I", "-B", "-m", "fleet_manager", "gateway-host", action],
            cwd=home, env=clean, timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    gateway = tmp_path / "gateway"
    gateway.mkdir()
    rig, enrollment = Rig(gateway), EnrollmentHarness(gateway)
    now = int(time.time())
    enrollment.scope.update(machine=value["machine"], expires_at=now + 1800)
    enrollment.delegation["grant_expires_at"] = now + 900
    enrollment.document["policy"]["manifest"]["revision"] += 1
    rig.h.document = enrollment.document
    rig.h.clock = lambda: int(time.time())

    async def handle(incoming):
        assert incoming.method == "GET" and incoming.path == "/directory"
        if incoming.query["after"] == "0":
            return web.Response(body=update(policy, directory).encoded)
        await asyncio.sleep(int(incoming.query["wait"]))
        return web.Response(status=204)

    rig.backends[0].handle = handle

    async def exercise():
        from fleet_manager.gateway_service_connector import ServiceConnector

        stopped = asyncio.Event()
        try:
            await rig.start(enrollment_journal=enrollment.ledger)
            enrollment.scope["destination"] = rig.origin
            enrollment.document["policy"]["manifest"]["revision"] += 1
            rig.save()
            await rig.state.reload()
            value.update(gateway_origin=rig.origin, gateway_ca=rig.ca)
            private_json(source, {
                "schema": "fleet-manager.continuity-projection", "version": 1,
                "provider": "local-projection-v1", "authority": "authority-a",
                "audience": AUDIENCE, "delegation": enrollment.delegation,
                "grant": GRANT, "gateway_ca": rig.ca,
            })
            private_json(request, selected_request(application))
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin, backend_origin=rig.origins[0],
                proof_key=enrollment.h.keys[1], gateway_ca=rig.ca,
                machine_principal="service-a", machine_audience=AUDIENCE,
            )
            rig.tasks.append(asyncio.create_task(connector.run(stopped)))
            await asyncio.wait_for(connector.connected.wait(), 3)
            assert not state.exists() and not root.exists()
            missing = await manager()
            assert missing.returncode != 0, missing.stdout + missing.stderr
            assert not state.exists() and not root.exists()
            with sqlite3.connect(enrollment.ledger.path) as connection:
                assert connection.execute("SELECT count(*) FROM machine_enrollments").fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM operations").fetchone() == (0,)
            await asyncio.to_thread(install_engine, "agent-worktrees")
            first = await manager()
            if first.returncode and (root / "current-version").exists():
                log = root / host.STATE / "host.log"
                if log.is_file():
                    with log.open("rb") as stream:
                        content = stream.read(16384)
                    progress("post-install native log markers: " + json.dumps({
                        name: marker in content
                        for name, marker in {
                            "bus_connection_failure": b"Failed to connect",
                            "missing_runtime_endpoint": b"No medium found",
                            "systemd_scope": b"systemd",
                            "permission_refused": b"Permission denied",
                            "traceback": b"Traceback",
                        }.items()
                    }, sort_keys=True))
                try:
                    observed = await control("status")
                except AssertionError:
                    progress("post-install observation: selected host status refused")
                else:
                    progress("post-install observation: " + json.dumps({
                        name: observed.get(name)
                        for name in ("state", "ready", "selected", "revision", "native_exit_confirmed")
                    }, sort_keys=True))
            assert first.returncode == 0, first.stdout + first.stderr
            assert "converged its authenticated directory" in first.stdout
            initial = await control("status")
            assert initial["ready"] and initial["selected"] and initial["revision"] == directory["revision"]
            retained = state / "machine.json"
            original = retained.read_bytes(), retained.stat().st_mtime_ns
            selected = (root / "current-version").read_bytes()
            assert (await manager()).returncode == 0
            assert (await control("status"))["pid"] == initial["pid"]
            valid = request.read_bytes()
            wrong = json.loads(valid)
            wrong["ssh"]["authentication"]["principal"] = "other-principal"
            request.write_bytes(canonical(wrong))
            assert (await manager()).returncode != 0
            assert (root / "current-version").read_bytes() == selected
            request.write_bytes(valid)
            assert (await control("status"))["pid"] == initial["pid"]
            request.write_bytes(canonical(selected_request(updated)))
            changed = await manager()
            assert changed.returncode == 0, changed.stdout + changed.stderr
            incoming = await control("status")
            assert incoming["ready"] and incoming["selected"] and incoming["pid"] != initial["pid"]
            assert (retained.read_bytes(), retained.stat().st_mtime_ns) == original
            assert (await control("stop"))["native_exit_confirmed"]
            restarted = await manager()
            assert restarted.returncode == 0, restarted.stdout + restarted.stderr
            resumed = await control("status")
            assert resumed["ready"] and resumed["selected"] and resumed["pid"] != incoming["pid"]
            assert (retained.read_bytes(), retained.stat().st_mtime_ns) == original
            with sqlite3.connect(enrollment.ledger.path) as connection:
                assert connection.execute("SELECT count(*) FROM machine_enrollments").fetchone() == (1,)
                assert connection.execute("SELECT count(*) FROM operations").fetchone() == (1,)
            assert not (root / "proof.pem").exists() and not (root / "access").exists()
            assert (await control("stop"))["native_exit_confirmed"]
        finally:
            try:
                if (root / host.STATE / host.RUNTIME).exists():
                    await control("stop")
            finally:
                stopped.set()
                await rig.close()

    asyncio.run(exercise())
