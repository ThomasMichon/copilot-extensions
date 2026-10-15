"""Source footprint and lifecycle-owned deploy-manifest publication."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import config as cfg, git_ops, installer as inst, output, registry_paths


def write_deploy_manifest(repo_dir: str | Path, machine: str) -> None:
    manifest_path = inst.install_dir() / "deploy-manifest.json"
    plugin_dir = inst.find_package_source(repo_dir).parent.parent
    plugin_path = str(plugin_dir)
    kind = inst._source_kind(plugin_path)
    version = "0.0.0"
    pyproject = plugin_dir / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                version = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    commit = None
    branch = None
    dirty = False
    if kind == "local":
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, **no_window_kwargs(),
            env=git_ops.repository_identity_env(),
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, **no_window_kwargs(),
            env=git_ops.repository_identity_env(),
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain", "--",
             "plugins/agent-worktrees/"],
            capture_output=True, text=True, **no_window_kwargs(),
            env=git_ops.repository_identity_env(),
        )
        if result.returncode == 0:
            dirty = bool(result.stdout.strip())
    manifest = {
        "schema_version": 3,
        "service": "agent-worktrees",
        "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed_by": f"{machine}-{cfg.detect_platform()}",
        "source": {
            "kind": kind, "path": plugin_path.replace("\\", "/"),
            "repo": "copilot-extensions", "plugin": "agent-worktrees",
            "version": version, "commit": commit, "branch": branch, "dirty": dirty,
        },
        "venv": str(inst.venv_dir()).replace("\\", "/"),
        "runtime": "python",
    }
    if registry_paths.installation_context() is None:
        from . import _command_binding, _installation_context

        publication = _command_binding.write_legacy_manifest(
            _installation_context, manifest,
            payload_root=inst._payload_root(repo_dir), plugin_id="agent-worktrees",
            legacy_root=inst.install_dir(), project_root=Path(repo_dir),
        )
        if publication["status"] == "unresolved":
            output.warn(f"Command ownership was not published: {publication['reason']}")
    else:
        temporary = manifest_path.with_name(manifest_path.name + ".tmp")
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(manifest_path)
    output.ok(f"Deploy manifest: {manifest_path} (source: {kind})")
