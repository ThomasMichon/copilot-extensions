"""Actual native dependency ingress with an explicitly inert policy observer."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from test_install_engine_adoption import PLUGIN, environment, stage_index_installer
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper
from test_managed_command_gate import application_python as application_python


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", ["prepared", "missing-uv", "missing-helper", "policy-refusal"])
def test_real_managed_cell_dependency_ingress(
    tmp_path: Path, host: str, materialized: bool, scenario: str,
    prepared_helper: tuple[Path, Path, Path], application_python: Path,
    record_property: Callable[[str, object], None],
) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("This control requires the addressed native shell")
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    uv, bootstrap, store = prepared_helper
    assert bootstrap != application_python
    payload = stage_index_installer(
        tmp_path / "source/plugins/agent-index", materialized=materialized,
    )
    names = ["cell-runtime.py", "cell_runtime_processes.py"]
    if scenario != "missing-helper":
        names.append("daemon_instance_runtime.pyz")
    for name in names:
        shutil.copy2(PLUGIN / "scripts" / name, payload / "scripts" / name)
    engine = payload / "scripts" if materialized else payload.parents[1] / "libs/installer-engine"
    (engine / "installer_dependency_policy.py").write_text(
        """import json,os,sys
from pathlib import Path
Path(os.environ['POLICY_OBSERVATION']).write_text(json.dumps({
    'version':list(sys.version_info[:3]),'python':sys.executable,'cwd':os.getcwd(),
    'arguments':sys.argv[1:],'resolver_config':os.environ.get('UV_CONFIG_FILE'),
    'python_state_absent':'PYTHONPATH' not in os.environ and 'PYTHONHOME' not in os.environ,
}),encoding='utf-8')
print('fixture dependency policy observation',file=sys.stderr)
sys.exit(37 if os.environ.get('POLICY_REFUSAL') else 0)
""",
        encoding="utf-8",
    )
    tools = tmp_path / "private tools"
    tools.mkdir()
    if scenario != "missing-uv":
        shutil.copy2(uv, tools / uv.name)
    if os.name != "nt":
        for name in ("bash", "dirname", "grep", "sleep"):
            executable = shutil.which(name)
            assert executable
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py"):
        command = tools / (name + ".cmd" if os.name == "nt" else name)
        command.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n'
            if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    foreign = tmp_path / "foreign \u03bb's project"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text(
        '[project]\nname="unrelated-project"\nversion="1.0"\nrequires-python=">=3.15"\n',
        encoding="utf-8",
    )
    observation = tmp_path / "policy-observation.json"
    env = environment(tmp_path)
    env.update(
        PATH=str(tools), UV_PYTHON_INSTALL_DIR=str(store),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="retained-resolver-settings.toml", UV_NO_CONFIG="false",
        UV_PROJECT=str(foreign), UV_WORKING_DIRECTORY=str(tmp_path / "missing cwd"),
        PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"), POLICY_OBSERVATION=str(observation),
    )
    if scenario == "policy-refusal":
        env["POLICY_REFUSAL"] = "1"
    driver = tmp_path / "dependency_driver.py"
    driver.write_text(
        r"""import importlib.util,json,os,sys
from pathlib import Path
source,uv,python,cwd,host,shell=sys.argv[1:]
spec=importlib.util.spec_from_file_location('cell',source)
cell=importlib.util.module_from_spec(spec); spec.loader.exec_module(cell)
helper=cell._cell_process_helpers()
helper.shutil.which=lambda name:os.path.relpath(shell) if name==host else None
original_prepared=helper._prepared_process_helpers
lookups=[]; executions=[]
def prepared():
    managed,owner=original_prepared()
    original_run=managed.run_caller_owned
    def run(command,**options):
        lookups.append(command)
        return original_run(command,**options)
    managed.run_caller_owned=run
    return managed,owner
helper._prepared_process_helpers=prepared
def execute(command,**options):
    executions.append({'shell':command[0],'cwd':str(options['cwd'])})
    cell._run_checked(command,**options)
before=dict(os.environ); original_cwd=os.getcwd()
try:
    helper.install_dependency(
        Path(source).parent.parent,uv,Path(python),'fixture-package',Path(cwd),
        error_type=cell.CellError,validate_file=cell._assert_regular_file,
        quote=cell._powershell_quote,isolated_environment=cell._isolated_environment,
        execute=execute,
    )
    report={'status':'completed'}
except cell.CellError as error:
    report={'status':'refused','error':str(error)}
report.update(lookups=lookups,executions=executions,
              caller_unchanged=before==dict(os.environ) and original_cwd==os.getcwd(),
              bootstrap=list(sys.version_info[:3]))
print(json.dumps(report))
""",
        encoding="utf-8",
    )
    payload_before = {p.relative_to(payload): p.read_bytes() for p in payload.rglob("*") if p.is_file()}
    completed = 2 if scenario == "prepared" else 1
    samples = []
    for cycle in range(completed):
        started = time.monotonic()
        result = subprocess.run(
            [str(bootstrap), "-I", "-B", str(driver), str(payload / "scripts/cell-runtime.py"),
             str((tools / uv.name).relative_to(tmp_path)), str(application_python),
             str(foreign), host, shell],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["bootstrap"] == [3, 11, 18]
        assert report["caller_unchanged"]
        assert report["status"] == ("completed" if scenario == "prepared" else "refused"), report
        admitted = scenario in {"prepared", "policy-refusal"}
        assert report["lookups"] == ([
            [str(tools / uv.name), "python", "find", "--no-project", "--managed-python",
             "--no-python-downloads", str(application_python)],
        ] if admitted else [])
        assert len(report["executions"]) == int(admitted), report
        if admitted:
            execution = report["executions"][0]
            assert Path(execution["shell"]).is_absolute()
            assert Path(execution["shell"]).resolve() == Path(shell).resolve()
            assert execution["cwd"] == str(foreign)
        if admitted:
            policy = json.loads(observation.read_text(encoding="utf-8"))
            assert policy["version"] == [3, 10, 20]
            assert Path(policy["python"]) == application_python
            assert policy["cwd"] == str(foreign)
            assert policy["arguments"] == [
                "--uv", str(tools / uv.name), "--", "--python", str(application_python),
                "fixture-package", "--quiet",
            ]
            assert policy["resolver_config"] == env["UV_CONFIG_FILE"]
            assert policy["python_state_absent"]
            assert "fixture dependency policy observation" in result.stderr
            if scenario == "policy-refusal":
                assert "exit 37" in report["error"]
        else:
            assert not observation.exists()
        assert not Path(env["AMBIENT_MARKER"]).exists()
    assert {p.relative_to(payload): p.read_bytes() for p in payload.rglob("*") if p.is_file()} == payload_before
    assert list(foreign.iterdir()) == [foreign / "pyproject.toml"]
    record_property("index_cell_dependency_performance", json.dumps({
        "host": host, "layout": "materialized" if materialized else "raw", "scenario": scenario,
        "status": "measured", "completed": completed, "samples_seconds": samples,
        "runtime_uv_lookups": completed if scenario in {"prepared", "policy-refusal"} else 0,
        "policy_observations": completed if scenario in {"prepared", "policy-refusal"} else 0,
        "network": "disabled", "caller_environment": "unchanged",
        "scope": "native ingress with inert policy observation; not a package installation",
    }))
