"""Uninstall inspects prepared managed Python without PATH interpreter fallback."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from test_installers import PLUGIN, _copy_payload

NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
HINTS = (
    "UV_PYTHON", "UV_PYTHON_PREFERENCE", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON",
    "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH", "UV_WORKING_DIR",
    "UV_WORKING_DIRECTORY", "UV_ISOLATED", "UV_ENV_FILE", "UV_PROJECT",
    "UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC",
)


def _run(
    arguments: list[str], directory: Path, environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed hosts and fixture-owned argument vectors
        arguments, cwd=directory, env=environment, capture_output=True,
        text=True, encoding="utf-8", timeout=60, creationflags=NO_WINDOW,
    )


@pytest.fixture(scope="module")
def managed() -> tuple[Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("Prepared uv is unavailable")
    declaration = json.loads((PLUGIN / "payload-invocation.json").read_text())
    environment = {key: value for key, value in os.environ.items() if key not in HINTS}
    result = subprocess.run(  # noqa: S603 - selected uv and the project's declared managed request
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@" + declaration["bootstrapPython"]],
        capture_output=True, text=True, timeout=30, env=environment, creationflags=NO_WINDOW,
    )
    if result.returncode:
        pytest.skip("The declared managed interpreter has not been prepared")
    return Path(uv), Path(result.stdout.strip())


@pytest.fixture
def payload(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    target = tmp_path / "payload"
    _copy_payload(target)
    root = tmp_path / "installed"
    root.mkdir()
    (root / "config.json").write_text('{"retained":true}\n')
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "budget-guidance",
        "source": {"kind": "local", "plugin": "budget-guidance"},
    }))
    scripts = target / "scripts"
    helper = scripts / "installer_engine_uninstall.py"
    helper.rename(scripts / "actual_uninstall.py")
    helper.write_text(
        "import importlib.util,json,os,pathlib,sys\n"
        "source=pathlib.Path(__file__).with_name('actual_uninstall.py')\n"
        "spec=importlib.util.spec_from_file_location('actual_uninstall',source)\n"
        "module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module\n"
        "spec.loader.exec_module(module)\n"
        "status=module.main()\n"
        "if status==0:\n"
        " pathlib.Path(os.environ['UNINSTALL_RECEIPT']).write_text(json.dumps(dict("
        "executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix)))\n"
        "raise SystemExit(status)\n",
    )
    receipt = tmp_path / "receipt.json"
    home = tmp_path / "home"
    home.mkdir()
    environment = {key: value for key, value in os.environ.items() if key not in HINTS}
    environment.update(
        HOME=str(home), USERPROFILE=str(home), UNINSTALL_RECEIPT=str(receipt),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1", UV_PYTHON="unrelated-python",
        UV_NO_MANAGED_PYTHON="1", VIRTUAL_ENV=str(root), PYTHONHOME=str(root),
    )
    return scripts, root, receipt, environment


def _prepare_path(
    temporary: Path, uv: Path, environment: dict[str, str], mode: str,
) -> None:
    tools = temporary / "tools"
    tools.mkdir()
    if os.name == "nt":
        paths = [str(tools), str(uv.parent)] if mode != "missing-uv" else [str(tools)]
        paths.append(str(Path(os.environ["SystemRoot"]) / "System32"))
        environment["PATH"] = os.pathsep.join(paths)
        for name in ("python.exe", "python3.exe", "py.exe"):
            (tools / name).write_bytes(b"not an executable interpreter\n")
    else:
        if mode != "missing-uv":
            (tools / "uv").symlink_to(uv)
        for name in ("bash", "dirname", "wc", "cat", "mkdir"):
            executable = shutil.which(name)
            assert executable, name
            (tools / name).symlink_to(executable)
        for name in ("python", "python3", "py"):
            candidate = tools / name
            candidate.write_text("#!/bin/sh\nexit 97\n")
            candidate.chmod(0o700)
        environment["PATH"] = str(tools)
    if mode == "missing-managed":
        store = temporary / "empty-managed-store"
        store.mkdir()
        environment["UV_PYTHON_INSTALL_DIR"] = str(store)


def _assert_result(
    result: subprocess.CompletedProcess[str], root: Path, receipt: Path, selected: Path, mode: str,
) -> None:
    assert (result.returncode == 0) is (mode == "prepared"), result.stdout + result.stderr
    assert receipt.exists() is (mode == "prepared"), result.stdout + result.stderr
    assert (root / "config.json").read_text() == '{"retained":true}\n'
    if mode == "missing-uv":
        assert any(message in result.stderr for message in ("uv is unavailable", "uv-not-on-path"))
    if mode == "missing-managed":
        assert any(message in result.stderr for message in (
            "No interpreter found", "managed-python-not-prepared",
        ))
    if mode == "inside-runtime":
        assert "outside runtime and prefix closure" in result.stderr
    if receipt.exists():
        observed = json.loads(receipt.read_text())
        assert os.path.samefile(observed["executable"], selected)
        assert all(
            not Path(value).resolve().is_relative_to(root.resolve()) for value in observed.values()
        )
    if mode == "missing-managed":
        assert not any((root.parent / "empty-managed-store").rglob("*"))


def _supply_inside_candidate(
    scripts: Path, root: Path, selected: Path, environment: dict[str, str],
) -> None:
    inside = root / "versions" / "fixture"
    result = _run(
        [str(selected), "-I", "-B", "-m", "venv", "--without-pip", str(inside)],
        root.parent, {key: value for key, value in environment.items() if key not in HINTS},
    )
    assert result.returncode == 0, result.stderr
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    environment["INSIDE_SELECTED"] = str(inside / executable)
    (scripts / "bootstrap-uv.sh").write_text(
        'resolve_uv_bootstrap_python() { printf "%s\\n" "$INSIDE_SELECTED"; }\n',
    )
    (scripts / "bootstrap-uv.ps1").write_text(
        "function Resolve-UvBootstrapPython {\n"
        " param($RequestFile,$InvokeCaptured,$Operation,[switch]$IsolateSelection)\n"
        " if ($Operation -cne 'Inspect' -or -not $IsolateSelection) { throw 'Wrong operation' }\n"
        " [pscustomobject]@{Status='selected';Python=$env:INSIDE_SELECTED}\n"
        "}\n",
    )


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX uninstall boundary")
@pytest.mark.parametrize("mode", ("prepared", "missing-uv", "missing-managed", "inside-runtime"))
def test_posix_uninstall_uses_prepared_managed_python(
    tmp_path: Path, managed: tuple[Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]], mode: str,
) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is unavailable")
    uv, selected = managed
    scripts, root, receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(selected.parents[2])
    _prepare_path(tmp_path, uv, environment, mode)
    if mode == "inside-runtime":
        _supply_inside_candidate(scripts, root, selected, environment)
    script = (
        "set -euo pipefail\n"
        f"SCRIPT_DIR={shlex.quote(str(scripts))};PLUGIN_DIR=${{SCRIPT_DIR%/*}}\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        '_fail() { printf "%s\\n" "$1" >&2; }\n'
        '. "$SCRIPT_DIR/uninstall-runtime.sh"\n'
        "_finite_runtime_uninstall --check\n"
    )
    result = _run([bash, "--noprofile", "--norc", "-c", script], tmp_path, environment)
    _assert_result(result, root, receipt, selected, mode)


@pytest.fixture(params=("pwsh", "powershell"))
def ps1(request: pytest.FixtureRequest) -> str:
    executable = shutil.which(str(request.param))
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


@pytest.mark.skipif(os.name != "nt", reason="Finite PowerShell cleanup requires native Windows")
@pytest.mark.parametrize("mode", ("prepared", "missing-uv", "missing-managed", "inside-runtime"))
def test_ps1_uninstall_uses_prepared_managed_python(
    ps1: str, tmp_path: Path, managed: tuple[Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]], mode: str,
) -> None:
    uv, selected = managed
    scripts, root, receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(selected.parents[1 if os.name == "nt" else 2])
    _prepare_path(tmp_path, uv, environment, mode)
    if mode == "inside-runtime":
        _supply_inside_candidate(scripts, root, selected, environment)
    invocation = scripts / "invoke.ps1"
    invocation.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        ". (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')\n"
        ". (Join-Path $PSScriptRoot 'uninstall-runtime.ps1')\n"
        "Invoke-FiniteRuntimeUninstall -InstallRoot $env:FIXTURE_ROOT -Check\n",
    )
    environment["FIXTURE_ROOT"] = str(root)
    result = _run([ps1, "-NoProfile", "-NonInteractive", "-File", str(invocation)],
                  tmp_path, environment)
    _assert_result(result, root, receipt, selected, mode)
