"""Prepared-only cell probes with independent inert application environments."""
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


@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", [
    "origin", "site", "missing-uv", "missing-support", "missing-runtime",
    "failed-import", "outside-slot",
])
def test_real_prepared_cell_probe(
    tmp_path: Path, materialized: bool, scenario: str, application_python: Path,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    uv, bootstrap, store = prepared_helper
    assert bootstrap != application_python
    payload = stage_index_installer(
        tmp_path / "source/plugins/agent-index", materialized=materialized,
    )
    scripts = payload / "scripts"
    for name in ("cell-runtime.py", "cell_runtime_processes.py", "daemon_instance_runtime.pyz"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    env = environment(tmp_path)
    env.update(
        UV_OFFLINE="1", UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON_INSTALL_DIR=str(store), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )
    slot = tmp_path / "selected application's \u03bb runtime"
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application_python), str(slot)],
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_index"
    package.mkdir()
    marker = tmp_path / "import-ran"
    (package / "__init__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['PROBE_MARKER']).write_text(json.dumps({"
        "'python':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'cwd':os.getcwd()}),encoding='utf-8')\n"
        + ("raise RuntimeError('inert import failure')\n" if scenario == "failed-import" else "")
        + ("__file__=os.environ['OUTSIDE_ORIGIN']\n" if scenario == "outside-slot" else ""),
        encoding="utf-8",
    )
    tools = tmp_path / "private tools"
    tools.mkdir()
    if scenario != "missing-uv":
        shutil.copy2(uv, tools / uv.name)
    for name in ("python", "python3", "py"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n'
            if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        poison.chmod(0o700)
    if scenario == "missing-support":
        capsule = scripts / "daemon_instance_runtime.pyz"
        capsule.rename(capsule.with_suffix(".unavailable"))
    elif scenario == "missing-runtime":
        python.rename(python.with_suffix(".unavailable"))
    env.update(
        PATH=str(tools), PROBE_MARKER=str(marker),
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        OUTSIDE_ORIGIN=str(tmp_path / "outside" / "__init__.py"),
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated",
        PYTHONPATH="unrelated", PYTHONHOME="unrelated",
    )
    driver = tmp_path / "probe_driver.py"
    driver.write_text(
        "import importlib.util,json,sys\nfrom pathlib import Path\n"
        "source,python,slot,scenario=map(str,sys.argv[1:])\n"
        "spec=importlib.util.spec_from_file_location('cell',source)\n"
        "cell=importlib.util.module_from_spec(spec); spec.loader.exec_module(cell)\n"
        "helper=cell._cell_process_helpers(); calls=[]\n"
        "if Path(source).with_name('daemon_instance_runtime.pyz').exists():\n"
        " managed,owner=helper._prepared_process_helpers()\n"
        " actual=owner.run_caller_owned\n"
        " def capture(kind,argv,**options):\n"
        "  calls.append({'kind':kind,'argv':argv})\n"
        "  return actual(argv,**options)\n"
        " managed.run_caller_owned=lambda argv,**kw:capture('uv',argv,**kw)\n"
        " owner.run_caller_owned=lambda argv,**kw:capture('probe',argv,**kw)\n"
        "try:\n"
        " if scenario=='site':\n"
        "  origin=str(helper.runtime_site_packages(Path(python),Path(slot),"
        "error_type=cell.CellError,isolated_environment=cell._isolated_environment))\n"
        " else:\n"
        "  origin=str(cell._runtime_module_path(Path(python),Path(slot),label='fixture runtime'))\n"
        " print(json.dumps({'status':'ok','origin':origin,'calls':calls,"
        "'bootstrap':sys.version_info[:3]}))\n"
        "except cell.CellError as error:\n"
        " print(json.dumps({'status':'refused','error':str(error),'calls':calls,"
        "'bootstrap':sys.version_info[:3]}))\n",
        encoding="utf-8",
    )
    before = {p.relative_to(slot): p.read_bytes() for p in slot.rglob("*") if p.is_file()}
    samples = []
    successful = scenario in {"origin", "site"}
    for _cycle in range(2 if successful else 1):
        started = time.monotonic()
        result = subprocess.run(
            [str(bootstrap), "-I", "-B", str(driver), str(scripts / "cell-runtime.py"),
             str(python), str(slot), scenario],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr, result.stderr
        report = json.loads(result.stdout)
        assert report["bootstrap"] == [3, 11, 18]
        assert report["status"] == ("ok" if successful else "refused"), report
        admitted = scenario not in {"missing-uv", "missing-support", "missing-runtime"}
        assert [call["kind"] for call in report["calls"]] == (["uv", "probe"] if admitted else [])
        if admitted:
            assert report["calls"][0]["argv"] == [
                str(tools / uv.name), "python", "find", "--no-project", "--managed-python",
                "--no-python-downloads", str(python),
            ]
            assert report["calls"][1]["argv"][:6] == [
                str(python), "-I", "-B", "-X", "utf8", "-c",
            ]
        if scenario == "origin":
            assert Path(report["origin"]) == (package / "__init__.py").resolve()
        elif scenario == "site":
            assert Path(report["origin"]) == site
        elif scenario == "outside-slot":
            assert "outside the selected runtime slot" in report["error"]
        elif scenario == "failed-import":
            assert "inert import failure" in report["error"]
        if admitted and scenario != "site":
            observed = json.loads(marker.read_text(encoding="utf-8"))
            assert Path(observed["python"]) == python
            assert Path(observed["prefix"]) == slot
            assert observed["version"] == [3, 10, 20]
            assert observed["isolated"] == observed["bytecode"] == 1
            assert Path(observed["cwd"]) == slot
        else:
            assert not marker.exists()
        assert not Path(env["AMBIENT_MARKER"]).exists()
    assert before == {p.relative_to(slot): p.read_bytes() for p in slot.rglob("*") if p.is_file()}
    record_property("index_cell_probe_performance", json.dumps({
        "layout": "materialized" if materialized else "raw", "scenario": scenario,
        "status": "measured", "completed": len(samples), "samples_seconds": samples,
        "uv_lookups": len(samples) if admitted else 0,
        "application_probes": len(samples) if admitted else 0, "network": "disabled",
    }))
