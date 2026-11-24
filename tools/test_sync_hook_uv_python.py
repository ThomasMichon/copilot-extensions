from __future__ import annotations

import importlib.util
import json
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "sync-hook-uv-python.py"
CANONICAL = REPO / "libs" / "hook-uv-python"

_SPEC = importlib.util.spec_from_file_location("sync_hook_uv_python", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
sync_hook_uv_python = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_hook_uv_python)


def _bash() -> str | None:
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    canonical = tmp_path / "libs" / "hook-uv-python"
    canonical.mkdir(parents=True)
    for name in sync_hook_uv_python.HELPER_NAMES:
        shutil.copyfile(CANONICAL / name, canonical / name)
    (tmp_path / ".python-version").write_text("3.11\n", encoding="utf-8")
    monkeypatch.setattr(sync_hook_uv_python, "ADOPTERS", {"demo": (".sh", ".ps1")})
    scripts = tmp_path / "plugins" / "demo" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "write-session-guidance.sh").write_text(
        '. "$helper"\nhook_uv_python "$script" || printf \'{}\'\n', encoding="utf-8",
    )
    (scripts / "write-session-guidance.ps1").write_text(
        ". $helper\nInvoke-HookUvPython -ArgumentList @($script)\n", encoding="utf-8",
    )
    sync_hook_uv_python.sync(tmp_path)
    return tmp_path


def test_repository_is_in_sync() -> None:
    assert sync_hook_uv_python.check() == []


def test_check_reports_drift_missing_call_and_system_python(fake_repo: Path) -> None:
    assert sync_hook_uv_python.check(fake_repo) == []
    scripts = fake_repo / "plugins" / "demo" / "scripts"
    (scripts / "hook-uv-python.ps1").write_text("drift\n", encoding="utf-8")
    (scripts / "write-session-guidance.sh").write_text(
        'python="$(command -v python3 || true)"\n"$python" "$script"\n', encoding="utf-8",
    )
    problems = sync_hook_uv_python.check(fake_repo)
    assert any("hook-uv-python.ps1 differs" in p for p in problems)
    assert any("does not call the uv-Python helper" in p for p in problems)
    assert any("still searches for a system Python" in p for p in problems)


def test_check_reports_version_mismatch(fake_repo: Path) -> None:
    (fake_repo / ".python-version").write_text("3.12\n", encoding="utf-8")
    problems = sync_hook_uv_python.check(fake_repo)
    assert sum("pins 3.11; .python-version is 3.12" in p for p in problems) == 2


PINNED_ARGS = [
    "run", "--quiet", "--isolated", "--no-project", "--no-config", "--no-env-file",
    "--no-python-downloads", "--managed-python", "--python", "3.11", "python",
]
SANITIZED = ("UV_PYTHON", "UV_CONFIG_FILE", "VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT")
FAKE_UV = """import json, os, sys
names = %r
record = {"argv": sys.argv[1:], "env": {n: os.environ.get(n) for n in names},
          "stdin": sys.stdin.read()}
with open(os.environ["FAKE_UV_RECORD"], "w", encoding="utf-8") as handle:
    json.dump(record, handle)
sys.exit(int(os.environ.get("FAKE_UV_RC", "0")))
""" % (SANITIZED,)


def _fake_uv(directory: Path) -> Path:
    """Write a fake `uv` that records argv, selected env and stdin; it never downloads."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "fake_uv.py"
    script.write_text(FAKE_UV, encoding="utf-8")
    python = Path(sys.executable)
    if os.name == "nt":
        (directory / "uv.cmd").write_text(
            f'@"{python}" "{script}" %*\r\n@exit /b %ERRORLEVEL%\r\n', encoding="utf-8",
        )
    shim = directory / "uv"
    shim.write_text(f'#!/bin/sh\nexec "{python.as_posix()}" "{script.as_posix()}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return directory


def _caller_env(tmp_path: Path, fake_dir: Path, rc: int) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(fake_dir), env.get("PATH", "")])
    env.update({
        "FAKE_UV_RECORD": str(tmp_path / "record.json"),
        "FAKE_UV_RC": str(rc),
        "UV_PYTHON": "3.9",
        "UV_CONFIG_FILE": str(tmp_path / "missing-uv.toml"),
        "VIRTUAL_ENV": str(tmp_path / ".venv"),
        "PYTHONPATH": "kept",
        "UV_PROJECT": str(tmp_path),
    })
    return env


def _record(tmp_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))
    return data


def _run_helper(
    bash: str, env: dict[str, str], *python_args: str, stdin: str = "", cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    helper = (CANONICAL / "hook-uv-python.sh").as_posix()
    return subprocess.run(
        [bash, "-c", f'. "{helper}"; hook_uv_python "$@"; echo "rc=$? pp=$PYTHONPATH"', "hook",
         *python_args],
        input=stdin, capture_output=True, text=True, env=env, cwd=cwd, timeout=120, check=False,
    )


def _require_bash() -> str:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    return bash


@pytest.mark.parametrize("rc", [0, 3])
def test_sh_helper_passes_pinned_args_sanitized_env_stdin_and_exit_code(tmp_path: Path, rc: int) -> None:
    bash = _require_bash()
    env = _caller_env(tmp_path, _fake_uv(tmp_path / "bin"), rc)
    result = _run_helper(bash, env, "-c", "print(1)", stdin="payload")
    assert f"rc={rc} pp=kept" in result.stdout, result.stdout + result.stderr
    record = _record(tmp_path)
    assert record["argv"] == [*PINNED_ARGS, "-c", "print(1)"]
    assert record["env"] == dict.fromkeys(SANITIZED)
    assert record["stdin"] == "payload"


def test_sh_helper_fails_clearly_without_uv(tmp_path: Path) -> None:
    bash = _require_bash()
    bash_dir = str(Path(bash).parent)
    path = os.pathsep.join([bash_dir, str(Path(bash).parent.parent / "usr" / "bin")])
    if shutil.which("uv", path=path):
        pytest.skip("uv lives next to bash on this host")
    env = {"PATH": path, "HOME": str(tmp_path), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    result = _run_helper(bash, env, "-c", "print(1)")
    assert "rc=127" in result.stdout
    assert "uv is required" in result.stderr
    assert "A system Python is never used" in result.stderr


def test_sh_helper_runs_prepared_python_without_download_config_or_ambient_venv(tmp_path: Path) -> None:
    bash = _require_bash()
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required")
    no_download = dict(os.environ, UV_PYTHON_DOWNLOADS="never")
    venv = subprocess.run(
        [uv, "venv", "--quiet", "--no-config", "--python", "3.11", str(tmp_path / ".venv")],
        capture_output=True, text=True, env=no_download, timeout=120, check=False,
    )
    if venv.returncode != 0:
        pytest.skip("uv-managed Python 3.11 is not prepared on this host")
    env = dict(no_download)
    env.update({
        "UV_PYTHON": "3.9",
        "PYTHONPATH": "/nonexistent",
        "UV_CONFIG_FILE": str(tmp_path / "missing-uv.toml"),
    })
    result = _run_helper(
        bash, env, "-c",
        "import os, sys; print(sys.version_info[:2], os.environ.get('PYTHONPATH'), sys.prefix)",
        cwd=tmp_path,
    )
    if "No interpreter found" in result.stderr:
        pytest.skip("uv-managed Python 3.11 is not prepared on this host")
    assert "rc=0" in result.stdout, result.stderr
    assert "(3, 11) None" in result.stdout
    assert str(tmp_path / ".venv") not in result.stdout


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _run_ps1(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")
    helper = str(CANONICAL / "hook-uv-python.ps1")
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", f". '{helper}'; {script}"],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )


@pytest.mark.parametrize("rc", [0, 3])
def test_ps1_helper_passes_pinned_args_sanitized_env_stdin_and_restores_env(tmp_path: Path, rc: int) -> None:
    if os.name != "nt" and _powershell() is None:
        pytest.skip("PowerShell is not available")
    env = _caller_env(tmp_path, _fake_uv(tmp_path / "bin"), rc)
    result = _run_ps1(
        "Invoke-HookUvPython -ArgumentList @('-c', 'print(1)') -InputText 'payload'; "
        "[Console]::Out.WriteLine('rc=' + $LASTEXITCODE + ' pp=' + $env:PYTHONPATH + "
        "' cfg=' + [bool]$env:UV_CONFIG_FILE + ' py=' + $env:UV_PYTHON)",
        env,
    )
    assert f"rc={rc} pp=kept cfg=True py=3.9" in result.stdout, result.stdout + result.stderr
    record = _record(tmp_path)
    assert record["argv"] == [*PINNED_ARGS, "-c", "print(1)"]
    assert record["env"] == dict.fromkeys(SANITIZED)
    assert record["stdin"].strip() == "payload"


def test_ps1_helper_fails_clearly_without_uv_and_restores_env(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.update({"USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "PYTHONPATH": "kept"})
    result = _run_ps1(
        "$env:PATH = ''; Invoke-HookUvPython -ArgumentList @('-c', 'print(1)'); "
        "[Console]::Out.WriteLine('rc=' + $LASTEXITCODE + ' pp=' + $env:PYTHONPATH)",
        env,
    )
    assert "rc=127 pp=kept" in result.stdout, result.stdout + result.stderr
    assert "uv is required" in result.stderr
