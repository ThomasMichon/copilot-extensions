"""Synthetic core providers around the real optional repository CLI caller."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])

from worktree_manager import engine_client, setup_cli as cli  # noqa: E402
from worktree_manager.core_install import CoreInstallResult, CoreStatus  # noqa: E402

events: list[str] = []
cli.detect_baseline = lambda: []
cli.provision_plan = lambda gaps: []


def prerequisites(*args, **kwargs):
    events.append("prerequisites")
    return []


def core(**kwargs):
    events.append("core")
    Path(sys.argv[4]).write_text(json.dumps(events))
    return CoreInstallResult(None, False, reason="already-installed")


cli.provision_apply = prerequisites
cli.core_status = lambda: CoreStatus("installed", True, True, "synthetic-core", "synthetic-runtime")
cli.install_command = lambda: None
cli.install_core = core
engine_client.set_engine_command([sys.executable, "-m", "agent_worktrees"])
raise SystemExit(cli.cmd_setup([
    "--apply", "--repository", sys.argv[2], "--repository-target", sys.argv[3],
    "--trust-repository-setup",
], banner="Synthetic consumer"))
