"""Private real canonical files and resident servers; no SDK or installed actor."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from agent_worktrees import config as cfg
from agent_worktrees import execution_spaces, tracking, tracking_claim_write
from agent_worktrees import tracking_save_notifications as notifications
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from agent_worktrees.worktrees_runtime import WorktreesRuntime


def selection(head="head"):
    return {
        "contract_version": 1,
        "project": "native-test",
        "worktree_id": "native-one",
        "expected_head_session_id": head,
        "after_cursor": None,
    }


def setup(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    project = home / ".native-test"
    records = project / "worktrees"
    records.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "machines.yaml").write_text("machines: {}\n")
    (project / "config.yaml").write_text(
        json.dumps(
            {
                "repo_name": "native-test",
                "machine": socket.gethostname(),
                "platform": cfg.detect_platform(),
                "repos": {"native-test": {"anchor": str(checkout)}},
            }
        )
    )
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("AGENT_WORKTREES_NO_SELFPROVISION", "1")
    monkeypatch.setattr(cfg, "install_dir", lambda: home / ".agent-worktrees")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: home / f".{name or 'native-test'}")
    cfg.set_active_project("native-test")
    config = cfg.load_config()
    context = execution_spaces.current_execution_context(config)
    identity = WorktreeMachineIdentity(
        context.machine, context.environment, context.execution_machine
    )
    record = tracking.WorktreeRecord(
        worktree_id="native-one",
        branch="worktree/native-one",
        worktree_path=str(checkout),
        repo="native-test",
        machine=identity.machine,
        platform=identity.environment,
        machine_identity=identity,
        started_at="2026-11-02T00:00:00Z",
        last_resumed_at="2026-11-02T00:00:00Z",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        checkout_managed=False,
        creation_nonce="native-incarnation",
        sessions=[tracking.SessionEntry("head", "2026-11-02T00:00:00Z")],
    )
    path = records / "native-one.yaml"
    tracking.save_record(record, path)
    owner = WorktreesRuntime(home / ".agent-worktrees" / "worktrees-current.sqlite3")
    monkeypatch.setattr(owner.agendas, "roster_changed", lambda project: None)
    owner.activate()
    owner.observation_read(selection())
    publish(owner, path)
    return owner, path, home


def current_row(path: Path):
    record = tracking.load_record(path)
    row = {
        "id": record.worktree_id,
        "repo": record.repo,
        "creation_nonce": record.creation_nonce,
        **record.machine_fields,
        "last_session_id": record.resolved_head_session,
        "title": record.title,
        "status": record.status,
        "summary": record.summary,
        "follow_up": record.follow_up,
        "paused": record.paused,
        "checkout_managed": False,
        "active_effort": None if record.active_effort is None else record.active_effort.to_dict(),
        "claims_assets": {"held_count": len(record.live_resources)},
        "fact_provenance": {"record": {"quality": "current", "observed_at": 1}},
    }
    return row


def publish(owner, path: Path):
    row = current_row(path)
    owner.publish(row["repo"], [row], observed_at=1)
    return owner.observation_read(selection(row["last_session_id"]))


def page(owner):
    current = owner.observation_read(selection())
    value = selection()
    value.pop("after_cursor")
    value.update(source_generation=current["source_generation"], before_cursor=None, limit=64)
    return owner.observation_history(value)


def environment(home: Path) -> dict[str, str]:
    return {
        **os.environ,
        "AGENT_HOME": str(home),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def observe_direct_claim(other_path: Path):
    def direct_b(event):
        if not hasattr(event, "after") or event.after.identity.worktree_id != "native-one":
            return
        result = tracking_claim_write.apply_claim_add(
            {
                "yaml_path": str(other_path),
                "worktree_id": "native-two",
                "kind": "codespace",
                "ref": "direct-B-child",
            }
        )
        assert result["ok"], result

    return notifications.observe_receipts(direct_b)
