"""Select bounded installation-context CI coverage from changed paths."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable


def needs_installation_context_smoke(paths: Iterable[str]) -> bool:
    for path in paths:
        if path in {
            ".github/workflows/ci.yml",
            "tools/sync-installation-context.py",
            "tools/installation_context_ci_scope.py",
            "tools/test_installation_context_ci_scope.py",
            "worktree-manager/src/worktree_manager/agent_plugin_runtime.py",
            "worktree-manager/tests/test_agent_plugin_runtime.py",
            "worktree-manager/tests/_installation_context_fixtures.py",
            "plugins/agent-worktrees/src/agent_worktrees/reconcile.py",
            "plugins/agent-worktrees/tests/test_reconcile.py",
            "plugins/agent-worktrees/src/agent_worktrees/installer.py",
            "plugins/agent-worktrees/src/agent_worktrees/installation_cli.py",
            "plugins/agent-worktrees/src/agent_worktrees/deploy_manifest.py",
            "plugins/agent-worktrees/tests/test_installer_command_binding.py",
            "plugins/agent-worktrees/scripts/command_binding.py",
            "plugins/agent-worktrees/src/agent_worktrees/_command_binding.py",
            "plugins/agent-worktrees/scripts/command_lifecycle.py",
            "plugins/agent-worktrees/src/agent_worktrees/_command_lifecycle.py",
            "plugins/agent-worktrees/scripts/install.sh",
            "plugins/agent-worktrees/scripts/install.ps1",
            "plugins/agent-worktrees/scripts/emit-command-catalog.sh",
            "plugins/agent-worktrees/scripts/emit-command-catalog.ps1",
            "plugins/agent-worktrees/payload-invocation.json",
        } or path.startswith((
            "libs/installation-context/", "libs/peer-launch/", "libs/payload-invocation/",
        )):
            return True
        if path.startswith("plugins/") and "/scripts/installation-context/" in path:
            return True
        if path.startswith(("plugins/", "worktree-manager/src/")):
            name = path.rsplit("/", 1)[-1]
            if name.startswith("_installation_context") and name.endswith(".py"):
                return True
            if name in {"_peer_launch.py", "peer_launch.py"}:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--event", default="")
    args = parser.parse_args(argv)
    if args.event == "workflow_dispatch" or not args.base:
        print("true")
        return 0
    available = subprocess.run(
        ["git", "rev-parse", "--verify", f"{args.base}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    if available.returncode:
        print(
            "installation-context scope: base revision unavailable; selecting smoke",
            file=sys.stderr,
        )
        print(available.stderr, file=sys.stderr, end="")
        print("true")
        return 0
    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base}...{args.head}"],
        capture_output=True, text=True, check=False,
    )
    if changed.returncode:
        print(changed.stderr, file=sys.stderr, end="")
        return 1
    print("true" if needs_installation_context_smoke(changed.stdout.splitlines()) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
