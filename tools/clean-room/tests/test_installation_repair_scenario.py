"""The repair scenario must prepare test dependencies through payload policy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="native POSIX scenario command")
def test_repair_scenario_test_preparation_uses_payload_dependency_policy(tmp_path):
    bash = shutil.which("bash")
    assert bash
    scenario = (
        Path(__file__).resolve().parents[1]
        / "scenarios" / "agent-machines-installation-repair" / "scenario.sh"
    ).read_text(encoding="utf-8")
    command = scenario.split('capture "prepare-tests" -- bash -c \'', 1)[1].split("' _ ", 1)[0]
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\nfrom pathlib import Path\n"
        "assert sys.argv[1]=='venv', 'raw uv pip install bypassed payload policy'\n"
        "target=Path(sys.argv[2])/'bin'\ntarget.mkdir(parents=True)\n"
        "os.symlink(sys.executable,target/'python')\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    payload = tmp_path / "payload with spaces"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "installer_dependency_policy.py").write_text(
        "import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8",
    )
    env = tmp_path / "test env"
    result = subprocess.run(
        [bash, "-c", command, "_", str(env), str(payload)],
        env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "--uv", "uv", "--", "--python", str(env / "bin" / "python"),
        "-e", str(payload) + "[dev]", "pytest-timeout>=2.3,<3",
    ]
