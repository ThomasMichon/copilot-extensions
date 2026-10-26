"""Opt-in actual SSH image account/admission proof; excluded from unit-only CI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("SSH_ACCOUNT_IMAGE_ACCEPTANCE") != "1",
    reason="requires explicit actual SSH image and private probe inputs",
)


def test_actual_ssh_image_default_registry_admission():
    docker = shutil.which("docker")
    assert docker is not None
    context = Path(os.environ["SSH_ACCOUNT_IMAGE_CONTEXT"])
    sources = json.loads((context / "sources.json").read_text())
    main = REPO / "services/ssh/src/agent_ssh_service/__main__.py"
    assert hashlib.sha256(main.read_bytes()).hexdigest() == sources["main_sha256"]
    result = subprocess.run(
        [docker, "run", "--rm", "--pull=never", "--network", "none", "--read-only",
         "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
         "--pids-limit", "64", "--memory", "512m",
         "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m",
         "--tmpfs", "/data:rw,nosuid,nodev,noexec,mode=700,uid=65532,gid=65532,size=16m",
         "--mount", f"type=bind,source={context / 'probe.py'},target=/proof/probe.py,readonly",
         "--entrypoint", "python", os.environ["SSH_ACCOUNT_IMAGE"],
         "-I", "/proof/probe.py", sources["main_sha256"]],
        capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof["uid"] == proof["gid"] == 65532 and proof["passwd_home"] == "/data"
    assert proof["production_admission"] and proof["real_main_and_config"]
    assert proof["synthetic_downstream"]
