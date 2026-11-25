"""Uninstall inspects prepared managed Python without PATH interpreter fallback."""
from __future__ import annotations

import json
import os
import re
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
DURABLE = {
    "config.json": b'{"retained":true}\n',
    "auth.json": b'{"identity":"retained"}\n',
    "queue.db": b"\x00queued work\xff",
    "unknown/precious": b"unknown state",
}
UNSAFE = {"inside-runtime", "physical-alias", "internal-base"}


def _run(
    arguments: list[str], directory: Path, environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed hosts and fixture-owned argument vectors
        arguments, cwd=directory, env=environment, capture_output=True,
        text=True, encoding="utf-8", timeout=60, creationflags=NO_WINDOW,
    )


@pytest.fixture(scope="module")
def managed() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("Prepared uv is unavailable")
    declaration = json.loads((PLUGIN / "payload-invocation.json").read_text())
    environment = {
        key: value for key, value in os.environ.items()
        if key not in HINTS
        and not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_PLUGIN_"))
        and key not in {"COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"}
    }
    result = subprocess.run(  # noqa: S603 - selected uv and the project's declared managed request
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@" + declaration["bootstrapPython"]],
        capture_output=True, text=True, timeout=30, env=environment, creationflags=NO_WINDOW,
    )
    if result.returncode:
        pytest.skip("The declared managed interpreter has not been prepared")
    directory = subprocess.run(  # noqa: S603 - query the selected uv's actual managed storage
        [uv, "python", "dir"], capture_output=True, text=True, timeout=30,
        env=environment, creationflags=NO_WINDOW,
    )
    assert directory.returncode == 0, directory.stderr
    return Path(uv), Path(result.stdout.strip()), Path(directory.stdout.strip())


@pytest.fixture
def payload(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    target = tmp_path / "payload"
    _copy_payload(target)
    root = tmp_path / "installed"
    root.mkdir()
    for name, value in DURABLE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
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
    environment = {
        key: value for key, value in os.environ.items()
        if key not in HINTS
        and not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_PLUGIN_"))
        and key not in {"COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"}
    }
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
        for name in (
            "bash", "dirname", "wc", "cat", "mkdir", "sed", "head", "tr",
            "ln", "date", "readlink", "sleep",
        ):
            executable = shutil.which(name)
            assert executable, name
            (tools / name).symlink_to(executable)
        flock = shutil.which("flock")
        if flock:
            (tools / "flock").symlink_to(flock)
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
    for name, value in DURABLE.items():
        assert (root / name).read_bytes() == value
    if mode == "missing-uv":
        assert any(message in result.stderr for message in ("uv is unavailable", "uv-not-on-path"))
    if mode == "missing-managed":
        assert any(message in result.stderr for message in (
            "No interpreter found", "managed-python-not-prepared",
        ))
    if mode in UNSAFE:
        assert "outside runtime and prefix closure" in result.stderr
    if receipt.exists():
        observed = json.loads(receipt.read_text())
        assert os.path.samefile(observed["executable"], selected)
        assert all(
            not Path(value).resolve().is_relative_to(root.resolve()) for value in observed.values()
        )
    if mode == "missing-managed":
        assert not any((root.parent / "empty-managed-store").rglob("*"))


def _make_venv(directory: Path, selected: Path, environment: dict[str, str]) -> Path:
    result = _run(
        [str(selected), "-I", "-B", "-m", "venv", "--without-pip", str(directory)],
        directory.parent, {key: value for key, value in environment.items() if key not in HINTS},
    )
    assert result.returncode == 0, result.stderr
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return directory / executable


def _link_directory(
    alias: Path, target: Path, environment: dict[str, str], host: str | None,
) -> None:
    if os.name != "nt":
        alias.symlink_to(target, target_is_directory=True)
        return
    assert host is not None
    def quote(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    result = _run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         f"New-Item -ItemType Junction -Path {quote(alias)} -Target {quote(target)} | Out-Null"],
        alias.parent, environment,
    )
    assert result.returncode == 0, result.stderr


def _supply_unsafe_candidate(
    mode: str, scripts: Path, root: Path, selected: Path, environment: dict[str, str],
    host: str | None = None,
) -> None:
    if mode == "internal-base":
        assert os.name == "nt"
        candidate = _make_venv(root.parent / "external", selected, environment)
        internal = root / "base"
        internal.mkdir()
        copied = internal / selected.name
        shutil.copyfile(selected, copied)
        for library in selected.parent.glob("*.dll"):
            shutil.copyfile(library, internal / library.name)
        for name in ("Lib", "DLLs"):
            if (selected.parent / name).is_dir():
                _link_directory(internal / name, selected.parent / name, environment, host)
        marker = candidate.parent.parent / "pyvenv.cfg"
        text = re.sub(r"(?m)^home = .*", f"home = {internal.as_posix()}", marker.read_text())
        text = re.sub(r"(?m)^executable = .*", f"executable = {copied.as_posix()}", text)
        marker.write_text(text)
        evidence = _run(
            [str(candidate), "-I", "-B", "-c",
             "import json,sys;print(json.dumps([sys.executable,sys.prefix,sys.base_prefix]))"],
            root.parent, environment,
        )
        assert evidence.returncode == 0, evidence.stderr
        executable, prefix, base_prefix = map(Path, json.loads(evidence.stdout))
        assert not executable.resolve().is_relative_to(root.resolve())
        assert not prefix.resolve().is_relative_to(root.resolve())
        assert base_prefix.resolve().is_relative_to(root.resolve())
    else:
        inside = root / "versions" / "fixture"
        inside.parent.mkdir()
        candidate = _make_venv(inside, selected, environment)
        if mode == "physical-alias":
            alias = root.parent / "outside-alias"
            _link_directory(alias, inside, environment, host)
            candidate = alias / candidate.relative_to(inside)
            assert alias.resolve().is_relative_to(root.resolve())
            assert not candidate.is_relative_to(root)
        evidence = _run(
            [str(candidate), "-I", "-B", "-c", "import sys;print(sys.prefix)"],
            root.parent, environment,
        )
        assert evidence.returncode == 0, evidence.stderr
        assert Path(evidence.stdout.strip()).resolve().is_relative_to(root.resolve())
    environment["INSIDE_SELECTED"] = str(candidate)
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
@pytest.mark.parametrize("mode", (
    "prepared", "missing-uv", "missing-managed", "inside-runtime", "physical-alias",
))
def test_posix_uninstall_uses_prepared_managed_python(
    tmp_path: Path, managed: tuple[Path, Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]], mode: str,
) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is unavailable")
    uv, selected, store = managed
    scripts, root, receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(store)
    _prepare_path(tmp_path, uv, environment, mode)
    if mode in UNSAFE:
        _supply_unsafe_candidate(mode, scripts, root, selected, environment)
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


def _invoke_ps1(
    host: str, scripts: Path, root: Path, environment: dict[str, str], *, remove: bool = False,
) -> subprocess.CompletedProcess[str]:
    invocation = scripts / "invoke.ps1"
    invocation.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        ". (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')\n"
        ". (Join-Path $PSScriptRoot 'uninstall-runtime.ps1')\n"
        "Invoke-FiniteRuntimeUninstall -InstallRoot $env:FIXTURE_ROOT"
        + ("\n" if remove else " -Check\n"),
    )
    environment["FIXTURE_ROOT"] = str(root)
    return _run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(invocation)],
        root.parent, environment,
    )


@pytest.mark.skipif(os.name != "nt", reason="Finite PowerShell cleanup requires native Windows")
@pytest.mark.parametrize("mode", (
    "prepared", "missing-uv", "missing-managed", "inside-runtime",
    "physical-alias", "internal-base",
))
def test_ps1_uninstall_uses_prepared_managed_python(
    ps1: str, tmp_path: Path, managed: tuple[Path, Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]], mode: str,
) -> None:
    uv, selected, store = managed
    scripts, root, receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(store)
    _prepare_path(tmp_path, uv, environment, mode)
    if mode in UNSAFE:
        _supply_unsafe_candidate(mode, scripts, root, selected, environment, ps1)
    result = _invoke_ps1(ps1, scripts, root, environment)
    _assert_result(result, root, receipt, selected, mode)


@pytest.mark.skipif(os.name != "nt", reason="Finite PowerShell cleanup requires native Windows")
def test_ps1_prepared_managed_cleanup_preserves_durable_and_unknown_state(
    ps1: str, tmp_path: Path, managed: tuple[Path, Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    uv, selected, store = managed
    scripts, root, receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(store)
    _prepare_path(tmp_path, uv, environment, "prepared")
    runtime_python = _make_venv(root / ".venv", selected, environment)
    checked = _invoke_ps1(ps1, scripts, root, environment)
    _assert_result(checked, root, receipt, selected, "prepared")
    assert runtime_python.is_file()
    removed = _invoke_ps1(ps1, scripts, root, environment, remove=True)
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not (root / ".venv").exists()
    assert not (root / "deploy-manifest.json").exists()
    for name, value in DURABLE.items():
        assert (root / name).read_bytes() == value


def _foreign_manifest(root: Path) -> dict[str, tuple[bytes, int]]:
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "foreign", "source": {"plugin": "foreign"},
    }))
    return {
        name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns)
        for name in (*DURABLE, "deploy-manifest.json")
    }


def _assert_foreign(
    result: subprocess.CompletedProcess[str], root: Path, before: dict[str, tuple[bytes, int]],
) -> None:
    assert result.returncode != 0, result.stdout + result.stderr
    assert "foreign" in (result.stdout + result.stderr).lower(), result.stdout + result.stderr
    assert {
        name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) for name in before
    } == before


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX uninstall boundary")
def test_posix_prepared_installer_refuses_foreign_ownership(
    tmp_path: Path, managed: tuple[Path, Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is unavailable")
    uv, _selected, store = managed
    scripts, root, _receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(store)
    _prepare_path(tmp_path, uv, environment, "prepared")
    before = _foreign_manifest(root)
    result = _run(
        [bash, str(scripts / "install.sh"), "uninstall", "--install-dir", str(root)],
        tmp_path, environment,
    )
    _assert_foreign(result, root, before)


@pytest.mark.skipif(os.name != "nt", reason="Finite PowerShell cleanup requires native Windows")
def test_ps1_prepared_installer_refuses_foreign_ownership(
    ps1: str, tmp_path: Path, managed: tuple[Path, Path, Path],
    payload: tuple[Path, Path, Path, dict[str, str]],
) -> None:
    uv, _selected, store = managed
    scripts, root, _receipt, environment = payload
    environment["UV_PYTHON_INSTALL_DIR"] = str(store)
    _prepare_path(tmp_path, uv, environment, "prepared")
    before = _foreign_manifest(root)
    result = _run(
        [ps1, "-NoProfile", "-NonInteractive", "-File", str(scripts / "install.ps1"),
         "-Action", "uninstall", "-InstallDir", str(root)],
        tmp_path, environment,
    )
    _assert_foreign(result, root, before)
