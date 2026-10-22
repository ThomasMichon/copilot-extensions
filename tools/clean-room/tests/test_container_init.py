"""Canonical Linux scenario containers need an orphan reaper, not PID-1 sleep."""
from pathlib import Path

RIG = Path(__file__).resolve().parents[1]


def test_linux_container_starts_require_docker_init_in_both_wrappers():
    powershell = (RIG / "run.ps1").read_text(encoding="utf-8")
    shell = (RIG / "run.sh").read_text(encoding="utf-8")
    ps_start = powershell.index("function Start-Container {")
    ps_body = powershell[ps_start:powershell.index("\nfunction Ensure-Container", ps_start)]
    sh_start = shell.index("start_container() {")
    sh_body = shell[sh_start:]
    assert "docker run -d --init --name $Container `" in ps_body
    assert 'docker run -d --init --name "$CONTAINER" \\' in sh_body
    assert "--entrypoint sleep $img infinity" in ps_body
    assert '--entrypoint sleep "$img" infinity' in sh_body
    assert "--init" not in powershell[:ps_start]
