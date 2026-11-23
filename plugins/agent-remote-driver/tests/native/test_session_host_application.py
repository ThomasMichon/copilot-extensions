"""First-touch application construction through the existing build/install owners."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, cast

from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
from agent_procutil.owned_process import require_contained_worker, run_caller_owned
from agent_procutil.daemon_observation import observe_instances
import pytest
from websockets.asyncio.client import connect
from websockets.typing import Subprotocol

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.osutil import kill_pid, pid_alive, process_start_time

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "plugins" / "agent-remote-driver"


def _run(
    arguments: Sequence[str | Path], *, cwd: Path, env: dict[str, str], deadline: float,
) -> subprocess.CompletedProcess[Any]:
    result = run_caller_owned(
        list(map(str, arguments)), cwd=cwd, env=env,
        timeout=max(0.01, deadline - time.monotonic()),
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=512 * 1024,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _frontdoor(
    slot: Path, *arguments: str, command: str = "agent-remote-driver",
) -> list[str]:
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        assert shell is not None
        return [
            shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(slot / f"{command}.ps1"), *arguments,
        ]
    return [str(slot / command), *arguments]


def _managed_python(
    environment: dict[str, str], *, directory: Path, uv: str, deadline: float,
) -> dict[str, str]:
    helper = ROOT / "plugins/agent-worktrees/tests/native/application_python_fixture.py"
    spec = importlib.util.spec_from_file_location("_driver_application_python_fixture", helper)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(dict[str, str], module.managed_python_environment(
        environment, directory=directory, uv=uv, deadline=deadline, run=_run,
    ))


def _prepare_application(
    inputs: Path, profile: Path, *, monkeypatch: pytest.MonkeyPatch, uv: str,
    interpreter: str, deadline: float, replacement: bool = False,
) -> Path:
    import build_python_artifacts as builder
    import materialize_main as materializer
    import local_install_profile as profiles
    import local_install_source as local
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
    for origin in local._build_input_roots(ROOT, ("agent-remote-driver",), builder):
        if origin != SOURCE:
            shutil.copytree(
                origin, inputs / origin.relative_to(ROOT),
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
        "            'core': sys.modules[host_main.__module__].__file__,\n"
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
            out_dir=artifacts, python=interpreter, toolchain=None, uv=uv, deadline=deadline,
        )
    project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
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


def _installed_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, caller_jobs: bool = False,
    recover: bool = False, public_stop: bool = False, removal_preflight: bool = False,
    service_retirement: bool = False, removal_transaction: bool = False,
    public_removal: bool = False,
) -> None:
    from agent_procutil.daemon_instances import (
        DaemonLeasePolicy, InstanceCleanupResult, REGISTRY_ENV,
        daemon_test_lifecycle, default_registry_root,
    )
    from agent_remote_driver_app import service_lifecycle as lifecycle

    if caller_jobs:
        import agent_procutil

        assert os.name == "nt" and not agent_procutil.contained_test_mode()
        assert os.environ.get("COPILOT_EXTENSIONS_NATIVE_DRIVER_SERVICE") == "1"
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select an existing native Copilot executable for application installation")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    sys.path.insert(0, str(ROOT / "tools"))
    from governed_feed_trust import _governed_feed_configured

    if not _governed_feed_configured():
        pytest.skip("native profile qualification requires the configured governed feed")
    uv = shutil.which("uv")
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert uv and interpreter
    deadline = time.monotonic() + 270
    inputs, installation = tmp_path / "source-inputs", tmp_path / "installation"
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if (key.startswith("COPILOT_EXTENSIONS_")
            or not key.startswith(("COPILOT_", "AGENT_", "UV_", "PYTHON", "WORKTREE_")))
        and key not in {"VIRTUAL_ENV", "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(home / ".copilot"),
        COPILOT_CACHE_HOME=str(tmp_path / "copilot-cache"), COPILOT_OFFLINE="true",
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1",
        PATH=str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    )
    environment[REGISTRY_ENV] = str(tmp_path / "instances")
    environment = _managed_python(
        environment, directory=tmp_path / "managed-python", uv=uv, deadline=deadline,
    )
    _run(
        [uv, "python", "find", "--managed-python", ".".join(map(str, sys.version_info[:3]))],
        cwd=caller, env=environment, deadline=deadline,
    )
    application = _prepare_application(
        inputs, tmp_path / "profile", monkeypatch=monkeypatch, uv=uv,
        interpreter=interpreter, deadline=deadline,
    )
    declaration = json.loads((application / "application.json").read_text())
    assert declaration["setup"] == [] and declaration["start"] == ["__source-start"]
    slot = installation / "versions" / declaration["construction"]["runtime_version"]
    shutil.copytree(application, slot)
    cleanup: list[InstanceCleanupResult] = []
    recovered = None
    with daemon_test_lifecycle(
        owner="driver-installed-service-fixture", registry_root=tmp_path / "instances",
        lease=DaemonLeasePolicy(ttl_seconds=30, max_lifetime_seconds=300),
        deadline=deadline + 30, cleanup_results=cleanup,
    ) as context:
        environment.update(context)
        with monkeypatch.context() as patch:
            for name, value in context.items():
                patch.setenv(name, value)
            patch.setenv(REGISTRY_ENV, environment[REGISTRY_ENV])
            assert default_registry_root().resolve() == (tmp_path / "instances").resolve()
            try:
                install = _frontdoor(slot, command="install")
                if caller_jobs:
                    stage = _run(
                        [*install, "--stage"], cwd=caller, env=environment, deadline=deadline,
                    )
                    assert json.loads(stage.stdout)["state"] == "staged"
                    assert not (installation / "current-version").exists()
                    assert lifecycle.endpoints(installation) == []
                    helper = ROOT / "plugins/agent-worktrees/tests/native/public_caller_jobs_fixture.py"
                    spec = importlib.util.spec_from_file_location("_driver_installer_jobs", helper)
                    assert spec is not None and spec.loader is not None
                    owner = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(owner)
                    result = owner.run_public_caller_jobs(
                        install, python=slot / ".venv" / "Scripts" / "python.exe",
                        cwd=caller, environment=environment, directory=tmp_path / "caller-jobs",
                        deadline=deadline,
                    )
                else:
                    require_contained_worker()
                    assert environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
                    # Keep the enclosing test Job until observation and exact cleanup finish.
                    result = subprocess.run(
                        install, cwd=caller, env=environment,
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                        timeout=max(.01, deadline - time.monotonic()), **no_window_kwargs(),
                    )
                assert result.returncode == 0, result.stdout + result.stderr
            except (AssertionError, subprocess.SubprocessError):
                log = installation / "logs" / "driver-service.log"
                if log.is_file():
                    with log.open("rb") as stream:
                        stream.seek(max(0, log.stat().st_size - 16_384))
                        print("Original fixture service log:\n" + stream.read().decode(
                            "utf-8", errors="replace",
                        ), flush=True)
                raise
            assert json.loads(result.stdout)["state"] == "installed"
            assert (installation / "current-version").read_text().strip() == slot.name
            observed = lifecycle.endpoints(installation, generation=slot.name)
            assert len(observed) == 1 and lifecycle.healthy(observed[0], timeout=3)
            original = observed[0]
            shutil.rmtree(inputs)
            support = _run(
                _frontdoor(home / ".local" / "bin", "--support-json"),
                cwd=caller, env=environment, deadline=deadline,
            )
            assert json.loads(support.stdout)["contract"] == "agent-remote-driver.session-host"
            assert lifecycle.endpoints(installation, generation=slot.name) == [original]
            assert lifecycle.healthy(original, timeout=3)
            catalog = installation / "connector-marketplace" / ".github" / "plugin" / "marketplace.json"
            assert catalog.is_file()
            installed = json.loads(_run(
                [executable, "plugin", "list", "--json"], cwd=caller,
                env=environment, deadline=deadline,
            ).stdout)
            assert any(
                item["name"] == "agent-remote-driver" and item["enabled"] is True
                and item["marketplace"] == "copilot-driver-standalone"
                for item in installed
            )
            packages = slot / ".venv" / ("Lib/site-packages" if os.name == "nt" else (
                f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
            ))
            assert not any((packages / name).exists() for name in (
                "agent_worktrees", "worktree_manager", "agent_bridge", "agent_dispatch",
            ))
            if recover or removal_preflight:
                require_contained_worker()

                def remove_application(*, blocked: bool) -> None:
                    if public_removal:
                        workspace = tmp_path / "public-removal"
                        arguments = [
                            "uninstall", "--workspace", str(workspace),
                            "--instance-id", original.instance_id,
                        ]
                        command = (
                            _frontdoor(home / ".local" / "bin", *arguments)
                            if slot.exists() else
                            [interpreter, "-I", "-B", str(workspace / "packet" / "install.py"),
                             *arguments]
                        )
                        result = run_caller_owned(
                            command, cwd=caller, env=environment,
                            timeout=max(.01, deadline - time.monotonic()),
                            max_stdout_bytes=128 * 1024, max_stderr_bytes=128 * 1024,
                        )
                        if blocked:
                            assert result.returncode != 0
                            assert (
                                "RuntimeReferenceHeld" in result.stderr
                                or "RetentionTimeout" in result.stderr
                            ), result.stdout + result.stderr
                        else:
                            assert result.returncode == 0, result.stdout + result.stderr
                            assert json.loads(result.stdout)["state"] == "removed"
                        for phase in ("service", "connector"):
                            assert json.loads(
                                (workspace / "journal" / f"{phase}.intent.json").read_bytes(),
                            ) == json.loads(
                                (workspace / "journal" / f"{phase}.settled.json").read_bytes(),
                            )
                        return
                    program = """
import importlib.machinery,json,pathlib,sys,time
packet,journal,home=map(pathlib.Path,sys.argv[1:4])
instance,executable=sys.argv[4:6]
blocked=json.loads(sys.argv[6])
sys.path[:0]=[str(path) for path in sorted((packet/'wheels').glob('*.whl'))]
from agent_remote_driver_app import application_removal
assert pathlib.Path(application_removal.__file__).is_relative_to(packet)
try:
    result=application_removal.remove(
        packet,journal,instance,deadline=time.monotonic()+45,home=home,executable=executable,
    )
except TimeoutError as error:
    assert blocked and type(error).__name__=='RetentionTimeout',repr(error)
    state='retained'
except application_removal.RuntimeReferenceHeld:
    assert blocked
    state='retained'
else:
    assert not blocked and result['state']=='removed'
    state='removed'
for phase in ('service','connector'):
    assert json.loads((journal/(phase+'.intent.json')).read_bytes())==json.loads(
        (journal/(phase+'.settled.json')).read_bytes()
    )
extensions=[
    module for module in list(sys.modules.values())
    if isinstance(getattr(module,'__loader__',None),importlib.machinery.ExtensionFileLoader)
]
assert all(
    pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(sys.base_prefix).resolve())
    for module in extensions
)
print(json.dumps({'state':state,'target_native_extensions':0}))
"""
                    result = _run(
                        [interpreter, "-I", "-B", "-c", program, tmp_path / "retained-removal",
                         tmp_path / "removal-transaction", home / ".copilot",
                         original.instance_id, executable, json.dumps(blocked)],
                        cwd=caller, env=environment, deadline=deadline,
                    )
                    assert json.loads(result.stdout) == {
                        "state": "retained" if blocked else "removed", "target_native_extensions": 0,
                    }

                def check_removal_custody(*, blocked: bool) -> None:
                    program = """
import importlib.util,json,pathlib,sys,time
helper,slot,commands=map(pathlib.Path,sys.argv[1:4])
blocked=json.loads(sys.argv[4])
spec=importlib.util.spec_from_file_location('external_application_removal',helper)
assert spec is not None and spec.loader is not None
provider=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=provider
spec.loader.exec_module(provider)
plan=provider.plan_application_removal(
    slot,component='agent-remote-driver',global_bin=commands,deadline=time.monotonic()+15,
)
assert plan.slot == slot and plan.component == 'agent-remote-driver'
try:
    with provider.application_removal_custody(plan,deadline=time.monotonic()+15):
        assert not blocked, 'live Host did not protect its installed runtime'
except TimeoutError as error:
    assert blocked and type(error).__name__ == 'RetentionTimeout', repr(error)
    state='retained'
else:
    state='quiescent'
assert all(path.read_bytes() == content for path,content in plan.publications)
print(json.dumps({'state':state,'generation':plan.slot.name}))
"""
                    result = _run(
                        [interpreter, "-I", "-B", "-c", program, application / "install.py",
                         slot, home / ".local" / "bin", json.dumps(blocked)],
                        cwd=caller, env=environment, deadline=deadline,
                    )
                    assert json.loads(result.stdout) == {
                        "state": "retained" if blocked else "quiescent", "generation": slot.name,
                    }

                def stop_for_removal() -> None:
                    if service_retirement:
                        from single_instance_lease.operation import OperationLock, OperationLockedError

                        records = [
                            item for item in observe_instances(service=lifecycle.SERVICE)
                            if item.get("instance_id") == original.instance_id
                        ]
                        assert len(records) == 1
                        artifact = Path(records[0][lifecycle.LOCATOR])
                        assert artifact.samefile(
                            slot / ".runtime-bootstrap"
                            / f"driver-service-{original.instance_id}.json"
                        )
                        assert artifact.is_file() and not (slot / "service").exists()
                        if public_removal:
                            shutil.rmtree(tmp_path / "profile")
                            remove_application(blocked=True)
                            assert not artifact.exists()
                            assert (installation / "current-version").read_text().strip() == slot.name
                            assert lifecycle.endpoints(installation, generation=slot.name) == []
                            return
                        packet_program = """
import importlib.util,json,pathlib,sys,time
helper,packet,slot,commands,artifact=map(pathlib.Path,sys.argv[1:6])
instance=sys.argv[6]
spec=importlib.util.spec_from_file_location('external_packet_preparation',helper)
assert spec is not None and spec.loader is not None
provider=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=provider
spec.loader.exec_module(provider)
deadline=time.monotonic()+45
plan=provider.plan_application_removal(
    slot,component='agent-remote-driver',global_bin=commands,deadline=deadline,
)
provider.prepare_application_removal(
    plan,packet,deadline=deadline,pending_artifacts=(artifact,),
)
spec=importlib.util.spec_from_file_location('retained_packet_controls',packet/'install.py')
assert spec is not None and spec.loader is not None
provider=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=provider
spec.loader.exec_module(provider)
assert provider.read_application_removal(packet,deadline=deadline)==plan
sys.path[:0]=[str(path) for path in sorted((packet/'wheels').glob('*.whl'))]
from agent_remote_driver_app import service_lifecycle
assert pathlib.Path(service_lifecycle.__file__).is_relative_to(packet)
observed=service_lifecycle.endpoints(slot.parent.parent,generation=slot.name)
assert len(observed)==1 and observed[0].instance_id==instance
assert service_lifecycle.healthy(observed[0],timeout=3)
record=json.loads((packet/'removal-packet.json').read_bytes())
receipt=json.loads((packet/'original-cleanup.json').read_bytes())
name='.runtime-bootstrap/driver-service-'+instance+'.json'
assert name in record['pending_artifacts'] and name not in receipt['entries']
assert artifact.is_file()
print(json.dumps({'packet':'retained','service':'live','original_artifact':'excluded'}))
"""
                        prepared = _run(
                            [interpreter, "-I", "-B", "-c", packet_program,
                             application / "install.py", tmp_path / "retained-removal",
                             slot, home / ".local" / "bin", artifact, original.instance_id],
                            cwd=caller, env=environment, deadline=deadline,
                        )
                        assert json.loads(prepared.stdout) == {
                            "packet": "retained", "service": "live",
                            "original_artifact": "excluded",
                        }
                        if removal_transaction:
                            shutil.rmtree(tmp_path / "profile")
                            remove_application(blocked=True)
                            assert not artifact.exists()
                            assert (installation / "current-version").read_text().strip() == slot.name
                            assert not (
                                installation / "connector-marketplace" / "service-binding.json"
                            ).exists()
                            assert lifecycle.endpoints(installation, generation=slot.name) == []
                            return
                        binding = SimpleNamespace(root=installation, slot=slot)
                        with lifecycle.service_removal_custody(
                            binding, original.instance_id, deadline=deadline,
                        ) as stopped:
                            assert stopped == original
                            assert not artifact.exists()
                            assert all(
                                item.get("instance_id") != original.instance_id
                                for item in observe_instances(service=lifecycle.SERVICE)
                            )
                            with pytest.raises(OperationLockedError):
                                with OperationLock(installation / "driver-service-start.lock"):
                                    pytest.fail("installed service start custody was released")
                            check_removal_custody(blocked=True)
                        return
                    stopped = json.loads(_run(
                        _frontdoor(
                            home / ".local" / "bin", "service", "stop",
                            "--instance-id", original.instance_id,
                        ), cwd=caller, env=environment, deadline=deadline,
                    ).stdout)
                    assert stopped["state"] == "stopped" and stopped["pid"] == original.pid
                    assert lifecycle.endpoints(installation, generation=slot.name) == []
                    check_removal_custody(blocked=True)

                def recover_service() -> None:
                    nonlocal recovered
                    global_bin = home / ".local" / "bin"
                    status = json.loads(_run(
                        _frontdoor(global_bin, "service", "status"),
                        cwd=caller, env=environment, deadline=deadline,
                    ).stdout)
                    assert status["state"] == "running"
                    assert status["instance_id"] == original.instance_id
                    assert process_start_time(original.pid) == original.birth
                    if public_stop:
                        for cycle in range(2):
                            stopped = json.loads(_run(
                                _frontdoor(
                                    global_bin, "service", "stop",
                                    "--instance-id", original.instance_id,
                                ), cwd=caller, env=environment, deadline=deadline,
                            ).stdout)
                            assert stopped["state"] == "stopped"
                            assert stopped["instance_id"] == original.instance_id
                            assert stopped["pid"] == (original.pid if cycle == 0 else None)
                    else:
                        from agent_procutil.process_retirement import PinnedProcess

                        owners = [
                            record for record in observe_instances(service=lifecycle.SERVICE)
                            if record.get("instance_id") == original.instance_id
                            and int(record["pid"]) == original.pid
                            and Path(record["runtime_root"]).samefile(slot)
                            and record.get("process_identity_state") == "matching"
                        ]
                        assert len(owners) == 1
                        with PinnedProcess(original.pid, owners[0]["birth"]) as owned:
                            owned.terminate(timeout=3)
                            assert owned.exited()
                    assert lifecycle.endpoints(installation, generation=slot.name) == []
                    assert not (
                        pid_alive(original.pid) and process_start_time(original.pid) == original.birth
                    )
                    marketplace = installation / "connector-marketplace"
                    module = (
                        marketplace / "generations" / slot.name / "extensions" / "agent-remote-driver"
                    )
                    script = (
                        "import { ensureInstalledService } from "
                        + json.dumps((module / "service-startup.mjs").as_uri()) + ";"
                        "const result = await ensureInstalledService({bindingPath: process.argv[1],"
                        "onFailure: error => process.stderr.write(String(error.stderr || error))});"
                        "console.log(JSON.stringify(result));"
                        "process.exitCode = result.state === 'ensured' ? 0 : 1;"
                    )
                    node = shutil.which("node")
                    assert node is not None
                    command = [
                        node, "--input-type=module", "-e", script,
                        str(marketplace / "service-binding.json"),
                    ]
                    for cycle in range(2):
                        # The enclosing Job owns recovery, probes and the independent Host.
                        result = subprocess.run(
                            command, cwd=caller, env=environment, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=max(.01, deadline - time.monotonic()), **no_window_kwargs(),
                        )
                        assert result.returncode == 0, result.stdout + result.stderr
                        assert json.loads(result.stdout)["state"] == "ensured"
                        current = lifecycle.endpoints(installation, generation=slot.name)
                        assert len(current) == 1 and lifecycle.healthy(current[0], timeout=3)
                        if cycle == 0:
                            recovered = current[0]
                            assert recovered.instance_id != original.instance_id
                        else:
                            assert current == [recovered]
                    if public_stop:
                        stale = run_caller_owned(
                            _frontdoor(
                                global_bin, "service", "stop", "--instance-id", original.instance_id,
                            ), cwd=caller, env=environment,
                            timeout=max(.01, deadline - time.monotonic()),
                            max_stdout_bytes=128 * 1024, max_stderr_bytes=64 * 1024,
                        )
                        assert stale.returncode != 0
                        assert recovered is not None
                        assert lifecycle.endpoints(installation, generation=slot.name) == [recovered]
                        assert lifecycle.healthy(recovered, timeout=3)

                if public_stop or removal_preflight:
                    directory = tmp_path / "independent-host"
                    directory.mkdir()
                    host_result = asyncio.run(_exercise_installed_host(
                        home / ".local" / "bin", caller, environment, interpreter, directory, deadline,
                        expected_slot=slot, expected_generation=1,
                        on_live=stop_for_removal if removal_preflight else recover_service,
                        capture_removal_owner=removal_transaction,
                    ))
                    if removal_preflight:
                        if removal_transaction:
                            from agent_procutil.daemon_instances import (
                                DaemonInstanceReceipt, cleanup_instance,
                            )

                            remove_application(blocked=True)
                            owner = host_result["host_registration"]
                            settled = cleanup_instance(
                                DaemonInstanceReceipt(
                                    owner["instance_id"], Path(owner["registry_root"]),
                                ), terminate=False, deadline=deadline,
                                runtime_root=Path(owner["runtime_root"]),
                            )
                            cleanup.append(settled)
                            assert settled.status == "settled", settled
                            remove_application(blocked=False)
                            assert not slot.exists()
                            assert not (installation / "current-version").exists()
                            assert not (installation / "last-known-good").exists()
                            assert not (
                                home / ".local" / "bin" / (
                                    "agent-remote-driver.ps1" if os.name == "nt"
                                    else "agent-remote-driver"
                                )
                            ).exists()
                            assert (
                                installation / "connector-marketplace" / "generations" / slot.name
                                / "plugin.json"
                            ).is_file()
                            remove_application(blocked=False)
                        else:
                            check_removal_custody(blocked=False)
                else:
                    recover_service()
    assert cleanup and all(item.status == "settled" for item in cleanup), cleanup
    assert not (pid_alive(original.pid) and process_start_time(original.pid) == original.birth)
    if recovered is not None:
        assert not (pid_alive(recovered.pid) and process_start_time(recovered.pid) == recovered.birth)


def test_fresh_application_installs_global_connector_and_owns_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(tmp_path, monkeypatch)


def test_installed_connector_recovers_service_without_installation_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(tmp_path, monkeypatch, recover=True)


def test_installed_public_stop_preserves_independent_host_and_refuses_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(tmp_path, monkeypatch, recover=True, public_stop=True)


def test_external_removal_preflight_preserves_live_host_and_admits_quiescent_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(tmp_path, monkeypatch, removal_preflight=True)


def test_original_service_artifact_settlement_preserves_independent_installed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(
        tmp_path, monkeypatch, removal_preflight=True, service_retirement=True,
        removal_transaction=True,
    )


def test_public_uninstall_preserves_independent_host_and_resumes_after_exact_owner_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(
        tmp_path, monkeypatch, removal_preflight=True, service_retirement=True,
        removal_transaction=True, public_removal=True,
    )


def test_external_service_control_loads_only_copied_profile_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_contained_worker()
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from governed_feed_trust import _governed_feed_configured

    if not _governed_feed_configured():
        pytest.skip("copied profile control requires the configured governed feed")
    uv = shutil.which("uv")
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert uv and interpreter
    deadline = time.monotonic() + 120
    inputs, profile = tmp_path / "source-inputs", tmp_path / "profile"
    application = _prepare_application(
        inputs, profile, monkeypatch=monkeypatch, uv=uv,
        interpreter=interpreter, deadline=deadline,
    )
    external = tmp_path / "external-control"
    shutil.copytree(application / "wheels", external / "wheels")
    shutil.rmtree(inputs)
    shutil.rmtree(profile)
    program = """
import importlib.machinery,json,os,pathlib,sys
external=pathlib.Path(sys.argv[1])
wheels=sorted((external/'wheels').glob('*.whl'))
assert wheels
sys.path[:0]=list(map(str,wheels))
from agent_remote_driver_app import connector,service_lifecycle
from agent_procutil._daemon_instance_identity import birth_identity
assert birth_identity(os.getpid())
for module in (connector,service_lifecycle):
    assert pathlib.Path(module.__file__).is_relative_to(external)
with service_lifecycle.OperationLock(external/'control.lock'):
    assert callable(service_lifecycle.service_removal_custody)
extensions=[
    module for module in list(sys.modules.values())
    if isinstance(getattr(module,'__loader__',None),importlib.machinery.ExtensionFileLoader)
]
assert all(
    pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(sys.base_prefix).resolve())
    for module in extensions
)
print(json.dumps({'control':'copied-wheels','target_native_extensions':0}))
"""
    result = _run(
        [interpreter, "-I", "-B", "-c", program, external],
        cwd=tmp_path, env=dict(os.environ), deadline=deadline,
    )
    assert json.loads(result.stdout) == {
        "control": "copied-wheels", "target_native_extensions": 0,
    }


@pytest.mark.parametrize("long_runtime", [False, True])
def test_original_service_artifacts_settle_through_canonical_supervision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, long_runtime: bool,
) -> None:
    from agent_procutil import spawn_daemon
    from agent_procutil.daemon_instances import (
        DaemonInstanceSpec, InstanceCleanupResult, REGISTRY_ENV, daemon_test_lifecycle,
    )
    from agent_remote_driver_app import service_lifecycle as lifecycle

    require_contained_worker()
    root = tmp_path / "application"
    slot = root / "versions" / ("fixture-" + "g" * 140 if long_runtime else "fixture-generation")
    (slot / ".runtime-bootstrap").mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    binding = SimpleNamespace(root=root, slot=slot)
    registered_slot = Path("\\\\?\\" + str(slot)) if os.name == "nt" and long_runtime else slot
    deadline = time.monotonic() + 30
    cleanup: list[InstanceCleanupResult] = []
    with daemon_test_lifecycle(
        owner="driver-service-artifact-fixture", registry_root=tmp_path / "instances",
        deadline=deadline, cleanup_results=cleanup,
    ) as context:
        for key, value in context.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv(REGISTRY_ENV, str(tmp_path / "instances"))
        process = spawn_daemon(
            [sys.executable, "-c",
             "import asyncio,sys;from pathlib import Path;from types import SimpleNamespace;"
             "from agent_remote_driver_app.service_lifecycle import serve;"
             "asyncio.run(serve(SimpleNamespace(root=Path(sys.argv[1]),slot=Path(sys.argv[2]))))",
             str(root), str(registered_slot)],
            cwd=tmp_path, log_path=tmp_path / "service.log",
            instance=DaemonInstanceSpec(lifecycle.SERVICE, registered_slot, Path(__file__)),
        )
        while True:
            assert process.poll() is None, (tmp_path / "service.log").read_text()
            try:
                original = lifecycle._ready(root, slot.name, deadline)
            except RuntimeError as error:
                assert str(error) == "Driver service startup outcome is unresolved"
                original = None
            if original is not None:
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        path = slot / ".runtime-bootstrap" / f"driver-service-{original.instance_id}.json"
        assert path.is_file()
        with lifecycle.service_removal_custody(
            binding, original.instance_id, deadline=deadline,
        ) as stopped:
            assert stopped == original and not path.exists()
        assert lifecycle.endpoints(root, generation=slot.name) == []
    assert all(item.status == "settled" for item in cleanup), cleanup


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_NATIVE_DRIVER_SERVICE") != "1",
    reason="requires the explicit leased Windows Driver service lifetime owner",
)
def test_installed_service_survives_all_installer_caller_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_service(tmp_path, monkeypatch, caller_jobs=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["unchanged", "replacement", "rollback"])
async def test_fresh_application_installs_reuses_and_runs_without_caller_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_testsuite_property: Callable[[str, str], None], selection: str,
) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    import local_install_profile as profiles
    from governed_feed_trust import _governed_feed_configured
    engine = profiles.source_inputs.engine

    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select an existing native Copilot executable for application installation")
    assert Path(executable).is_absolute() and Path(executable).is_file()
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
               COPILOT_HOME=str(home / ".copilot"),
               COPILOT_CACHE_HOME=str(tmp_path / "copilot-cache"), COPILOT_OFFLINE="true",
               PATH=str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", ""),
               UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON_DOWNLOADS="never",
               UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1")
    env = _managed_python(
        env, directory=tmp_path / "managed-python", uv=uv, deadline=deadline,
    )
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

    def assert_current(expected_generation: int) -> None:
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

    def replace_while_live() -> None:
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

    def rollback_while_live() -> None:
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
    slot: Path, caller: Path, env: dict[str, str], interpreter: str, directory: Path,
    deadline: float, *, expected_slot: Path, expected_generation: int,
    on_live: Callable[[], None] | None = None,
    capture_removal_owner: bool = False,
) -> dict[str, Any]:
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
    host_registration = None
    try:
        while not state.exists() and process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.025)
        assert state.exists()
        facts = json.loads(state.read_text())
        if capture_removal_owner:
            from agent_procutil.daemon_instances import default_registry_root

            registered = [
                owner for owner in observe_instances()
                if owner.get("pid") == str(facts["pid"])
                and owner.get("process_identity_state") == "matching"
                and any(
                    lifecycle_path.is_absolute() and lifecycle_path.samefile(expected_slot)
                    for lifecycle_path in Path(owner["runtime_root"]).parents
                    if lifecycle_path.exists()
                )
            ]
            assert len(registered) == 1
            host_registration = {
                "instance_id": registered[0]["instance_id"],
                "runtime_root": registered[0]["runtime_root"],
                "registry_root": str(default_registry_root()),
            }
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
            subprotocols=[Subprotocol("acp.v1"), Subprotocol("bearer." + websocket["nonce"])],
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
        "host_registration": host_registration,
    }
