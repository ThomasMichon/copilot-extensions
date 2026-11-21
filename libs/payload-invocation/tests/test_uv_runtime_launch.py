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


@pytest.mark.parametrize("kind", ["sh", "ps1"])
def test_prepared_uv_command_preserves_generation_arguments_streams_and_exit(
    tmp_path: Path, kind: str,
) -> None:
    if kind == "sh" and os.name == "nt":
        pytest.skip("POSIX shell execution is qualified on POSIX")
    host = shutil.which("sh" if kind == "sh" else "pwsh")
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
    package = Path(library) / "agent_example"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(
        "import json,sys\n"
        "print(json.dumps({'python':sys.executable,'args':sys.argv[1:],"
        "'stdin':sys.stdin.read()}))\n"
        "print('application diagnostic',file=sys.stderr)\n"
        "raise SystemExit(37)\n"
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
    }
    shim = manifest.parent / "bin" / ("agent-example" if kind == "sh" else "agent-example.ps1")
    if kind == "ps1":
        wrapper = tmp_path / "shadowed-uv.ps1"
        wrapper.write_text(
            "function uv { throw 'A PowerShell function must not replace the uv application' }\n"
            "& $env:FIXTURE_SHIM @args\nexit $LASTEXITCODE\n",
        )
        env["FIXTURE_SHIM"] = str(shim)
        shim = wrapper
    arguments = ["", "space 'quoted' \"double\" \\slash &|<>()$?;", "λ", "line1\nline2"]
    command = [host, str(shim)] if kind == "sh" else [host, "-NoProfile", "-File", str(shim)]
    for _ in range(2):
        result = subprocess.run(
            [*command, *arguments], input="input\nλ\n", capture_output=True, text=True,
            encoding="utf-8", env=env, cwd=tmp_path, timeout=30,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        assert result.returncode == 37, result.stdout + result.stderr
        assert result.stderr.strip() == "application diagnostic"
        output = json.loads(result.stdout)
        assert Path(output["python"]).absolute() == python.absolute()
        assert output["args"] == arguments
        assert output["stdin"] == "input\nλ\n"


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
