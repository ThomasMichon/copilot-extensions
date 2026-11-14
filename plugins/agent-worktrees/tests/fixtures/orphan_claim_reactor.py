"""Finite competing claim writer; only fixture configuration and telemetry are substituted."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

root, fixture, project, identity, machine, hostname, markers, *operation = sys.argv[1:]
root, fixture, markers = Path(root), Path(fixture), Path(markers)
sys.path.insert(0, str(root / "plugins/agent-worktrees/src"))

from agent_worktrees import config as cfg, config_authority, execution_spaces, repos, tracking, tracking_claim_write

def fixture_config(name):
    if name not in {"harness", "knowledge"}:
        raise ValueError("unknown fixture receiving project")
    anchor = fixture / name
    return cfg.Config(
        srcroot=str(fixture), machine=machine, platform=cfg.detect_platform(), repo_name=name,
        repos={name: cfg.RepoConfig(
            anchor=str(anchor), worktree_root=cfg.derive_worktree_root(str(anchor)), default_branch="main",
        )},
    )


def load_config(config_file=None, **kwargs):
    name = kwargs.get("project") or (Path(config_file).parent.name.lstrip(".") if config_file else project)
    return fixture_config(name)


config = fixture_config(project)
cfg._home = lambda: fixture
cfg.socket.gethostname = lambda: hostname
cfg.project_name = lambda: project
cfg.active_project = lambda: project
cfg.project_dir = lambda name=None: fixture / f".{name or project}"
cfg.tracking_dir = lambda name=None: cfg.project_dir(name) / "worktrees"
cfg.load_config = load_config
cfg.load_project_config = lambda name, **kwargs: fixture_config(name)
config_authority.load_project_authority_config = lambda name, **kwargs: fixture_config(name)
repos.find_repo = lambda name: repos.RepoEntry(
    name=name, repo_class="worktree", paths={config.platform: str(fixture / name)},
)
tracking_claim_write.activity.log_event = lambda *args, **kwargs: None
tracking_claim_write.claim_history.record_event = lambda *args, **kwargs: None

source = cfg.tracking_dir(project) / f"{identity}.yaml"
native_lock = tracking._RecordLock


class ObservedRecordLock(native_lock):
    def __enter__(self):
        if self._yaml_path == source:
            (markers / "attempted").write_text("ready", encoding="utf-8")
        result = super().__enter__()
        if self._yaml_path == source:
            (markers / "acquired").write_text("entered", encoding="utf-8")
        return result


tracking._RecordLock = ObservedRecordLock
if operation == ["settle-parent"]:
    warnings = []
    execution_spaces.settle_parent_obligation(
        tracking.load_record(source), config, identity,
        output=SimpleNamespace(ok=lambda *_args: None, warn=warnings.append),
    )
    print(json.dumps({"ok": not warnings, "warnings": warnings}), flush=True)
    raise SystemExit(0 if not warnings else 1)
result = tracking_claim_write.apply_claim_add({
    "worktree_id": identity, "yaml_path": str(source),
    "kind": "codespace", "ref": "owned-resource",
})
print(json.dumps(result), flush=True)
raise SystemExit(0 if result.get("ok") else 1)
