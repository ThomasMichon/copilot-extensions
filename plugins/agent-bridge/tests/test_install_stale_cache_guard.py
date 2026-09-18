"""Regression guard: agent-bridge's local `[tool.uv.sources]` path deps
(ssh-manager, credential-relay, zdd, single-instance-lease, config-migrate,
agent-procutil, and agent-bridge itself) must always force a fresh `uv`
build/install, never a possibly-stale cached wheel.

`uv pip install` on a local PATH source caches its build keyed by source
path, not source content -- a stale wheel from a prior build can silently be
served instead of a fresh one. This is the confirmed root cause behind
ThomasMichon/copilot-extensions#2863 (agent-dispatch's identical exposure),
and `agent-procutil` specifically was directly implicated in a live
LAUNCH_ACP "session host exited early" incident on the same host during the
same outage window: `session_host/launcher.py` imports `agent_procutil` at
module level, so a stale build there crashes every headless-spawn session
host launch, invisibly to any health check that never imports it.

`agent-procutil` has no dedicated install call of its own in either
installer (it is only ever resolved transitively while installing
agent-bridge), so its cache-bust must ride on the final agent-bridge
package install call specifically.

These are static-text guards (following the precedent set in
agent-dispatch's `test_install_health_gate.py`), checked at both call sites
in each installer (the initial-install path and the update path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = (_PLUGIN_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
_INSTALL_PS1 = (_PLUGIN_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

_LOCAL_PATH_PACKAGES = (
    "agent-bridge",
    "agent-procutil",
    "agent-ssh-manager",
    "agent-credential-relay",
    "agent-zdd",
    "agent-single-instance-lease",
    "agent-config-migrate",
)


@pytest.mark.guard
def test_sh_local_path_packages_force_fresh_build():
    for pkg in _LOCAL_PATH_PACKAGES:
        assert _INSTALL_SH.count(f"--reinstall-package {pkg} --refresh-package {pkg}") >= 2, (
            f"{pkg} must force a fresh build at both the initial-install and "
            "update call sites in install.sh"
        )


@pytest.mark.guard
def test_ps1_local_path_packages_force_fresh_build():
    for pkg in _LOCAL_PATH_PACKAGES:
        assert (
            _INSTALL_PS1.count(f"'--reinstall-package', '{pkg}', '--refresh-package', '{pkg}'")
            >= 2
        ), (
            f"{pkg} must force a fresh build at both the initial-install and "
            "update call sites in install.ps1"
        )
