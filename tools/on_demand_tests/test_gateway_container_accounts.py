"""Explicit cached offline Gateway account/admission image lane, not unit CI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("GATEWAY_ACCOUNT_IMAGE_ACCEPTANCE") != "1",
    reason="requires explicit cached offline Gateway account contexts",
)


@pytest.mark.parametrize("case", ("default", "custom", "existing", "zero", "zero-gid", "linked-home"))
def test_gateway_selected_account_and_production_admission(case):
    docker = shutil.which("docker")
    assert docker is not None
    context = Path(os.environ["GATEWAY_ACCOUNT_IMAGE_CONTEXT"]) / case
    identity = json.loads((context / "identity.json").read_text())
    recipe = REPO / "fleet-manager/containers/gateway/Dockerfile"
    assert identity["recipe_sha256"] == hashlib.sha256(recipe.read_bytes()).hexdigest()
    image = f"copilot-gateway-account-{case}:20261024"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    arguments = [] if case == "default" else [
        "--build-arg", f"GATEWAY_UID={identity['uid']}",
        "--build-arg", f"GATEWAY_GID={identity['gid']}",
    ]
    built = subprocess.run(
        [docker, "build", "--network", "none", "--quiet", "--tag", image, *arguments, str(context)],
        capture_output=True, text=True, timeout=180, creationflags=flags,
    )
    if case in {"zero", "zero-gid", "linked-home"}:
        assert built.returncode != 0
        assert "did not complete successfully" in built.stderr
        return
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [docker, "run", "--rm", "--pull=never", "--network", "none", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m",
         "--entrypoint", "/opt/gateway/bin/python", image, "-I", "/proof/probe.py"],
        capture_output=True, text=True, timeout=45, creationflags=flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert (report["uid"], report["gid"]) == (identity["uid"], identity["gid"])
    assert report["passwd_home"] == "/state" and report["production_admission"]
    assert report["transport_started"] is False
