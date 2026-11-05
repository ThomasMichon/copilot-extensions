"""Native batched hook input reaches the existing guard policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

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
