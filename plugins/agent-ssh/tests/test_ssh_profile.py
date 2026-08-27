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
        if line.strip() == ssh_profile._include_line(config_d)
    ]
    assert include_lines == [ssh_profile._include_line(config_d)]
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


def _load_transport(name: str) -> dict:
    with (ROOT / "transports" / name / "module.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_wsl_transport_interop_proxycommand() -> None:
    """The wsl transport bridges the last hop through wsl.exe interop, filling
    the {distro}/{user}/{port} placeholders and emitting no TCP HostName."""
    wsl = _load_transport("wsl")
    assert wsl["module"] == "wsl"

    cfg = {
        "transport": "wsl",
        "machines": [
            {
                "name": "devbox-wsl",
                "user": "agent",
                "port": 2200,
                "distro": "Ubuntu",
                "identity_file": "~/.ssh/id_ed25519",
                "via": "direct",
                "options": {"StrictHostKeyChecking": "accept-new", "ServerAliveInterval": 30},
            }
        ],
    }

    fragment = ssh_profile.render_fragment(cfg, wsl)
    assert "Host devbox-wsl" in fragment
    assert (
        "ProxyCommand wsl.exe -d Ubuntu -u agent exec nc 127.0.0.1 2200" in fragment
    )
    assert "User agent" in fragment
    assert "IdentityFile ~/.ssh/id_ed25519" in fragment
    # No TCP endpoint: the interop pipe carries everything, so no HostName / ProxyJump.
    assert "HostName" not in fragment
    assert "ProxyJump" not in fragment
    assert written_name(wsl) == "50-agent-ssh-wsl.conf"


def written_name(module: dict) -> str:
    return ssh_profile.fragment_name(module["module"])


def _load_wsl_emit_registry():
    import importlib.util

    path = ROOT / "transports" / "wsl" / "deploy" / "emit-registry.py"
    spec = importlib.util.spec_from_file_location("wsl_emit_registry", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_wsl_emit_registry_from_machines_yaml() -> None:
    scratch = _reset_scratch()
    machines_yaml = scratch / "machines.yaml"
    machines_yaml.write_text(
        "machines:\n"
        "  devbox:\n"
        "    ssh:\n"
        "      environments:\n"
        "        - name: windows\n"
        "          alias: devbox\n"
        "        - name: wsl\n"
        "          alias: devbox-wsl\n"
        "          user: agent\n",
        encoding="utf-8",
    )
    mod = _load_wsl_emit_registry()
    out = scratch / "wsl-registry.yaml"
    rc = mod.main(
        [
            "--machines", str(machines_yaml),
            "--machine", "devbox",
            "--distro", "Ubuntu",
            "--identity-file", "~/.ssh/id_test",
            "--out", str(out),
        ]
    )
    assert rc == 0
    reg = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert reg["transport"] == "wsl"
    m = reg["machines"][0]
    assert m["name"] == "devbox-wsl"
    assert m["user"] == "agent"
    assert m["port"] == 2200
    assert m["distro"] == "Ubuntu"
    assert m["identity_file"] == "~/.ssh/id_test"

    # Round-trip: the emitted registry renders the interop ProxyCommand.
    wsl = _load_transport("wsl")
    fragment = ssh_profile.render_fragment(reg, wsl)
    assert "ProxyCommand wsl.exe -d Ubuntu -u agent exec nc 127.0.0.1 2200" in fragment


def _keypair(root: Path, name: str) -> None:
    (root / name).write_text("private", encoding="utf-8")
    (root / f"{name}.pub").write_text("public", encoding="utf-8")


def test_wsl_identity_selection_prefers_machine_scoped_then_canonical() -> None:
    scratch = _reset_scratch()
    mod = _load_wsl_emit_registry()
    _keypair(scratch, "id_ed25519_devbox")
    assert mod._select_identity_file("devbox", scratch) == "~/.ssh/id_ed25519_devbox"
    _keypair(scratch, "id_ed25519")
    assert mod._select_identity_file("devbox", scratch) == "~/.ssh/id_ed25519_devbox"
    (scratch / "id_ed25519_devbox").unlink()
    (scratch / "id_ed25519_devbox.pub").unlink()
    assert mod._select_identity_file("devbox", scratch) == "~/.ssh/id_ed25519"


def test_wsl_identity_selection_falls_back_and_requires_public_sibling() -> None:
    scratch = _reset_scratch()
    mod = _load_wsl_emit_registry()
    (scratch / "id_ed25519_devbox").write_text("private-only", encoding="utf-8")
    _keypair(scratch, "id_ed25519_other")
    assert mod._select_identity_file("devbox", scratch) == "~/.ssh/id_ed25519_other"
    (scratch / "id_ed25519_other.pub").unlink()
    assert mod._select_identity_file("devbox", scratch) is None
