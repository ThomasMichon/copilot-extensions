"""Prepared-only POSIX management with inert applications and real offline UV."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest

from test_install_engine_adoption import PLUGIN, function, stage_index_installer
from test_managed_bootstrap_reader_sh import _environment, prepared_helper as prepared_helper

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX management")


@pytest.mark.parametrize("scenario", [
    "stop-current", "stop-lkg", "stop-private", "start-current", "engine-independent",
    "deploy-incoming", "missing-bound-uv", "incomplete",
])
def test_management_admits_selected_runtime_before_effects(
    tmp_path: Path, scenario: str, prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    uv, _helper, store = prepared_helper
    environment = _environment(tmp_path, prepared_helper, "raw")
    environment.update(UV_PYTHON_INSTALL_DIR=str(store), AGENT_RT_ROOT="retained-root")
    probe_environment = {
        key: value for key, value in environment.items()
        if not key.startswith(("UV_", "PYTHON")) and key not in {"VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    probe_environment.update(UV_PYTHON_INSTALL_DIR=str(store), UV_OFFLINE="1", UV_NO_CONFIG="1")
    found = subprocess.run(
        [str(uv), "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        env=probe_environment,
        capture_output=True, text=True, timeout=15,
    )
    if found.returncode and "No interpreter found" in found.stderr:
        pytest.skip("Prepare CPython 3.10.20 before this acquisition-free control")
    assert found.returncode == 0, found.stdout + found.stderr
    packaged = scenario == "stop-lkg"
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=packaged,
    )
    scripts = payload / "scripts"
    shutil.copy2(PLUGIN / "scripts/resolve-runtime.sh", scripts / "resolve-runtime.sh")
    support = scripts if packaged else payload.parents[1] / "libs/installer-engine"
    root = tmp_path / "owned index's \u00e9"
    slot = root / "versions/1.0.0"
    built = subprocess.run(
        [found.stdout.strip(), "-I", "-B", "-m", "venv", "--copies", "--without-pip", str(slot)],
        env=environment, capture_output=True, text=True, timeout=20,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / "bin/python"
    site = next(slot.glob("lib/python*/site-packages"))
    package = site / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "print(json.dumps({'args':sys.argv[1:],'prefix':sys.prefix,"
        "'version':list(sys.version_info[:2]),'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode,'cwd':os.getcwd(),"
        "'uv':os.environ.get('AGENT_INDEX_UV'),'pid':os.getpid(),'sid':os.getsid(0)}))\n"
        "raise SystemExit(23)\n", encoding="utf-8",
    )
    if scenario != "incomplete":
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": "1.0.0", "completed_at": "fixture", "pid": 0,
        }), encoding="utf-8")
    (root / "current-version").write_text("9.0.0" if packaged else "1.0.0", encoding="utf-8")
    if packaged:
        (root / "last-known-good").write_text("1.0.0", encoding="utf-8")
    incoming = root / "versions/9.0.0/bin/python"
    incoming.parent.mkdir(parents=True)
    incoming.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    incoming.chmod(0o700)
    private = root / "tool/uv" if scenario == "stop-private" else tmp_path / "private tools/uv"
    private.parent.mkdir()
    private.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_PROBE_LOG"\n'
        f'exec {shlex.quote(str(uv))} "$@"\n', encoding="utf-8",
    )
    private.chmod(0o700)
    if scenario == "stop-private":
        (Path(environment["PATH"]) / "uv").unlink()
    else:
        environment["AGENT_INDEX_UV"] = str(
            tmp_path / "missing-bound-uv" if scenario == "missing-bound-uv" else private
        )
    for name in ("sort", "nohup"):
        binary = shutil.which(name)
        assert binary
        (Path(environment["PATH"]) / name).symlink_to(binary)
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    functions = "\n".join(function(name, "sh") for name in (
        "_index_management_uv", "_index_management_python", "_active_index_python",
        "_start", "_stop", "_ensure_engine_running", "_service_cutover",
    ))
    definition = next(line for line in (PLUGIN / "scripts/install.sh").read_text().splitlines()
                      if line.startswith("_NEW_SESSION_PY="))
    action = (
        "_start; wait" if scenario == "start-current" else
        "_ensure_engine_running" if scenario == "engine-independent" else
        "_service_cutover" if scenario == "deploy-incoming" else "_stop"
    )
    harness = tmp_path / "management.sh"
    harness.write_text(f"""
set -euo pipefail
. {shlex.quote(str(support / "installer-engine.sh"))}
SCRIPT_DIR={shlex.quote(str(scripts))}
INSTALL_DIR={shlex.quote(str(root))}
LINK_PYTHON={shlex.quote(str(python if scenario == "deploy-incoming" else incoming))}
ENGINE_VENV_PYTHON={shlex.quote(str(python))}
UNIT_DIR={shlex.quote(str(tmp_path / "absent-units"))}
SYSTEMD_UNIT=agent-index.service; ENGINE_SYSTEMD_UNIT=agent-index-engine.service
ENV_FILE={shlex.quote(str(tmp_path / "absent.env"))}; SRC_VERSION=1.0.0; NO_SERVICE=0
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}; _fail() {{ echo "$*" >&2; }}
_activation_role() {{ echo host; }}; _machine_role() {{ echo host; }}
_index_publication_fresh() {{ return 0; }}
_service_healthy() {{ return 1; }}
_write_service_files() {{ printf files > {shlex.quote(str(tmp_path / "service-files"))}; }}
command() {{ [[ "${{1:-}}" != -v || "${{2:-}}" != systemctl ]] || return 1; builtin command "$@"; }}
{definition}
{functions}
before_path="$PATH"; before_uv="${{AGENT_INDEX_UV:-}}"
printf 'shell:%s\\n' "$$"
result=0
{action if scenario == "start-current" else action + " || result=$?"}
[[ "$PATH" == "$before_path" && "${{AGENT_INDEX_UV:-}}" == "$before_uv" &&
   "$AGENT_RT_ROOT" == retained-root && "$UV_NO_CONFIG" == false ]] || exit 96
exit "$result"
""", encoding="utf-8")
    samples = []
    successful = scenario not in {"missing-bound-uv", "incomplete"}
    for _cycle in range(2 if successful else 1):
        started = time.monotonic()
        result = subprocess.run(
            ["/bin/bash", str(harness)], env=environment, cwd=tmp_path,
            capture_output=True, text=True, timeout=20, start_new_session=True,
        )
        samples.append(time.monotonic() - started)
        expected = 23 if scenario == "deploy-incoming" else 0 if successful else 1
        assert result.returncode == expected, result.stdout + result.stderr
        output = (
            (root / "service.log").read_text().splitlines()[-1]
            if scenario == "start-current" else result.stdout
        )
        records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        if successful:
            assert records, result.stdout + result.stderr
            for record in records:
                assert Path(record["prefix"]) == slot
                assert record["version"] == [3, 10]
                assert record["isolated"] == 1 and record["bytecode"] is True
                assert record["cwd"] == str(tmp_path) and record["uv"] == str(private)
                if scenario == "start-current":
                    assert record["args"] == ["start"]
                    shell_pid = next(int(line.split(":")[1]) for line in result.stdout.splitlines()
                                     if line.startswith("shell:"))
                    assert record["sid"] == (
                        shell_pid if environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
                        else record["pid"]
                    )
                else:
                    assert record["args"] in (
                        [["engine", "start"]] if scenario == "engine-independent" else
                        [["deploy"]] if scenario == "deploy-incoming" else
                        [["stop"], ["engine", "stop"]]
                    )
        else:
            assert not records and not (root / "service.log").exists()
            assert not (tmp_path / "service-files").exists()
    calls = Path(environment["UV_PROBE_LOG"])
    assert (calls.read_text().splitlines() if calls.exists() else []) == (
        [f"python find --no-project --managed-python --no-python-downloads {python}"] * 2
        if successful else []
    )
    assert not Path(environment["AMBIENT_MARKER"]).exists()
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and path.name != "service.log"} == before
    assert (tmp_path / "service-files").exists() is (scenario == "deploy-incoming")
    record_property("index_posix_management", json.dumps({
        "scenario": scenario, "status": "measured", "completed": len(samples),
        "samples_seconds": samples, "uv_calls": 2 if successful else 0,
        "runtime_python": "3.10.20", "helper_python": "3.11.18", "network": "disabled",
        "unavailable": ["full installation", "installed service lifetime", "child CPU", "tails"],
    }))


@pytest.mark.parametrize("action", ["_uninstall", "_ensure_running", "_service_cutover"])
def test_management_refusal_stops_dependent_effects(tmp_path: Path, action: str) -> None:
    root = tmp_path / "runtime"
    (root / "versions").mkdir(parents=True)
    functions = "\n".join(function(name, "sh") for name in (
        "_index_management_uv", "_index_management_python", "_active_index_python",
        "_stop", "_uninstall", "_ensure_engine_running", "_ensure_running", "_service_cutover",
    ))
    effect = tmp_path / "forbidden-effect"
    harness = tmp_path / "refusal.sh"
    harness.write_text(f"""
set -euo pipefail
{functions}
INSTALL_DIR={shlex.quote(str(root))}
SCRIPT_DIR={shlex.quote(str(PLUGIN / "scripts"))}
LINK_PYTHON=/bin/bash; ENGINE_VENV_PYTHON=/bin/bash
UNIT_DIR={shlex.quote(str(tmp_path / "units"))}
SYSTEMD_UNIT=agent-index; ENGINE_SYSTEMD_UNIT=agent-index-engine
AGENT_INDEX_UV={shlex.quote(str(tmp_path / "absent-uv"))}
DRY_RUN=0; PURGE=0; NO_SERVICE=0
_activation_role() {{ echo host; }}; _machine_role() {{ echo host; }}
_service_healthy() {{ return 0; }}
_index_publication_fresh() {{ return 0; }}
_fail() {{ echo "$*" >&2; }}
_ensure_service_running() {{ touch {shlex.quote(str(effect))}; }}
_write_service_files() {{ touch {shlex.quote(str(effect))}; }}
_finite_index_uninstall() {{ [[ "$1" == --check ]] || touch {shlex.quote(str(effect))}; }}
command() {{ [[ "${{1:-}}" != -v || "${{2:-}}" != systemctl ]] || return 1; builtin command "$@"; }}
result=0
{action} || result=$?
exit "$result"
""", encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(harness)], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not effect.exists()
