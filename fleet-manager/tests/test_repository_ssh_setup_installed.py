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
import zipfile

from aiohttp import web
import pytest

from fleet_manager import gateway_host as host
from fleet_manager.planning import canonical
from fleet_manager.private_state import create_private_file
from gateway_service_transport_fixtures import Rig
from test_client_distribution import PROJECT, build, run
from test_gateway_enrollment import EnrollmentHarness, GRANT
from test_gateway_host_distribution import prepared_application
from test_runtime_identity import AUDIENCE
from test_ssh_machine import configured, update

pytestmark = [
    pytest.mark.client_distribution_full,
    pytest.mark.skipif(
        os.environ.get("COPILOT_EXTENSIONS_SSH_SETUP_NATIVE") != "1",
        reason="explicit normal SSH setup lane requires prepared engine dependency support",
    ),
]

ENGINE = r"""
import json, site, sys
from pathlib import Path
context = json.loads(sys.argv.pop(1))
sys.path.insert(0, context["engine"])
for path in context["support"]:
    site.addsitedir(path)
from agent_worktrees import repository_ssh_setup, repos, repository_bootstrap
assert Path(repository_ssh_setup.__file__).resolve().is_relative_to(Path(context["engine"]))
installer = repository_ssh_setup._installer()
assert Path(installer.__file__).resolve().is_relative_to(Path(context["engine"]))
repos._repos_yaml_path = lambda: Path(context["registry"])
assert sys.argv[1:3] == ["repos", "setup"]
raise SystemExit(repository_bootstrap.command(sys.argv[3:]))
"""

MANAGER = r"""
import json, site, sys
context = json.loads(sys.argv.pop(1))
for path in context["support"]:
    site.addsitedir(path)
sys.path.insert(0, context["manager"])
from worktree_manager import engine_client, repository_bootstrap
engine_client.set_engine_command([
    sys.executable, "-I", "-B", "-c", context["driver"], json.dumps(context),
])
raise SystemExit(repository_bootstrap.run(repository_bootstrap.parse(sys.argv[1:])))
"""


def engine_wheel(tmp_path):
    source = tmp_path / "engine-source"
    project = source / "plugins" / "agent-worktrees"
    original = PROJECT.parent / "plugins" / "agent-worktrees"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "setup.py"):
        shutil.copyfile(original / name, project / name)
    for name in ("src", "scripts"):
        shutil.copytree(
            original / name, project / name,
            ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info"),
        )
    shutil.copytree(
        PROJECT.parent / "libs" / "versioned-runtime", source / "libs" / "versioned-runtime",
        ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info"),
    )
    output = tmp_path / "engine-wheel"
    result = build(project, output)
    assert result.returncode == 0, result.stdout + result.stderr
    installed = tmp_path / "engine"
    with zipfile.ZipFile(next(output.glob("*.whl"))) as archive:
        archive.extractall(installed)
    for name in ("repository_continuity.py", "repository_setup.py", "repository_ssh_setup.py"):
        assert (installed / "agent_worktrees" / name).read_bytes() == (
            original / "src" / "agent_worktrees" / name
        ).read_bytes()
    return installed


def private_json(path, value):
    with os.fdopen(create_private_file(path), "wb") as stream:
        stream.write(canonical(value))


def test_normal_manager_engine_ssh_first_touch_repeat_update_and_stop(
    tmp_path_factory, wheel, monkeypatch, capfd,
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
    prepared = os.environ.get("COPILOT_EXTENSIONS_TEST_VENV_ROOT")
    assert prepared, "normal SSH lane requires the runner's explicit environment locator"
    engine_python = Path(prepared) / "agent-worktrees" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    assert engine_python.is_file(), "prepare the engine dependency environment before this lane"
    support = run(
        [str(engine_python), "-I", "-c", "import json,sysconfig;print(json.dumps(sorted({"
         "sysconfig.get_path('purelib'),sysconfig.get_path('platlib')})))"],
        timeout=15,
    )
    assert support.returncode == 0, support.stderr
    engine = engine_wheel(tmp_path)
    snapshot, application, updated = prepared_application(
        tmp_path, wheel, monkeypatch, progress, extras="gateway-host,ssh-client",
    )
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
    context = {
        "engine": str(engine), "support": json.loads(support.stdout),
        "manager": str(PROJECT.parent / "worktree-manager" / "src"),
        "registry": str(home / "repos.yaml"), "driver": ENGINE,
    }
    clean = {
        name: item for name, item in os.environ.items()
        if not name.startswith("UV_")
        and name not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "FLEET_MANAGER_HOST_NONCE"}
    }
    clean.update(
        HOME=str(home), USERPROFILE=str(home), UV_OFFLINE="1", UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never", UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        FLEET_MANAGER_HOME=str(root),
    )
    if os.name == "nt":
        clean.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])

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
            run, [sys.executable, "-I", "-B", "-c", MANAGER, json.dumps(context),
                  "--apply", "--repository", url, "--repository-target", str(harness),
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
