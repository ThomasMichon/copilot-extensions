"""Opted-in commands keep generation selection while uv owns Python launch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

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
)


def _same_host_wrapper(shim: Path) -> str:
    return (
        "$before = @{}\n"
        f"foreach ($key in @({','.join(repr(key) for key in _UV_HINTS)})) {{\n"
        "    $before[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')\n}\n"
        "function uv { throw 'A PowerShell function must not replace the uv application' }\n"
        f"& '{str(shim).replace(chr(39), chr(39) * 2)}' @args\n"
        "$code = $LASTEXITCODE\n"
        "foreach ($key in $before.Keys) {\n"
        "    $after = [Environment]::GetEnvironmentVariable($key, 'Process')\n"
        "    if ($after -cne $before[$key]) { throw ('Caller environment changed: ' + $key) }\n"
        "}\nexit $code\n"
    )


@pytest.mark.parametrize("kind", ["sh", "ps1", "installed-ps1", "winps", "installed-winps"])
@pytest.mark.parametrize("directory_hint", ["UV_WORKING_DIR", "UV_WORKING_DIRECTORY"])
def test_prepared_uv_command_preserves_generation_arguments_streams_and_exit(
    tmp_path: Path, kind: str, directory_hint: str,
) -> None:
    if kind == "sh" and os.name == "nt":
        pytest.skip("POSIX shell execution is qualified on POSIX")
    if "winps" in kind and os.name != "nt":
        pytest.skip("Windows PowerShell execution is qualified on Windows")
    host = shutil.which("sh" if kind == "sh" else "powershell.exe" if "winps" in kind else "pwsh")
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
    runtime = root / ".agent-example/versions/selected"
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
    (scripts / "resolve-runtime.sh").write_text("AGENT_RT_PY=" + shlex.quote(str(python)) + "\n")
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = '" + str(python).replace("'", "''") + "'\n",
    )
    assert generator.process_manifest(manifest, check=False) == []
    tools = tmp_path / "without-python"
    tools.mkdir()
    for name in (("uv",) if os.name == "nt" else ("uv", "dirname", "date", "sed", "mkdir")):
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
    }
    foreign = tmp_path / "foreign directory"
    foreign.mkdir()
    (tmp_path / "caller-config.json").write_text("caller configuration")
    (foreign / "caller-config.json").write_text("unrelated configuration")
    env[directory_hint] = str(foreign)
    shim = manifest.parent / "bin" / ("agent-example" if kind == "sh" else "agent-example.ps1")
    if kind.startswith("installed-"):
        shim = tmp_path / "installed.ps1"
        shim.write_text(_installed_powershell_shim(), encoding="utf-8")
        installed = root / ".budget-guidance/bin"
        installed.mkdir(parents=True)
        shutil.copyfile(scripts / "resolve-runtime.ps1", installed / "resolve-runtime.ps1")
    if kind != "sh":
        wrapper = tmp_path / "shadowed-uv.ps1"
        wrapper.write_text(_same_host_wrapper(shim), encoding="utf-8")
        shim = wrapper
    arguments = ["", "space 'quoted' \"double\" \\slash &|<>()$?;", "λ", "line1\nline2"]
    command = [host, str(shim)] if kind == "sh" else [host, "-NoProfile", "-File", str(shim)]
    for code in (0, 37, None):
        run_env = {**env, "FIXTURE_EXIT": str(code or 0)}
        if code is None:
            run_env.update(UV_PYTHON_PREFERENCE="", UV_ISOLATED="", VIRTUAL_ENV="")
        result = subprocess.run(
            [*command, *arguments], input="input\nλ\n", capture_output=True, text=True,
            encoding="utf-8", env=run_env, cwd=tmp_path, timeout=30,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        expected = 2 if code is None and "winps" in kind else code or 0
        assert result.returncode == expected, result.stdout + result.stderr
        if expected == 2:
            assert "--python-preference" in result.stderr
            assert not result.stdout
            continue
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
             "UV_ISOLATED": "1", "UV_ENV_FILE": "missing.env"},
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
