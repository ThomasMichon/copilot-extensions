"""Prepared installer Python admission with inert cell-lifecycle consumers."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

from .managed_command_fixture import HEADLESS, PLUGIN, REPO, prepared_python_inputs, snapshot

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
ACTIONS = [
    "cell-repair", "cell-uninstall", "cell-attribute-legacy", "cell-deactivate", "cell-retire-legacy",
]


@pytest.fixture(scope="module")
def installer_python() -> tuple[Path, Path, Path]:
    return prepared_python_inputs("cpython@3.11.18")


@dataclass
class LifecycleFixture:
    payload: Path
    support: Path
    capture: Path
    home: Path
    tools: Path
    env: dict[str, str]
    python: Path
    proof: Path
    calls: Path
    poison: Path
    shell: str
    host: str

    def run(self, action: str) -> subprocess.CompletedProcess[str]:
        if self.host == "bash":
            command = [
                self.shell, str(self.payload / "scripts/init.sh"), action,
                "--context", "context with spaces", "--durable-home", "durable home",
                "--expect-current-absent",
            ]
        else:
            command = [
                self.shell, "-NoProfile", "-File", str(self.payload / "scripts/init.ps1"),
                "-Action", action, "-Context", "context with spaces",
                "-DurableHome", "durable home", "-ExpectCurrentAbsent",
            ]
        return subprocess.run(
            command, input="caller input\n", capture_output=True, text=True,
            env=self.env, cwd=self.payload, timeout=40, creationflags=HEADLESS,
        )


@pytest.fixture
def lifecycle(
    tmp_path: Path, host: str, materialized: bool, installer_python: tuple[Path, Path, Path],
) -> LifecycleFixture:
    if (host == "powershell" and os.name != "nt") or (host == "bash" and os.name == "nt"):
        pytest.skip("This installer shell is not supported on the addressed operating system")
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    uv, python, store = installer_python
    payload = tmp_path / "source/plugins/agent-machines"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "init.sh", "init.ps1", "cell-lifecycle-bootstrap.sh", "cell-lifecycle-bootstrap.ps1",
        "versioned_runtime_native.ps1",
    ):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    suffix = "sh" if host == "bash" else "ps1"
    if materialized:
        previous = sys.path[:]
        try:
            owner = runpy.run_path(str(REPO / "tools/materialize_installer_engine.py"))
            for source, target in owner["derived_file_pairs"](
                REPO, payload.parents[1], copied_consumer=(PLUGIN, payload),
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        finally:
            sys.path[:] = previous
        support = scripts
        capture = scripts / ("native_capture.sh" if host == "bash" else "bootstrap-capture.exe")
    else:
        support = payload.parents[1] / "libs/installer-engine"
        support.mkdir(parents=True)
        for name in (
            f"bootstrap-uv.{suffix}", "installer-instance-python-version.txt",
            "bootstrap-capture.ps1", "bootstrap-capture.exe",
        ):
            shutil.copy2(REPO / "libs/installer-engine" / name, support / name)
        capture = (
            payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
            if host == "bash" else support / "bootstrap-capture.exe"
        )
        if host == "bash":
            capture.parent.mkdir(parents=True)
            shutil.copy2(REPO / "libs/agent-procutil/src/agent_procutil/native_capture.sh", capture)
    provider = support / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "${*:2}" >> "$MACHINES_UV_LOG"\n' if host == "bash" else
        '    [IO.File]::AppendAllText($env:MACHINES_UV_LOG, ($Arguments -join " ") + "`n")\n'
    )
    original = provider.read_text(encoding="utf-8")
    assert original.count(invocation) == 1
    provider.write_text(original.replace(invocation, observation + invocation), encoding="utf-8", newline="\n")
    (scripts / "cell_lifecycle.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['LIFECYCLE_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'version':list(sys.version_info[:3]),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'selector':os.environ.get('UV_PYTHON')}),encoding='utf-8')\n"
        "print('lifecycle stdout')\nprint('lifecycle stderr',file=sys.stderr)\n"
        "raise SystemExit(37)\n", encoding="utf-8",
    )
    home, tools = tmp_path / "private home", tmp_path / "private tools"
    home.mkdir()
    tools.mkdir()
    if os.name != "nt":
        for name in ("dirname", "wc", "cat", "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir"):
            executable = shutil.which(name)
            assert executable is not None, name
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n' if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n', encoding="utf-8", newline="\n",
        )
        poison.chmod(0o700)
    shutil.copy2(uv, tools / uv.name)
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_MACHINES_", "AGENT_RT_", "MACHINES_", "COPILOT_EXTENSIONS_",
                               "UPDATE_DRIVER_"))
        and key not in ("BASH_ENV", "ENV", "COPILOT_PLUGIN_ROOT")
    }
    proof, calls, poison = tmp_path / "proof.json", tmp_path / "uv-calls", tmp_path / "ambient-ran"
    env.update(
        HOME=str(home), USERPROFILE=str(home), PATH=str(tools),
        UV_PYTHON_INSTALL_DIR=str(store), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated", PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        LIFECYCLE_PROOF=str(proof), MACHINES_UV_LOG=str(calls), AMBIENT_MARKER=str(poison),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    return LifecycleFixture(
        payload, support, capture, home, tools, env, python, proof, calls, poison, shell, host,
    )


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("action", ACTIONS)
def test_cell_lifecycle_uses_prepared_installer_python(
    lifecycle: LifecycleFixture, action: str, record_property: Callable[[str, object], None],
) -> None:
    before = snapshot(lifecycle.payload.parents[1])
    caller = os.environ.copy()
    samples = []
    for _ in range(2):
        started = time.monotonic()
        result = lifecycle.run(action)
        samples.append(time.monotonic() - started)
        assert result.returncode == 37, result
        assert result.stdout == "lifecycle stdout\n", result
        assert result.stderr == "lifecycle stderr\n", result
        proof = json.loads(lifecycle.proof.read_text(encoding="utf-8"))
        assert Path(proof["python"]).resolve() == lifecycle.python
        assert proof["version"] == [3, 11, 18]
        assert proof["argv"] == [
            action, "--context", "context with spaces", "--durable-home", "durable home",
            "--expect-current-absent",
        ]
        assert proof["stdin"] == "caller input\n"
        assert Path(proof["cwd"]) == lifecycle.home
        assert proof["isolated"] == 1 and proof["bytecode"]
        assert proof["selector"] == "unrelated-python"
        assert not lifecycle.poison.exists()
    calls = lifecycle.calls.read_text(encoding="utf-8").splitlines()
    expected = [
        "--version",
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ]
    assert calls == expected * 2
    assert snapshot(lifecycle.payload.parents[1]) == before
    assert os.environ == caller
    record_property("machines_cell_bootstrap", json.dumps({
        "shell": lifecycle.host, "action": action,
        "materialized": lifecycle.support == lifecycle.payload / "scripts",
        "bootstrap_python": "3.11.18", "uv_calls": 2, "samples_seconds": samples, "network": "disabled",
    }, sort_keys=True))


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", [
    "missing-uv", "missing-provider", "missing-request", "missing-capture", "unprepared",
])
def test_cell_lifecycle_missing_prerequisites_never_falls_back(
    lifecycle: LifecycleFixture, scenario: str,
) -> None:
    if scenario == "missing-uv":
        uv = next(path for path in lifecycle.tools.iterdir() if path.stem == "uv")
        uv.rename(uv.with_name("unavailable-uv"))
    elif scenario == "missing-provider":
        path = lifecycle.support / ("bootstrap-uv.sh" if lifecycle.host == "bash" else "bootstrap-uv.ps1")
        path.rename(path.with_suffix(".unavailable"))
    elif scenario == "missing-request":
        path = lifecycle.support / "installer-instance-python-version.txt"
        path.rename(path.with_suffix(".unavailable"))
    elif scenario == "missing-capture":
        path = lifecycle.capture if lifecycle.host == "bash" or os.name == "nt" else (
            lifecycle.payload / "scripts/versioned_runtime_native.ps1"
        )
        path.rename(path.with_suffix(".unavailable"))
    else:
        store = lifecycle.home / "empty-python-store"
        store.mkdir()
        lifecycle.env["UV_PYTHON_INSTALL_DIR"] = str(store)
    before = snapshot(lifecycle.payload.parents[1])
    result = lifecycle.run("cell-repair")
    assert result.returncode not in (0, 37, 91), result
    assert not lifecycle.proof.exists()
    assert not lifecycle.poison.exists()
    assert snapshot(lifecycle.payload.parents[1]) == before
    calls = lifecycle.calls.read_text(encoding="utf-8").splitlines() if lifecycle.calls.exists() else []
    assert not any("install" in call or "pip" in call for call in calls)
