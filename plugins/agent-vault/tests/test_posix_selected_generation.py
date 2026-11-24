"""Native POSIX management verifies generation identity, not unit activity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import venv

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="native POSIX service management")


def _function(name: str) -> str:
    source = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    opening, closing = (r"\(", r"\)") if name == "_active_vault_python" else (r"\{", r"\}")
    match = re.search(rf"^{name}\(\) {opening}.*?^{closing}", source, re.M | re.S)
    assert match is not None, name
    return match.group()


@pytest.mark.parametrize(
    "before,after,manager,expected,starts",
    [(0, 0, 0, 0, 0), (1, 0, 0, 0, 1), (1, 1, 0, 1, 1),
     (2, 0, 0, 1, 0), (3, 0, 0, 1, 0), (1, 0, 7, 7, 1)],
)
def test_posix_start_does_not_certify_an_old_active_unit(
    tmp_path: Path, before: int, after: int, manager: int, expected: int, starts: int,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    units = tmp_path / "units"
    units.mkdir()
    (units / "fixture.service").write_text("retained unit", encoding="utf-8")
    calls = tmp_path / "calls"
    script = tmp_path / "start.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"UNIT_DIR={shlex.quote(str(units))}; SYSTEMD_UNIT=fixture.service\n"
        f"CALLS={shlex.quote(str(calls))}; checks=0\n"
        "_active_vault_python() { printf '%s\\n' selected-python; }\n"
        f"systemctl() {{ printf '%s\\n' \"$*\" >> \"$CALLS\"; return {manager}; }}\n"
        "_vault_selected_generation() {\n"
        " [[ \"$1\" == selected-python ]] || return 9\n"
        " checks=$((checks+1)); if [[ \"$checks\" == 1 ]]; then\n"
        f"  [[ \"$2\" == ready && \"$3\" == 0 ]] || return 9; return {before}; fi\n"
        " [[ \"$2\" == ensure ]] || return 9\n"
        f" return {after}\n"
        "}\n_fail() { printf '%s\\n' \"$1\" >&2; }\n_ok() { printf '%s\\n' \"$1\"; }\n"
        + _function("do_start") + "\ndo_start\n",
        encoding="utf-8",
    )
    result = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stdout + result.stderr
    observed = calls.read_text().splitlines() if calls.exists() else []
    assert observed == ["--user start fixture.service"] * starts
    assert (units / "fixture.service").read_text() == "retained unit"
    if expected:
        assert "generation is ready" not in result.stdout


def test_posix_management_resolver_is_read_only_and_caller_scoped(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    resolver = root / "bin/resolve-runtime.sh"
    resolver.write_text(
        '[[ "$AGENT_RT_RESOLVE_TRACE_MODE" == read-only ]] || return 9\n'
        "AGENT_RT_PY=selected-python\n", encoding="utf-8",
    )
    script = tmp_path / "resolve.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}; LINK_PYTHON=unbuilt-python\n"
        "AGENT_RT_ROOT=retained-root; AGENT_RT_RESOLVE_TRACE_MODE=retained-mode\n"
        + _function("_active_vault_python")
        + '\nselected="$(_active_vault_python)"\n'
        '[[ "$selected" == selected-python && "$AGENT_RT_ROOT" == retained-root'
        ' && "$AGENT_RT_RESOLVE_TRACE_MODE" == retained-mode ]]\n',
        encoding="utf-8",
    )
    before = resolver.read_bytes()
    result = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert resolver.read_bytes() == before


@pytest.mark.parametrize(
    "mode,ready,selected,expected",
    [("ready", False, True, 1), ("ready", True, True, 0),
     ("ready", True, False, 3), ("ensure", True, True, 0)],
)
def test_posix_generation_probe_uses_the_selected_isolated_runtime(
    tmp_path: Path, mode: str, ready: bool, selected: bool, expected: int,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    root = tmp_path / "runtime"
    runtime = root / "versions/current"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / "bin/python"
    site = next((runtime / "lib").glob("python*/site-packages"))
    package = site / "agent_vault"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    report = tmp_path / "observed.json"
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent_vault.py").write_text("raise AssertionError('cwd import')\n", encoding="utf-8")
    (package / "configuration.py").write_text(
        "def service_configuration_snapshot(project):\n"
        " return {'root':project,'config':project+'/native.json','native_default_owner':'1'}\n",
        encoding="utf-8",
    )
    (package / "config.py").write_text(
        "import json,os,pathlib,sys\n"
        "def bind_service_configuration(root, *, native_default=False):\n"
        f" pathlib.Path({str(report)!r}).write_text(json.dumps({{"
        "'root':root,'native':native_default,'prefix':sys.prefix,"
        "'home':os.environ.get('AGENT_VAULT_HOME'),"
        "'runtime':os.environ.get('AGENT_RT_ROOT'),"
        "'config':os.environ.get('AGENT_VAULT_CONFIG'),"
        "'port':os.environ.get('AGENT_VAULT_PORT')}))\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text("def run_dir(): return 'fixture'\n", encoding="utf-8")
    (package / "service_generation.py").write_text(
        "def read_endpoint(root): return root\n"
        f"def selected(): return {selected!r}\n"
        f"def endpoint_health(endpoint): return {{'accepting_work':{ready!r}}}\n"
        f"def same_generation(status): return {ready!r}\n"
        f"def ensure_selected(timeout): return {ready!r}\n",
        encoding="utf-8",
    )
    script = tmp_path / "probe.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}; RUN_DIR=\"$INSTALL_DIR/run\"\n"
        "SOCKET_PATH=fixture; PIPE_PATH=fixture; PID_FILE=fixture; LOG_FILE=fixture\n"
        "SERVICE_SUFFIX=fixture; INSTALLATION_ID=fixture\n"
        f"COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT={shlex.quote(str(project))}\n"
        + _function("_vault_selected_generation")
        + f"\n_vault_selected_generation {shlex.quote(str(python))} {mode} 0\n",
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    environment["PYTHONPATH"] = str(project)
    environment["AGENT_RT_ROOT"] = str(project)
    result = subprocess.run(
        [bash, str(script)], cwd=project, env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert json.loads(report.read_text()) == {
        "root": str(project), "native": True, "prefix": str(runtime), "home": str(root),
        "config": str(project / "native.json"), "port": "0", "runtime": str(root),
    }
