import json
import os
from pathlib import Path
import subprocess

import pytest

from test_installers import BASH, PWSH, _copy_payload, _fake_uv_environment


@pytest.mark.parametrize("platform", ["posix", "powershell"])
def test_dependency_artifacts_are_cleaned_before_and_after_each_build_attempt(tmp_path, platform):
    if platform == "posix" and (BASH is None or os.name == "nt"):
        pytest.skip("POSIX installer coverage")
    if platform == "powershell" and (PWSH is None or os.name == "nt"):
        pytest.skip("portable PowerShell fixture uses a POSIX fake uv executable")
    _, env = _fake_uv_environment(tmp_path)
    payload = tmp_path / "budget-guidance"
    _copy_payload(payload)
    procutil = payload / "libs" / "agent-procutil"
    for source in (payload, procutil):
        (source / "build" / "lib").mkdir(parents=True, exist_ok=True)
        (source / "seed.egg-info").mkdir()
        (source / "src" / "seed.egg-info").mkdir()
    env["TEST_BUILD_ARTIFACT_PROBE"] = "1"
    attempts = tmp_path / "attempts.jsonl"
    env["TEST_INSTALL_ATTEMPTS_LOG"] = str(attempts)
    install_dir = Path(env["HOME"]) / ".budget-guidance"
    command = (
        [BASH, str(payload / "scripts" / "install.sh"), "install", "--install-dir", str(install_dir)]
        if platform == "posix" else
        [PWSH, "-NoProfile", "-File", str(payload / "scripts" / "install.ps1"),
         "-Action", "install", "-InstallDir", str(install_dir)]
    )
    result = subprocess.run(
        command, cwd=tmp_path, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout
    assert [json.loads(line) for line in attempts.read_text().splitlines()] == [
        {"engine": "uv", "sources": 2}, {"engine": "pip", "sources": 2},
    ]
    for source in (payload, procutil):
        assert not (source / "build").exists()
        assert not list(source.glob("*.egg-info"))
        assert not list((source / "src").glob("*.egg-info"))
