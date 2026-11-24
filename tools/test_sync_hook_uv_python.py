from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

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


def _run_helper(bash: str, env: dict[str, str], *python_args: str) -> subprocess.CompletedProcess[str]:
    helper = (CANONICAL / "hook-uv-python.sh").as_posix()
    return subprocess.run(
        [bash, "-c", f'. "{helper}"; hook_uv_python "$@"; echo "rc=$?"', "hook", *python_args],
        capture_output=True, text=True, env=env, timeout=300, check=False,
    )


def test_sh_helper_fails_clearly_without_uv(tmp_path: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    bash_dir = str(Path(bash).parent)
    path = os.pathsep.join([bash_dir, str(Path(bash).parent.parent / "usr" / "bin")])
    if shutil.which("uv", path=path):
        pytest.skip("uv lives next to bash on this host")
    env = {"PATH": path, "HOME": str(tmp_path), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    result = _run_helper(bash, env, "-c", "print(1)")
    assert "rc=127" in result.stdout
    assert "uv is required" in result.stderr
    assert "A system Python is never used" in result.stderr


def test_sh_helper_runs_pinned_managed_python() -> None:
    bash = _bash()
    if bash is None or shutil.which("uv") is None:
        pytest.skip("bash and uv are required")
    env = dict(os.environ)
    env["UV_PYTHON"] = "3.9"
    env["PYTHONPATH"] = "/nonexistent"
    result = _run_helper(
        bash, env, "-c",
        "import os, sys; print(sys.version_info[:2], os.environ.get('PYTHONPATH'))",
    )
    assert "rc=0" in result.stdout, result.stderr
    assert "(3, 11) None" in result.stdout


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def test_ps1_helper_fails_clearly_without_uv_and_restores_env(tmp_path: Path) -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")
    helper = str(CANONICAL / "hook-uv-python.ps1")
    script = (
        f". '{helper}'; $env:PATH = ''; "
        "Invoke-HookUvPython -ArgumentList @('-c', 'print(1)'); "
        "[Console]::Out.WriteLine('rc=' + $LASTEXITCODE + ' pp=' + $env:PYTHONPATH)"
    )
    env = dict(os.environ)
    env.update({"USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "PYTHONPATH": "kept"})
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert "rc=127 pp=kept" in result.stdout, result.stdout + result.stderr
    assert "uv is required" in result.stderr
