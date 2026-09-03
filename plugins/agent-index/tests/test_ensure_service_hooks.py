from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


def test_session_start_python_probes_are_isolated_and_origin_checked() -> None:
    bootstrap_sh = (PLUGIN / "scripts" / "bootstrap-check.sh").read_text(
        encoding="utf-8"
    )
    bootstrap_ps = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(
        encoding="utf-8"
    )
    ensure_sh = (PLUGIN / "scripts" / "ensure-service.sh").read_text(
        encoding="utf-8"
    )
    ensure_ps = (PLUGIN / "scripts" / "ensure-service.ps1").read_text(
        encoding="utf-8"
    )

    assert "agent_index.__file__" in bootstrap_sh
    assert "agent_index.__file__" in bootstrap_ps
    assert '"$python" -I -X utf8 -c' in bootstrap_sh
    assert "& $Python -I -X utf8 -c" in bootstrap_ps
    assert '"$py" -I -X utf8 "$script_dir/resolve-activation-role.py"' in ensure_sh
    assert '"$probe_python" -I -X utf8 -' in ensure_sh
    assert "& $python.Source -I -X utf8 $resolver" in ensure_ps
    assert "-E -X utf8" not in ensure_sh
    assert "-E -X utf8" not in ensure_ps


def _shell_command(shell: str, script: Path) -> list[str]:
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX ensure-service test")
        executable = shutil.which("bash")
        if executable is None:
            pytest.skip("bash is unavailable")
        return [executable, str(script)]
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is unavailable")
    return [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def _fixture(
    tmp_path: Path,
    shell: str,
    role: str | None,
) -> tuple[Path, Path, dict[str, str]]:
    payload = tmp_path / f"payload-{shell}"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "ps1" if shell == "powershell" else "sh"
    ensure = scripts / f"ensure-service.{suffix}"
    shutil.copy2(PLUGIN / "scripts" / ensure.name, ensure)
    marker = tmp_path / f"{shell}-runtime-gate-called"
    runtime_gate = scripts / f"runtime-gate.{suffix}"
    if shell == "powershell":
        runtime_gate.write_text(
            "[IO.File]::WriteAllText($env:HOOK_MARKER, 'called')\nexit 0\n",
            encoding="utf-8-sig",
        )
    else:
        runtime_gate.write_text(
            "#!/usr/bin/env bash\nprintf called > \"$HOOK_MARKER\"\nexit 0\n",
            encoding="utf-8",
            newline="\n",
        )
    (scripts / "resolve-activation-role.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "path = Path(sys.argv[sys.argv.index('--config') + 1])\n"
        "print(path.read_text(encoding='utf-8').strip())\n",
        encoding="utf-8",
    )
    repo = tmp_path / f"repo-{shell}"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(repo)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if role is not None:
        config = repo / ".agent-index" / "config.yaml"
        config.parent.mkdir()
        config.write_text(role + "\n", encoding="utf-8")
    profile = tmp_path / f"profile-{shell}"
    profile.mkdir()
    environment = {
        **os.environ,
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "AGENT_INDEX_MACHINE": "test-machine",
        "HOOK_MARKER": str(marker),
        "PYTHONUTF8": "1",
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("AGENT_INDEX_ROLE", None)
    return ensure, repo, environment


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("role", [None, "client"])
def test_repo_admission_precedes_namespaced_service_ensure(
    tmp_path: Path,
    shell: str,
    role: str | None,
) -> None:
    script, repo, environment = _fixture(tmp_path, shell, role)

    result = subprocess.run(
        _shell_command(shell, script),
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert not Path(environment["HOOK_MARKER"]).exists()


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_designated_host_session_may_kick_namespaced_service_ensure(
    tmp_path: Path,
    shell: str,
) -> None:
    script, repo, environment = _fixture(tmp_path, shell, "host")

    result = subprocess.run(
        _shell_command(shell, script),
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert Path(environment["HOOK_MARKER"]).read_text(encoding="utf-8") == "called"
