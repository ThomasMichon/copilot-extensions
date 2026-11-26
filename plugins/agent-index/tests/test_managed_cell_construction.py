"""Actual cell constructors retain explicit managed requests without host changes."""
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
@pytest.mark.parametrize("scenario", ["prepared", "missing-uv", "missing-support", "failed-request"])
def test_real_managed_cell_constructor(
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
    for name in ("cell-runtime.py", "cell_runtime_processes.py"):
        shutil.copy2(PLUGIN / "scripts" / name, payload / "scripts" / name)
    if scenario == "missing-support":
        engine = payload / "scripts" if materialized else payload.parents[1] / "libs/installer-engine"
        support = engine / ("bootstrap-uv.ps1" if os.name == "nt" else "bootstrap-uv.sh")
        support.rename(support.with_suffix(".unavailable"))
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
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n'
            if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        poison.chmod(0o700)
    foreign = tmp_path / "foreign project"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text(
        '[project]\nname="unrelated-project"\nversion="1.0"\nrequires-python=">=3.15"\n',
        encoding="utf-8",
    )
    incumbent = tmp_path / "unrelated environment"
    incumbent.mkdir()
    (incumbent / "retained").write_text("unchanged", encoding="utf-8")
    trace = tmp_path / "uv-calls"
    env = environment(tmp_path)
    command_path = str(tools)
    if os.name != "nt" and scenario == "prepared":
        command_path = str(tools.relative_to(tmp_path)) + os.pathsep + command_path
    env.update(
        PATH=command_path, UV_PYTHON_INSTALL_DIR=str(store),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        UV_PROJECT=str(foreign), UV_PROJECT_ENVIRONMENT=str(incumbent),
        UV_WORKING_DIRECTORY=str(tmp_path / "missing cwd"),
        VIRTUAL_ENV=str(incumbent), CONDA_PREFIX=str(incumbent),
        PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"), CELL_CONSTRUCTION_TRACE=str(trace),
    )
    driver = tmp_path / "construction_driver.py"
    driver.write_text(
        r"""import importlib.util,json,os,subprocess,sys
from pathlib import Path
source,uv,slot,cwd,request,host,shell=sys.argv[1:]
spec=importlib.util.spec_from_file_location('cell',source)
cell=importlib.util.module_from_spec(spec); spec.loader.exec_module(cell)
helper=cell._cell_process_helpers()
original_which=helper.shutil.which
def which(name):
    if os.name=='nt' and name in ('pwsh','powershell'):
        return shell if name==host else None
    return original_which(name)
helper.shutil.which=which
calls=[]
def execute(command,**options):
    calls.append({'shell':command[0],'cwd':str(options['cwd'])})
    position=4 if os.name=='nt' else 2
    anchor='& $cellUv @UvArguments' if os.name=='nt' else 'invoke_uv_bootstrap_environment "$uv" "$@"'
    prefix=(
        '[IO.File]::AppendAllText($env:CELL_CONSTRUCTION_TRACE,'
        '($UvArguments -join [char]0)+[char]0+[char]0,[Text.UTF8Encoding]::new($false)); '
        if os.name=='nt' else
        'printf \'%s\\0\' "$@" >> "$CELL_CONSTRUCTION_TRACE"; '
        'printf \'\\0\' >> "$CELL_CONSTRUCTION_TRACE"; '
    )
    assert command[position].count(anchor)==1
    command[position]=command[position].replace(anchor,prefix+anchor)
    cell._run_checked(command,**options)
before=dict(os.environ); original_cwd=os.getcwd()
try:
    helper.construct_runtime(
        Path(source).parent.parent,uv,Path(slot),Path(cwd),python_version=request,
        error_type=cell.CellError,validate_file=cell._assert_regular_file,
        quote=cell._powershell_quote,isolated_environment=cell._isolated_environment,
        execute=execute,
    )
    python=Path(slot)/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    result=subprocess.run(
        [str(python),'-I','-B','-X','utf8','-c',
         'import json,sys; print(json.dumps({"prefix":sys.prefix,"version":list(sys.version_info[:3]),'
         '"base_executable":sys._base_executable}))'],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=10,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
    )
    assert result.returncode==0,result.stderr
    report={'status':'created','application':json.loads(result.stdout)}
except cell.CellError as error:
    report={'status':'refused','error':str(error)}
report.update(calls=calls,caller_unchanged=before==dict(os.environ) and original_cwd==os.getcwd(),
              bootstrap=list(sys.version_info[:3]))
print(json.dumps(report))
""",
        encoding="utf-8",
    )
    payload_before = {p.relative_to(payload): p.read_bytes() for p in payload.rglob("*") if p.is_file()}
    samples = []
    completed = 2 if scenario == "prepared" else 1
    request = "3.11.999" if scenario == "failed-request" else "3.10.20"
    slots = []
    for cycle in range(completed):
        slot = tmp_path / f"runtime \u03bb's {cycle}"
        slot.mkdir()
        receipt = slot / ".runtime-slot-ownership.json"
        receipt.write_bytes(b'{"fixture":"retained ownership"}\n')
        slots.append(slot)
        started = time.monotonic()
        result = subprocess.run(
            [str(bootstrap), "-I", "-B", str(driver), str(payload / "scripts/cell-runtime.py"),
             str(tools / uv.name), str(slot), str(foreign), request, host, shell],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["bootstrap"] == [3, 11, 18]
        assert report["caller_unchanged"]
        assert report["status"] == ("created" if scenario == "prepared" else "refused"), report
        assert len(report["calls"]) == (0 if scenario in {"missing-uv", "missing-support"} else 1)
        if report["calls"]:
            assert Path(report["calls"][0]["shell"]).resolve() == Path(shell).resolve()
            assert Path(report["calls"][0]["cwd"]) == foreign
        if scenario == "prepared":
            app = report["application"]
            assert Path(app["prefix"]) == slot
            assert app["version"] == [3, 10, 20]
            assert Path(app["base_executable"]).resolve() == application_python
        else:
            assert not (slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).exists()
        assert receipt.read_bytes() == b'{"fixture":"retained ownership"}\n'
        assert not Path(env["AMBIENT_MARKER"]).exists()
    calls = [
        [argument.decode("utf-8") for argument in call.split(b"\0")]
        for call in trace.read_bytes().split(b"\0\0")[:-1]
    ] if trace.exists() else []
    assert calls == (
        [["venv", str(slot), "--python", request, "--managed-python", "--no-project", "--allow-existing"]
         for slot in slots] if scenario not in {"missing-uv", "missing-support"} else []
    )
    assert {p.relative_to(payload): p.read_bytes() for p in payload.rglob("*") if p.is_file()} == payload_before
    assert list(incumbent.iterdir()) == [incumbent / "retained"]
    assert (incumbent / "retained").read_text(encoding="utf-8") == "unchanged"
    assert list(foreign.iterdir()) == [foreign / "pyproject.toml"]
    record_property("index_cell_construction_performance", json.dumps({
        "host": host, "layout": "materialized" if materialized else "raw", "scenario": scenario,
        "status": "measured", "completed": completed, "samples_seconds": samples,
        "uv_venv_calls": len(calls), "network": "disabled", "caller_environment": "unchanged",
        "scope": "explicit constructions in fresh private slots; not ordinary completed-runtime reuse",
    }))
