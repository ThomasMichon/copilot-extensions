"""Explicit prepared-image account/admission qualification; never a unit CI target."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("INDEX_ACCOUNT_IMAGE_ACCEPTANCE") != "1",
    reason="requires explicit prepared offline Index account image contexts",
)


@pytest.mark.parametrize("case", ("fresh", "custom", "existing", "conflict", "linked-home"))
def test_real_generated_account_stage_and_startup_admission(case):
    docker = shutil.which("docker")
    assert docker is not None, "prepared Docker lane is required"
    context = Path(os.environ["INDEX_ACCOUNT_IMAGE_CONTEXT"]) / case
    identity = json.loads((context / "runtime-stage.json").read_text())
    source = REPO / "services/index/src/agent_index_service/container.py"
    assert identity["generator_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    image = f"copilot-index-account-{case}:20261024"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    built = subprocess.run(
        [docker, "build", "--network", "none", "--quiet", "--tag", image, str(context / "build")],
        capture_output=True, text=True, timeout=180, creationflags=flags,
    )
    if case in {"conflict", "linked-home"}:
        assert built.returncode != 0
        assert "did not complete successfully" in built.stderr
        return
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [docker, "run", "--rm", "--pull=never", "--network", "none", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m",
         "--entrypoint", "/opt/agent-index/bin/python", image, "-I", "/proof/probe.py"],
        capture_output=True, text=True, timeout=45, creationflags=flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert (report["uid"], report["gid"]) == (identity["uid"], identity["gid"])
    assert report["passwd_home"] == "/var/lib/agent-index"
    assert report["production_admission"] and report["real_main_and_config"]
    assert report["synthetic_downstream"]
