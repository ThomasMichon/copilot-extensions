"""Opted-in commands keep generation selection while uv owns Python launch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from test_generate import _manifest, generator


def _installed_powershell_shim() -> str:
    source = (Path(__file__).resolve().parents[3] /
              "plugins/budget-guidance/scripts/install.ps1").read_text(encoding="utf-8")
    return source.split("$ps1Content = @'\n", 1)[1].split(
        "\n'@\n    [System.IO.File]::WriteAllText($ps1Path", 1,
    )[0]


_UV_HINTS = (
    "UV_PYTHON", "UV_PYTHON_PREFERENCE", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON",
    "VIRTUAL_ENV", "CONDA_PREFIX", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY",
    "UV_ISOLATED", "UV_ENV_FILE", "UV_PROJECT", "UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC",
    "UV_CONFIG_FILE",
)


def _same_host_wrapper(shim: Path, *, legacy: bool = False) -> str:
    return (
        ("$PSNativeCommandArgumentPassing = 'Legacy'\n" if legacy else "") +
        "$before = @{}\n"
        f"foreach ($key in @({','.join(repr(key) for key in _UV_HINTS)})) {{\n"
        "    $before[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')\n}\n"
        "function uv { throw 'A PowerShell function must not replace the uv application' }\n"
        "$applicationArguments = @($args)\n"
        "if ($env:FIXTURE_ARGUMENTS) {\n"
        "    $applicationArguments = ConvertFrom-Json $env:FIXTURE_ARGUMENTS\n"
        "}\n"
        "if (@($applicationArguments | Where-Object { $_ -isnot [string] }).Count) {\n"
        "    throw 'The test caller must supply a flat string argument vector'\n"
        "}\n"
        f"& '{str(shim).replace(chr(39), chr(39) * 2)}' @applicationArguments\n"
        "$code = $LASTEXITCODE\n"
        "foreach ($key in $before.Keys) {\n"
        "    $after = [Environment]::GetEnvironmentVariable($key, 'Process')\n"
        "    if ($after -cne $before[$key]) { throw ('Caller environment changed: ' + $key) }\n"
        "}\nexit $code\n"
    )


@pytest.mark.parametrize("kind", [
    "sh", "installed-sh", "ps1", "installed-ps1", "ps1-legacy", "installed-ps1-legacy",
    "winps", "installed-winps",
])
@pytest.mark.parametrize("directory_hint", ["UV_WORKING_DIR", "UV_WORKING_DIRECTORY"])
def test_prepared_uv_command_preserves_generation_arguments_streams_and_exit(
    tmp_path: Path, kind: str, directory_hint: str, request: pytest.FixtureRequest,
) -> None:
    posix = kind in ("sh", "installed-sh")
    if posix and os.name == "nt":
        pytest.skip("POSIX shell execution is qualified on POSIX")
    if "winps" in kind and os.name != "nt":
        pytest.skip("Windows PowerShell execution is qualified on Windows")
    host = shutil.which("sh" if posix else "powershell.exe" if "winps" in kind else "pwsh")
    uv = shutil.which("uv")
    if not host or not uv:
        pytest.skip("This native control requires the addressed shell and uv")
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data.update(bootstrapPython="3.13.12", pythonLauncher="uv")
    manifest.write_text(json.dumps(data))
    (manifest.parent / "plugin.json").write_text('{"name":"agent-example"}')
    root = tmp_path / "home"
    root.mkdir()
    runtime = root / (".budget-guidance" if kind.startswith("installed-") else ".agent-example")
    runtime = runtime / "versions/selected"
    environment = {**os.environ, "UV_PYTHON_DOWNLOADS": "never"}
    for key in ("UV_PYTHON", "UV_NO_MANAGED_PYTHON", "UV_MANAGED_PYTHON",
                "UV_PYTHON_PREFERENCE", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    created = subprocess.run(
        [uv, "venv", str(runtime), "--python", "cpython@3.13.12", "--managed-python",
         "--no-python-downloads"],
        capture_output=True, text=True, env=environment, timeout=30,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if created.returncode and "No interpreter found" in created.stderr:
        pytest.skip("This acquisition-free control requires prepared managed CPython 3.13.12")
    assert created.returncode == 0, created.stderr
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    library = subprocess.check_output(
        [str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True, env=environment, timeout=10,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    ).strip()
    package = Path(library) / ("budget_guidance" if kind.startswith("installed-") else "agent_example")
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "print(json.dumps({'python':sys.executable,'args':sys.argv[1:],"
        "'stdin':sys.stdin.read(),'config':Path('caller-config.json').read_text()}))\n"
        "print('application diagnostic',file=sys.stderr)\n"
        "import os\nraise SystemExit(int(os.environ['FIXTURE_EXIT']))\n"
    )
    scripts = manifest.parent / "scripts"
    resolver = Path(__file__).resolve().parents[3] / "plugins/budget-guidance/scripts/resolve-runtime.sh"
    shutil.copyfile(resolver, scripts / "resolve-runtime.sh")
    selected_root = runtime.parents[1]
    (runtime / ".install-complete.json").write_text(json.dumps({
        "version": "selected", "completed_at": "fixture", "pid": 1,
    }))
    (selected_root / "current-version").write_text("incomplete\n")
    (selected_root / "last-known-good").write_text("selected\n")
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = '" + str(python).replace("'", "''") + "'\n",
    )
    assert generator.process_manifest(manifest, check=False) == []
    tools = tmp_path / "without-python"
    tools.mkdir()
    for name in (("uv",) if os.name == "nt" else (
        "uv", "dirname", "date", "sed", "mkdir", "tr", "awk", "grep", "sort", "cut",
    )):
        source = shutil.which(name)
        assert source
        shutil.copy2(source, tools / Path(source).name)
    env = {
        **environment, "HOME": str(root), "USERPROFILE": str(root), "PATH": str(tools),
        "UV_PYTHON": "must-not-be-selected", "UV_NO_MANAGED_PYTHON": "1",
        "UV_PYTHON_PREFERENCE": "only-system", "VIRTUAL_ENV": str(tmp_path / "foreign"),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_ISOLATED": "1", "UV_ENV_FILE": str(tmp_path / "missing.env"),
        "UV_PROJECT": str(tmp_path / "missing-project"),
        "UV_FROZEN": "1", "UV_LOCKED": "1", "UV_NO_SYNC": "1",
        "UV_CONFIG_FILE": str(tmp_path / "missing-config.toml"),
    }
    foreign = tmp_path / "foreign directory"
    foreign.mkdir()
    (tmp_path / "caller-config.json").write_text("caller configuration")
    (foreign / "caller-config.json").write_text("unrelated configuration")
    env[directory_hint] = str(foreign)
    shim = manifest.parent / "bin" / ("agent-example" if posix else "agent-example.ps1")
    if kind.startswith("installed-"):
        shim = tmp_path / ("installed.sh" if posix else "installed.ps1")
        content = _installed_powershell_shim()
        if posix:
            installer = resolver.with_name("install.sh").read_text(encoding="utf-8")
            content = installer.split("cat > \"$STUB\" << 'STUBEOF'\n", 1)[1].split(
                "\nSTUBEOF", 1,
            )[0]
        shim.write_text(content, encoding="utf-8")
        installed = root / ".budget-guidance/bin"
        installed.mkdir(parents=True)
        name = "resolve-runtime.sh" if posix else "resolve-runtime.ps1"
        shutil.copyfile(scripts / name, installed / name)
    if not posix:
        wrapper = tmp_path / "shadowed-uv.ps1"
        wrapper.write_text(_same_host_wrapper(shim, legacy=kind.endswith("-legacy")), encoding="utf-8")
        shim = wrapper
    arguments = [
        "", "space 'quoted' \"double\" \\slash &|<>()$?;", "λ", "line1\nline2",
        "trailing space\\", 'backslash before quote \\" and trailing \\\\',
        "--%", "%FIXTURE_EXIT%",
    ]
    command = [host, str(shim)] if posix else [host, "-NoProfile", "-File", str(shim)]
    for code in (0, 37, None):
        run_env = {
            **env, "FIXTURE_EXIT": str(code or 0), "FIXTURE_ARGUMENTS": json.dumps(arguments),
        }
        if code is None:
            run_env.update(UV_PYTHON_PREFERENCE="", UV_ISOLATED="", VIRTUAL_ENV="")
        started = time.monotonic()
        result = subprocess.run(
            [*command, *(arguments if posix else [])], input="input\nλ\n", capture_output=True, text=True,
            encoding="utf-8", env=run_env, cwd=tmp_path, timeout=30,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        request.node.user_properties.append((
            "launch_" + ("empty" if code is None else "cold" if code == 0 else "warm") + "_seconds",
            time.monotonic() - started,
        ))
        if code is None and "winps" in kind and result.returncode == 2:
            assert "--python-preference" in result.stderr
            assert not result.stdout
            continue
        assert result.returncode == (code or 0), result.stdout + result.stderr
        assert result.stderr.strip() == "application diagnostic"
        output = json.loads(result.stdout)
        assert Path(output["python"]).absolute() == python.absolute()
        assert output["args"] == arguments
        assert output["stdin"] == "input\nλ\n"
        assert output["config"] == "caller configuration"


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("failure", ["missing-uv", "missing-runtime", "empty-values"])
@pytest.mark.parametrize("engine", ["pwsh", "powershell.exe"] if os.name == "nt" else ["pwsh"])
def test_powershell_early_exit_restores_same_host_environment(
    tmp_path: Path, installed: bool, failure: str, engine: str,
) -> None:
    host = shutil.which(engine)
    uv = shutil.which("uv")
    if not host or not uv:
        pytest.skip("This native control requires PowerShell and uv")
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data.update(bootstrapPython="3.13.12", pythonLauncher="uv")
    manifest.write_text(json.dumps(data))
    (manifest.parent / "plugin.json").write_text('{"name":"agent-example"}')
    assert generator.process_manifest(manifest, check=False) == []
    shim = manifest.parent / "bin/agent-example.ps1"
    if installed:
        shim = tmp_path / "installed.ps1"
        shim.write_text(_installed_powershell_shim(), encoding="utf-8")
    wrapper = tmp_path / "caller.ps1"
    prefix = ""
    if failure == "empty-values":
        prefix = (
            "foreach ($key in @('UV_ISOLATED', 'UV_PYTHON_PREFERENCE', 'VIRTUAL_ENV')) {\n"
            "    [Environment]::SetEnvironmentVariable($key, '', 'Process')\n}\n"
        )
    wrapper.write_text(prefix + _same_host_wrapper(shim), encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    if os.name != "nt":
        mkdir = shutil.which("mkdir")
        assert mkdir
        shutil.copy2(mkdir, tools / "mkdir")
    if failure != "missing-uv":
        shutil.copy2(uv, tools / Path(uv).name)
    result = subprocess.run(
        [host, "-NoProfile", "-File", str(wrapper)], capture_output=True, text=True, timeout=20,
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
             "PATH": str(tools), "UV_PYTHON": "foreign", "VIRTUAL_ENV": "foreign",
             "UV_ISOLATED": "1", "UV_ENV_FILE": "missing.env", "UV_CONFIG_FILE": "missing.toml"},
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == (127 if failure == "missing-uv" else 1), result.stdout + result.stderr
    assert "Caller environment changed" not in result.stderr


@pytest.mark.parametrize("kind", ["sh", "ps1"])
def test_unprepared_uv_command_does_not_invoke_installer(tmp_path: Path, kind: str) -> None:
    if kind == "sh" and os.name == "nt":
        pytest.skip("POSIX shell execution is qualified on POSIX")
    host = shutil.which("sh" if kind == "sh" else "pwsh")
    if not host:
        pytest.skip("The addressed native shell is unavailable")
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data.update(bootstrapPython="3.13.12", pythonLauncher="uv")
    manifest.write_text(json.dumps(data))
    (manifest.parent / "plugin.json").write_text('{"name":"agent-example"}')
    scripts = manifest.parent / "scripts"
    (scripts / "install.sh").write_text("echo INSTALLER_MUST_NOT_RUN >&2; exit 97\n")
    (scripts / "install.ps1").write_text("throw 'INSTALLER_MUST_NOT_RUN'\n")
    assert generator.process_manifest(manifest, check=False) == []
    shim = manifest.parent / "bin" / ("agent-example" if kind == "sh" else "agent-example.ps1")
    command = [host, str(shim)] if kind == "sh" else [host, "-NoProfile", "-File", str(shim)]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=15, cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "runtime is not prepared" in result.stderr
    assert "INSTALLER_MUST_NOT_RUN" not in result.stderr


@pytest.mark.parametrize("launcher", [None, True, "unknown", "uv"])
def test_invalid_or_incomplete_uv_launch_declaration_refuses_before_writes(
    tmp_path: Path, launcher: object,
) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["pythonLauncher"] = launcher
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="pythonLauncher|bootstrap declaration"):
        generator.process_manifest(manifest, check=False)
    assert not (manifest.parent / "bin").exists()


@pytest.mark.parametrize("kind", ["ps1", "winps", "cmd", "sh"])
@pytest.mark.parametrize("failure", [None, "missing-uv", "unprepared"])
def test_generated_managed_bootstrap_ignores_path_python(
    tmp_path: Path, kind: str, failure: str | None,
) -> None:
    if kind == "sh" and os.name == "nt" or kind in ("cmd", "winps") and os.name != "nt":
        pytest.skip("This control requires the native shell")
    host = shutil.which(
        "bash" if kind == "sh" else "cmd.exe" if kind == "cmd"
        else "powershell.exe" if kind == "winps" else "pwsh",
    )
    uv = shutil.which("uv")
    if not host or not uv:
        pytest.skip("This native control requires the addressed shell and uv")
    environment = {**os.environ, "UV_PYTHON_DOWNLOADS": "never"}
    for key in _UV_HINTS:
        environment.pop(key, None)
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", ">=3.10"],
        capture_output=True, text=True, env=environment, timeout=15,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires a prepared managed interpreter")
    python = selected.stdout.strip()
    version = subprocess.check_output(
        [python, "-I", "-S", "-B", "-c",
         "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        text=True, env=environment, timeout=15,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    ).strip()
    manifest = _manifest(tmp_path)
    scripts = manifest.parent / "scripts"
    (manifest.parent / "plugin.json").write_text('{"name":"agent-example"}')
    (scripts / "probe.ps1").write_text(
        "& (Join-Path $PSScriptRoot 'installation-context\\shell-caller-owned.ps1') "
        "-Worker (Join-Path $PSScriptRoot 'worker.py') -Request @{arguments=@($args)}\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    (scripts / "probe.sh").write_text(
        '#!/bin/sh\n'
        'script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
        'py=$(bash "$script_dir/_bootstrap_python/select.sh") || exit\n'
        'exec "$py" -I -S -B "$script_dir/worker.py" "$@"\n',
        encoding="utf-8",
    )
    (scripts / "probe.sh").chmod(0o755)
    (scripts / "worker.py").write_text(
        "import json,os,sys\n"
        "def report(arguments):\n"
        "    print(json.dumps({'python':sys.executable,'arguments':arguments}))\n"
        "    return 37\n"
        "def main(request):\n"
        "    sys.path.insert(0,os.environ['FIXTURE_ARCHIVE'])\n"
        "    from agent_procutil._shell_caller_owned import serve\n"
        "    return serve(lambda:report(request['arguments']), **request['lifetime'])\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(report(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    context = scripts / "installation-context"
    context.mkdir()
    source = Path(__file__).resolve().parents[3] / "libs/installation-context"
    shutil.copyfile(source / "shell-caller-owned.ps1", context / "shell-caller-owned.ps1")
    data = json.loads(manifest.read_text())
    data.update(
        bootstrapPython=version, bootstrapLauncher="uv",
        payloadDispatcher={"posix": "scripts/probe.sh", "windows": "scripts/probe.ps1"},
    )
    manifest.write_text(json.dumps(data))
    assert generator.process_manifest(manifest, check=False) == []
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy2(uv, tools / Path(uv).name)
    old_python = os.environ.get("TEST_OLD_PYTHON")
    if old_python:
        old_version = subprocess.check_output(
            [old_python, "-I", "-S", "-c", "import sys; print(sys.version_info < (3,10))"],
            text=True, timeout=15, env=environment,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        assert old_version.strip() == "True"
        old_directory = str(Path(old_python).parent)
    elif os.name == "nt":
        # A corrupt executable is a stronger negative control than a compatible Python.
        (tools / "python.exe").write_bytes(b"PATH Python must not run")
        old_directory = str(tools)
    else:
        for name in ("python", "python3"):
            (tools / name).write_text("#!/bin/sh\necho PATH_PYTHON_MUST_NOT_RUN >&2\nexit 98\n")
            (tools / name).chmod(0o755)
        old_directory = str(tools)
    if os.name != "nt":
        for name in ("bash", "dirname", "wc", "cat"):
            executable = shutil.which(name)
            assert executable
            shutil.copy2(executable, tools / name)
    environment.update(
        PATH=os.pathsep.join((old_directory, str(tools))),
        FIXTURE_ARCHIVE=str(source / "daemon_instance_runtime.pyz"),
        COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON="",
        UV_PYTHON="must-not-be-selected", UV_NO_MANAGED_PYTHON="1",
        VIRTUAL_ENV=str(tmp_path / "unrelated environment"),
    )
    if failure == "missing-uv":
        (tools / Path(uv).name).unlink()
    if failure == "unprepared":
        environment["UV_PYTHON_INSTALL_DIR"] = str(tmp_path / "unprepared")
    suffix = ".cmd" if kind == "cmd" else "" if kind == "sh" else ".ps1"
    shim = manifest.parent / "bin" / ("agent-example" + suffix)
    command = (
        [host, "/d", "/c", str(shim), "literal", "argument"]
        if kind == "cmd" else [host, str(shim), "literal", "argument"]
        if kind == "sh" else [host, "-NoProfile", "-File", str(shim), "literal", "argument"]
    )
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=45,
        env=environment, cwd=tmp_path,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if failure:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not result.stdout
        assert (
            "uv-not-on-path" if failure == "missing-uv" and kind != "sh"
            else "uv is unavailable" if failure == "missing-uv"
            else "managed-python-not-prepared" if kind != "sh"
            else "No interpreter found"
        ) in result.stderr
        assert not (tmp_path / "unprepared").exists()
        return
    assert result.returncode == 37, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert Path(value["python"]).samefile(python)
    assert value["arguments"] == ["literal", "argument"]
    assert not result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer admission")
@pytest.mark.parametrize("selection", ["managed", "unavailable", "explicit"])
def test_first_use_bootstrap_candidates_do_not_depend_on_path_python(
    tmp_path: Path, selection: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    scripts = tmp_path / "scripts"
    bootstrap = scripts / "_bootstrap_python"
    bootstrap.mkdir(parents=True)
    (scripts / "bootstrap-python-launcher.txt").write_text("uv\n")
    (bootstrap / "select.sh").write_text(
        'printf "uv is unavailable\\n" >&2; exit 17\n'
        if selection == "unavailable" else 'printf "%s\\n" "$TEST_SELECTED"\n',
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("python", "python3"):
        (tools / name).write_text("#!/bin/sh\nexit 98\n")
        (tools / name).chmod(0o755)
    source = (
        Path(__file__).resolve().parents[3] / "plugins/agent-worktrees/scripts/install.sh"
    ).read_text(encoding="utf-8")
    body = source.split("_bootstrap_python_candidates() {", 1)[1].split(
        "# A structured caller supplies", 1,
    )[0]
    environment = {
        **os.environ, "PATH": str(tools), "TEST_SELECTED": sys.executable,
    }
    environment.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    expected = sys.executable
    if selection == "explicit":
        expected = str(tmp_path / "explicit-python")
        environment["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"] = expected
    result = subprocess.run(
        [bash, "-c", 'SCRIPT_DIR="$1"\n_bootstrap_python_candidates() {' + body +
         "\n_bootstrap_python_candidates\n", "admission", str(scripts)],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    if selection == "unavailable":
        assert result.returncode != 0
        assert not result.stdout
        assert "uv is unavailable" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected + "\n"
