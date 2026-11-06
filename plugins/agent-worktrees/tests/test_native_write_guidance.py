"""Native batched hook input reaches the existing guard policy."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch_keeps_valid_write_after_reads_under_one_deadline(tmp_path, monkeypatch):
    policy = load("hook_pre_policy")
    forms = load("tool_call_forms")
    guard = load("cross_repo_guard")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    target = tmp_path / "target"
    target.mkdir()
    roots = [{"name": "target", "path": str(target), "delegate": "agent-bridge"}]
    observed = []

    def guarded(event, *, home, deadline):
        observed.append((event["toolName"], deadline))
        return guard.decide(event, env={}, home=home, guarded_roots=roots)

    def sibling(name):
        if name == "tool_call_forms.py":
            return forms
        if name == "cross_repo_guard.py":
            return SimpleNamespace(decide=guarded)
        return SimpleNamespace(decide=lambda *args, **kwargs: None)

    def merge(left, right):
        return {**left, **(right or {})}

    result = policy.decide({
        "sessionId": "native-session", "cwd": str(tmp_path),
        "toolCalls": [
            None,
            {"id": "read", "name": "view", "args": {"path": str(target / "source.py")}},
            {"id": "write", "name": "apply_patch", "args": (
                f"*** Begin Patch\n*** Update File: {target}/source.py\n*** End Patch\n"
            )},
        ],
    }, home=tmp_path, load_sibling=sibling, merge_decisions=merge, budget_seconds=25)
    assert result["permissionDecision"] == "deny"
    assert [name for name, _ in observed] == ["view", "apply_patch"]
    assert observed[0][1] == observed[1][1]


def test_structured_multi_edit_collects_paths_not_replacement_text(tmp_path):
    forms = load("tool_call_forms")
    assert forms.write_targets("multi_edit", {
        "edits": [
            {"file_path": "one.py", "new_string": "mention other/repo"},
            {"path": "two.py"},
        ],
        "content": "*** Update File: not-a-target.py",
    }) == ["one.py", "two.py"]


def test_unknown_batch_shape_reports_unavailable_without_a_deny(tmp_path, monkeypatch):
    policy = load("hook_pre_policy")
    forms = load("tool_call_forms")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = policy.decide(
        {"toolCalls": "not-an-array"}, home=tmp_path,
        load_sibling=lambda name: forms,
        merge_decisions=lambda left, right: {**left, **right}, budget_seconds=25,
    )
    assert "permissionDecision" not in result
    assert "native tool batch is invalid" in result["additionalContext"]


@pytest.mark.parametrize("form", ["batch", "apply_patch", "multi_edit"])
def test_native_write_does_not_accept_empty_legacy_monitor_decision(tmp_path, monkeypatch, form):
    client, forms, guard = (load(name) for name in ("hook_client", "tool_call_forms", "cross_repo_guard"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    target = tmp_path / "target"
    target.mkdir()
    roots = [{"name": "target", "path": str(target), "delegate": "agent-bridge"}]
    calls = []

    def monitor(*args):
        calls.append(args)
        return {}

    def sibling(name):
        if name == "tool_call_forms.py":
            return forms
        if name == "cross_repo_guard.py":
            return SimpleNamespace(decide=lambda event, *, home, deadline: guard.decide(
                event, env={}, home=home, guarded_roots=roots,
            ))
        return SimpleNamespace(decide=lambda *a, **k: None, needs_observations=lambda *a: False)

    monkeypatch.setattr(client, "_request", monitor)
    monkeypatch.setattr(client, "_load_sibling", sibling)
    patch = f"*** Begin Patch\n*** Update File: {target / 'source.py'}\n*** End Patch\n"
    event = {"cwd": str(tmp_path)}
    if form == "batch":
        event["toolCalls"] = [{"name": "apply_patch", "args": patch}]
    else:
        event.update(toolName=form, toolArgs=(
            patch if form == "apply_patch" else {"edits": [{"path": str(target / "source.py")}]}
        ))
    assert client.decide("preToolUse", event, home=tmp_path)["permissionDecision"] == "deny"
    assert calls == []
    assert client.decide("preToolUse", {
        "cwd": str(tmp_path), "toolName": "edit", "toolArgs": {"path": "source.py"},
    }, home=tmp_path) == {}
    assert len(calls) == 1


@pytest.mark.parametrize("posture", ["worktree", "reference", "delegated"])
def test_composed_maintenance_pull_retains_each_repository_policy(tmp_path, monkeypatch, posture):
    client, cross, anchor = (load(name) for name in (
        "hook_client", "cross_repo_guard", "anchor_write_guard",
    ))
    target = tmp_path / "target"
    (target / ".git").mkdir(parents=True)
    row = {
        "name": "target", "path": str(target),
        "delegate": "agent-bridge" if posture == "delegated" else "none",
    }
    if posture != "delegated":
        row["posture"] = posture
    original = client._load_sibling

    def sibling(name):
        if name == "cross_repo_guard.py":
            return SimpleNamespace(decide=lambda event, *, home, deadline: cross.decide(
                event, env={}, home=home, guarded_roots=[row],
            ))
        if name == "anchor_write_guard.py":
            return SimpleNamespace(decide=lambda event, *, home: anchor.decide(
                event, env={}, home=home, anchors=[row],
            ))
        if name == "tool_call_forms.py":
            return original(name)
        return SimpleNamespace(decide=lambda *a, **k: None, needs_observations=lambda *a: False)

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(client, "_load_sibling", sibling)
    result = client.decide("preToolUse", {"cwd": str(tmp_path), "toolCalls": [{
        "name": "powershell", "args": {
            "command": f'git -C "{target}" pull --ff-only origin main',
        },
    }]}, home=tmp_path)
    assert (result.get("permissionDecision") == "deny") is (posture != "worktree")


def test_composed_guards_consume_selected_grant_and_revocation(tmp_path, monkeypatch):
    from agent_worktrees import allow_edits

    policy = load("hook_pre_policy")
    client = load("hook_client")
    root = tmp_path / "harness"
    (root / ".git").mkdir(parents=True)
    (root / ".agent-worktrees").mkdir()
    (root / ".agent-worktrees" / "config.yaml").write_text("stateless: true\n")
    selected_home = tmp_path / "selected"
    registry = selected_home / ".agent-worktrees"
    registry.mkdir(parents=True)
    (registry / "repos.yaml").write_text(
        "repos:\n  harness:\n    class: worktree\n"
        + f"    {'windows' if os.name == 'nt' else 'linux'}: {json.dumps(str(root))}\n"
    )
    monkeypatch.setenv("AGENT_HOME", str(selected_home))
    monkeypatch.delenv("CROSS_REPO_GUARD", raising=False)
    monkeypatch.setenv("CROSS_REPO_GUARD_MODE", "off")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    for key in ("ANCHOR_WRITE_GUARD", "ANCHOR_WRITE_GUARD_MODE", "AGENT_WORKTREES_STATELESS_GUARD"):
        monkeypatch.delenv(key, raising=False)
    event = {"cwd": str(root), "toolName": "apply_patch", "toolArgs": (
        f"*** Begin Patch\n*** Add File: {root / 'efforts' / 'note.md'}\n*** End Patch\n"
    )}

    def decide():
        return policy.decide(
            event, home=tmp_path, load_sibling=client._load_sibling,
            merge_decisions=client._merge_pre_decisions, budget_seconds=25,
        )

    assert decide()["permissionDecision"] == "deny"
    allow_edits.grant("harness", "isolated composed consumer validation", 10)
    assert decide().get("permissionDecision") is None
    assert not (tmp_path / ".agent-worktrees" / "allow-edits.json").exists()
    assert allow_edits.revoke("harness")
    assert decide()["permissionDecision"] == "deny"


def test_batch_pr_observation_uses_one_owned_worker_after_cheap_ask(tmp_path, monkeypatch):
    policy, forms, client = (load(name) for name in (
        "hook_pre_policy", "tool_call_forms", "hook_client",
    ))
    observed = []
    event = {"cwd": str(tmp_path), "toolCalls": [
        {"name": "edit", "args": {"path": "target.py"}},
        {"name": "powershell", "args": {"command": "gh pr close 41 --repo example/project"}},
    ]}

    def worker(frame, **kwargs):
        assert json.loads(frame) == event
        assert kwargs["legacy"] is True
        assert kwargs["initial_decision"]["permissionDecision"] == "ask"
        assert kwargs["settlement_deadline"] > 0
        observed.append("owned-worker")
        return 0

    def sibling(name):
        if name == "tool_call_forms.py":
            return forms
        if name == "hook_policy_process.py":
            return SimpleNamespace(execute=worker)
        if name == "pr_supersede_guard.py":
            return SimpleNamespace(needs_observations=lambda e: e.get("toolName") == "powershell")
        return SimpleNamespace(decide=lambda *a, **k: {
            "permissionDecision": "ask", "permissionDecisionReason": "confirmed guard",
        })

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    assert policy.execute_owned_observations(
        event, json.dumps(event), sibling, client._merge_pre_decisions,
    ) == 0
    assert observed == ["owned-worker"]
