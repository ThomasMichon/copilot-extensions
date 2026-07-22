from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_ssh import ssh_profile  # noqa: E402

SCRATCH = Path(__file__).resolve().parent / ".scratch"


def _reset_scratch() -> Path:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    return SCRATCH


def _load_example(name: str) -> dict:
    with (ROOT / "contract" / "examples" / f"{name}.module.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_cloudflare_recipe_direct_recipe_and_coexistence() -> None:
    scratch = _reset_scratch()
    config_d = scratch / "config.d"
    ssh_config = scratch / "config"
    peer = config_d / "50-agent-ssh-peer.conf"
    config_d.mkdir()
    peer.write_text("Host peer\n    HostName peer.example.com\n", encoding="utf-8")

    cfg = {
        "transport": "cloudflare",
        "machines": [
            {
                "name": "alpha",
                "hostname": "alpha.example.com",
                "user": "agent",
                "port": 22,
                "identity_file": "~/.ssh/id_agent",
            }
        ],
    }

    cloudflare = _load_example("cloudflare")
    fragment = ssh_profile.render_fragment(cfg, cloudflare)
    assert "Host alpha" in fragment
    assert "ProxyCommand cloudflared access ssh --hostname alpha.example.com" in fragment

    direct = _load_example("direct")
    direct_fragment = ssh_profile.render_fragment(cfg, direct)
    assert "Host alpha" in direct_fragment
    assert "ProxyCommand" not in direct_fragment

    written = ssh_profile.write_fragment(cfg, cloudflare, config_d=config_d, ssh_config=ssh_config)
    assert written == config_d / "50-agent-ssh-cloudflare.conf"
    ssh_profile.write_fragment(cfg, cloudflare, config_d=config_d, ssh_config=ssh_config)

    include_lines = [
        line for line in ssh_config.read_text(encoding="utf-8").splitlines()
        if line.strip() == ssh_profile.ROOT_INCLUDE
    ]
    assert include_lines == [ssh_profile.ROOT_INCLUDE]
    assert peer.read_text(encoding="utf-8") == "Host peer\n    HostName peer.example.com\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL hardening")
def test_chmod_windows_removes_owner_rights() -> None:
    """On Windows, _chmod must set a user-only ACL with NO OWNER RIGHTS ACE.

    An inherited OWNER RIGHTS (S-1-3-4) ACE makes Windows OpenSSH reject the
    file ("Bad owner or permissions") and refuse the Include, so every
    ssh <machine> using an agent-ssh fragment fails. Regression guard for that.
    """
    scratch = _reset_scratch()
    frag = scratch / "50-agent-ssh-dtssh.conf"
    frag.write_text("Host x\n", encoding="utf-8")

    ssh_profile._chmod(frag, 0o600)

    acl = subprocess.run(
        ["icacls", str(frag)], capture_output=True, text=True
    ).stdout
    assert "S-1-3-4" not in acl  # OWNER RIGHTS SID
    assert "OWNER RIGHTS" not in acl
    user = os.environ.get("USERNAME", "")
    assert user and user in acl  # only the current user is granted
