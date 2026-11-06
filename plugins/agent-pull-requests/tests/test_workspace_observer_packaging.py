"""Fresh first-party wheels with prepared external dependencies, no Copilot state."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig

import pytest
from agent_procutil.owned_process import run_caller_owned


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.timeout(300)
def test_installed_console_and_service_dependencies_do_not_require_source_or_copilot(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "tools"))
    from build_python_artifacts import resolve_vendored_libs
    from standalone_consumers import standalone_build_resources

    uv = shutil.which("uv")
    assert uv, "existing uv is required for isolated wheel qualification"
    plugin = repo / "plugins" / "agent-pull-requests"
    fleet = repo / "fleet-manager"
    projects = {
        plugin, fleet,
        *(path for _, path in resolve_vendored_libs(plugin)),
        *(path for _, path in resolve_vendored_libs(fleet)),
    }
    copied = tmp_path / "source"
    wheels = tmp_path / "wheels"
    environment = {
        **os.environ,
        "UV_EXCLUDE_NEWER": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
        "UV_PYTHON_DOWNLOADS": "never",
    }

    def run(argv, *, cwd=tmp_path):
        result = run_caller_owned(
            list(map(str, argv)), cwd=cwd, env=environment,
            timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    for project in sorted(projects):
        target = copied / project.relative_to(repo)
        shutil.copytree(
            project, target,
            ignore=shutil.ignore_patterns(
                "tests", "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache",
            ),
        )
    for resource in standalone_build_resources("fleet-manager", source_root=repo):
        shutil.copytree(
            resource, copied / resource.relative_to(repo), dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("tests", "__pycache__", "*.egg-info"),
        )
    for project in sorted(projects):
        run([
            uv, "build", "--offline", "--no-build-isolation", "--python", sys.executable,
            "--wheel", "--out-dir", wheels, copied / project.relative_to(repo),
        ])
    slot = tmp_path / "application"
    run([uv, "venv", "--python", sys.executable, slot])
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    built = sorted(wheels.glob("*.whl"))
    selected = next(wheel for wheel in built if wheel.name.startswith("agent_pull_requests-"))
    requirements = [
        *[str(wheel) for wheel in built if wheel != selected],
        "agent-pull-requests[fleet] @ " + selected.as_uri(),
    ]
    run([uv, "pip", "install", "--offline", "--no-deps", "--python", python, *requirements])
    libraries = slot / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    # Reuse prepared third-party packages, not their editable-source .pth loaders.
    # A path entry does not execute .pth files from the appended directory.
    (libraries / "prepared-external-dependencies.pth").write_text(
        sysconfig.get_path("purelib") + "\n", encoding="utf-8",
    )
    shutil.rmtree(copied)
    home = tmp_path / "home"
    home.mkdir()
    environment.update(
        HOME=str(home), USERPROFILE=str(home),
        AGENT_PULL_REQUESTS_HOME=str(home / "application-config"),
    )
    for key in ("COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT", "PYTHONPATH"):
        environment.pop(key, None)
    probe = run([
        python, "-I", "-c",
        "import json,sys,inspect,agent_pull_requests,agent_procutil,fleet_manager,"
        "update_driver,zdd,work_coalescing_singleton,single_instance_lease;"
        "from agent_procutil.owned_process import run_caller_owned;"
        "from fleet_manager.gateway_service_caller import ServiceCaller;"
        "print(json.dumps([sys.prefix,[m.__file__ for m in "
        "(agent_pull_requests,agent_procutil,fleet_manager,update_driver,zdd,"
        "work_coalescing_singleton,single_instance_lease)],"
        "list(inspect.signature(run_caller_owned).parameters),"
        "any('Editable' in str(type(f)) for f in sys.meta_path)]))",
    ])
    prefix, modules, parameters, editable = json.loads(probe.stdout)
    assert Path(prefix) == slot
    assert all(Path(path).is_relative_to(slot) for path in modules)
    assert {"stdin_stream", "stdout_stream"} <= set(parameters)
    assert not editable
    command = slot / ("Scripts/agent-pull-requests.exe" if os.name == "nt" else "bin/agent-pull-requests")
    assert "workspace" in run([command, "--help"]).stdout
    unbound = json.loads(run([command, "workspace", "capabilities"]).stdout)
    assert unbound["owner_binding"] == "unavailable"
    refused = run_caller_owned(
        [str(command), "workspace", "--client-profile", "missing", "capabilities"],
        cwd=home, env=environment, timeout=60, max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert refused.returncode == 1 and refused.stderr == ""
    assert json.loads(refused.stdout)["effect_completion"] == "not_inferred"
    assert not (home / ".copilot").exists()
    assert not (home / "application-config").exists()
