"""Exercise the generated POSIX service binstub without registering a service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins/agent-bridge/scripts"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX service ingress")
Ingress = tuple[Path, Path, dict[str, str]]


def _function(name: str) -> str:
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n\n", start) + 2]


@pytest.fixture
def service_ingress(tmp_path: Path) -> Ingress:
    root = tmp_path / "bridge space"
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    home.mkdir()
    tools.mkdir()
    for name in ("bash", "dirname", "cat", "tr", "awk", "date", "sed", "grep", "sort", "cut",
                 "cp", "mkdir", "chmod"):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    calls = tmp_path / "systemctl-calls"
    script = tmp_path / "publish.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        f"SCRIPT_DIR={shlex.quote(str(SCRIPTS))}\n"
        f"SYSTEMD_CALLS={shlex.quote(str(calls))}\n"
        'VENV_DIR="$INSTALL_DIR/versions/obsolete"; PUBLISH_GLOBAL_BINSTUBS=false\n'
        'SYSTEMD_UNIT=agent-bridge.service\n'
        '_ok() { :; }; _skip() { :; }\n'
        '_runtime_publication_copy() { cp -- "$1" "$2"; }\n'
        'systemctl() { printf "%s\\n" "$*" >> "$SYSTEMD_CALLS"; }\n'
        + _function("deploy_binstub") + "\n"
        + _function("_install_systemd_unit") + "\n"
        + "deploy_binstub\n_install_systemd_unit\n",
        encoding="utf-8",
    )
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" > "$UV_CALLS"\n'
        'for key in UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON '
        'VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH UV_WORKING_DIR UV_WORKING_DIRECTORY '
        'UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC UV_CONFIG_FILE; do\n'
        '    [[ ! -v "$key" ]] || exit 91\n'
        'done\n[[ "$UV_NO_CONFIG" == 1 ]] || exit 92\n'
        '[[ "${UV_TEST_MODE:-}" != denied ]] || { echo "policy denied (os error 448)" >&2; exit 93; }\n'
        '[[ "${UV_TEST_MODE:-}" != wrong ]] || { echo /foreign/python; exit 0; }\n'
        'printf "%s\\n" "${@: -1}"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    subprocess.run(
        ["/bin/bash", str(script)], env={**os.environ, "HOME": str(home), "PATH": str(tools)},
        check=True, capture_output=True, text=True, timeout=15,
    )
    assert calls.read_text().splitlines() == [
        "--user daemon-reload", "--user enable agent-bridge.service",
    ]
    unit = (home / ".config/systemd/user/agent-bridge.service").read_text()
    assert f'ExecStart=/bin/bash "{root}/bin/start-agent-bridge"' in unit
    assert "obsolete" not in unit
    assert "KillMode=process\n" in unit
    assert "Restart=on-failure\n" in unit
    assert f'WorkingDirectory="{root}"' in unit
    assert not (home / ".local/bin/agent-bridge").exists()
    environment = {
        **os.environ, "PATH": str(tools), "HOME": str(home),
        "UV_CALLS": str(tmp_path / "uv-calls"),
        "WORKER_CALLS": str(tmp_path / "worker-calls"),
        "UV_PYTHON": "poison", "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "PYTHONHOME": "poison", "PYTHONPATH": "poison",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison",
        "AGENT_RT_ROOT": "/foreign/root", "AGENT_BRIDGE_INSTALL_DIR": "/foreign/root",
    }
    return root, tools, environment


def _slot(root: Path, version: str, *, nested: bool = False) -> Path:
    slot = root / "versions" / version
    python = slot / (".venv/bin/python" if nested else "bin/python")
    python.parent.mkdir(parents=True)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "fixture", "pid": 0,
    }) + "\n", encoding="utf-8")
    python.write_text(
        "#!/bin/bash\n"
        '[[ ! -v PYTHONHOME && ! -v PYTHONPATH ]] || exit 94\n'
        'printf "%s\\n" "$$" "$0" "$PWD" "$AGENT_BRIDGE_INSTALL_DIR" '
        '"$AGENT_BRIDGE_CONFIG_DIR" "$AGENT_BRIDGE_CONNECT_LOG" "$@" > "$WORKER_CALLS"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    return python


def _launch(root: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec /bin/bash "$1" "two words" ""',
         "service", str(root / "bin/start-agent-bridge")],
        env=environment, capture_output=True, text=True, timeout=15,
    )


@pytest.mark.parametrize("selection", ["current", "lkg", "newest", "nested", "cutover"])
def test_posix_service_admits_selected_runtime_before_exec(
    service_ingress: Ingress, selection: str,
) -> None:
    root, _tools, environment = service_ingress
    selected = _slot(root, "1.0.0", nested=selection == "nested")
    _slot(root, "2.0.0")
    if selection in {"current", "nested", "cutover"}:
        (root / "current-version").write_text("1.0.0\n")
    elif selection == "lkg":
        (root / "current-version").write_text("missing\n")
        (root / "last-known-good").write_text("1.0.0\n")
    else:
        selected = root / "versions/2.0.0/bin/python"
    for cycle in range(2):
        if selection == "cutover" and cycle:
            (root / "current-version").write_text("2.0.0\n")
            selected = root / "versions/2.0.0/bin/python"
        before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        result = _launch(root, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(environment["UV_CALLS"]).read_text().splitlines() == [
            "python", "find", "--no-project", "--managed-python", "--no-python-downloads",
            str(selected),
        ]
        assert Path(environment["WORKER_CALLS"]).read_text().splitlines() == [
            result.stdout.strip(), str(selected), str(root), str(root), str(root),
            str(root / "logs/connect.log"), "-m", "agent_bridge", "start", "two words", "",
        ]
        assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("failure", ["runtime", "uv", "provider", "denied", "wrong"])
def test_posix_service_refuses_before_application_python(
    service_ingress: Ingress, failure: str,
) -> None:
    root, tools, environment = service_ingress
    if failure != "runtime":
        _slot(root, "1.0.0")
        (root / "current-version").write_text("1.0.0\n")
    if failure == "uv":
        (tools / "uv").unlink()
    elif failure == "provider":
        (root / "bin/bootstrap-uv.sh").unlink()
    else:
        environment["UV_TEST_MODE"] = failure
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _launch(root, environment)
    assert result.returncode != 0
    assert not Path(environment["WORKER_CALLS"]).exists()
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    if failure in {"runtime", "uv", "provider"}:
        assert not Path(environment["UV_CALLS"]).exists()


def test_posix_service_real_uv_keeps_offline_managed_runtime(
    service_ingress: Ingress, request: pytest.FixtureRequest,
) -> None:
    root, tools, environment = service_ingress
    uv = shutil.which("uv")
    assert uv, "The launch test environment requires prepared UV"
    clean_environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    clean_environment["UV_NO_CONFIG"] = "1"
    clean_environment["UV_PYTHON_DOWNLOADS"] = "never"
    prepared = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=clean_environment, capture_output=True, text=True, timeout=15,
    )
    if prepared.returncode:
        pytest.skip("Explicitly prepare managed CPython 3.11.18 before this offline native witness")
    slot = root / "versions/1.0.0"
    subprocess.run(
        [uv, "venv", "--no-project", "--python", prepared.stdout.strip(),
         "--no-python-downloads", str(slot)],
        env=clean_environment, check=True, capture_output=True, text=True, timeout=15,
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "fixture", "pid": 0,
    }) + "\n", encoding="utf-8")
    (root / "current-version").write_text("1.0.0\n")
    site_packages = next(slot.glob("lib/python*/site-packages"))
    (site_packages / "agent_bridge.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['WORKER_CALLS']).write_text(json.dumps({\n"
        "    'python': sys.executable, 'base': sys._base_executable,\n"
        "    'prefix': sys.prefix, 'pid': os.getpid(), 'argv': sys.argv[1:],\n"
        "}))\n",
        encoding="utf-8",
    )
    shutil.copyfile(uv, tools / "uv-real")
    (tools / "uv-real").chmod(0o755)
    (tools / "uv").write_text(
        '#!/bin/bash\nprintf "selection\\n" >> "$UV_CALLS"\n'
        'exec "$(dirname -- "$0")/uv-real" "$@"\n',
        encoding="utf-8",
    )
    manager_tools = tools.with_name("manager-tools")
    manager_tools.mkdir()
    for source in tools.iterdir():
        if source.name not in {"uv", "uv-real"}:
            (manager_tools / source.name).symlink_to(source)
    environment["PATH"] = str(manager_tools)
    samples = []
    for _cycle in range(2):
        started = time.perf_counter()
        result = _launch(root, environment)
        samples.append(time.perf_counter() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        worker = json.loads(Path(environment["WORKER_CALLS"]).read_text())
        assert Path(worker.pop("base")).samefile(prepared.stdout.strip())
        assert worker == {
            "python": str(slot / "bin/python"),
            "prefix": str(slot), "pid": int(result.stdout.strip()),
            "argv": ["start", "two words", ""],
        }, worker
    assert Path(environment["UV_CALLS"]).read_text().splitlines() == ["selection"] * 2
    request.node.user_properties.append(("bridge_posix_service_performance", json.dumps({
        "samples_seconds": samples, "cycles": 2, "uv_calls_per_launch": 1,
        "network": "no-downloads", "runtime": "managed-venv",
    })))
