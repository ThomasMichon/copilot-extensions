from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/agent-worktrees/scripts"
_spec = importlib.util.spec_from_file_location(
    "promotion_native_tool_policy",
    ROOT / "plugins/agent-worktrees/src/agent_worktrees/native_tool_policy.py",
)
assert _spec and _spec.loader
_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_api)
POLICY_SWITCHES = _api.POLICY_SWITCHES
decide_pre = _api.decide_pre


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def merge(current: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], load("hook_client.py")._merge_pre_decisions(current, new))


def event(**values: Any) -> dict[str, Any]:
    return {
        "toolName": "edit", "cwd": "/example",
        "_agentWorktrees": {"nativePolicy": 1, "environment": dict.fromkeys(POLICY_SWITCHES)},
        **values,
    }


@pytest.mark.parametrize("flag", [True, 2, "1"])
def test_unsupported_native_dispatch_never_uses_legacy_policy(flag: Any) -> None:
    policy = SimpleNamespace(
        hook_client=None,
        pre=lambda *_args: pytest.fail("native dispatch entered legacy policy"),
    )
    result = _api.dispatch_pre(policy, event(_agentWorktrees={"nativePolicy": flag}), time.time() + 0.5)
    assert "permissionDecision" not in result
    assert "not authorization" in result["additionalContext"]


def test_legacy_dispatch_keeps_the_existing_operation() -> None:
    payload = {"toolName": "edit"}
    calls = []

    def pre(value: dict[str, Any]) -> dict[str, str]:
        calls.append(value)
        return {"permissionDecision": "deny", "permissionDecisionReason": "legacy guard"}

    assert _api.dispatch_pre(SimpleNamespace(pre=pre), payload, None)["permissionDecision"] == "deny"
    assert calls == [payload]


def test_native_policy_preserves_batch_decisions_and_caller_switches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CROSS_REPO_GUARD", "off")
    seen: list[tuple[str, str, dict[str, str]]] = []

    def sibling(name: str) -> ModuleType:
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        module = ModuleType(name)

        def decide(payload: dict[str, Any], **kwargs: Any) -> dict[str, str] | None:
            seen.append((name, payload["toolName"], kwargs["env"]))
            if name == "anchor_write_guard.py" and payload["toolName"] == "edit":
                return {"permissionDecision": "deny", "permissionDecisionReason": "protected"}
            return None

        module.decide = decide  # type: ignore[attr-defined]
        return module

    result = _api.dispatch_pre(
        SimpleNamespace(
            hook_client=SimpleNamespace(_merge_pre_decisions=merge),
            _module=sibling,
            pre=lambda *_args: pytest.fail("native dispatch entered legacy policy"),
        ),
        event(toolCalls=[{"name": "view"}, {"name": "edit", "args": {"path": "target"}}]),
        time.time() + 0.5,
    )
    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == "protected"
    assert [name for name, tool, _ in seen if tool == "view"] == [
        "statelessness_guard.py", "cross_repo_guard.py",
        "anchor_write_guard.py", "pr_supersede_guard.py",
    ]
    assert all("CROSS_REPO_GUARD" not in environment for _, _, environment in seen)


@pytest.mark.parametrize(("caller", "resident"), [
    (False, True), (True, False), (None, True), ("true", True),
], ids=["caller-standard", "caller-elevated", "caller-missing", "caller-invalid"])
def test_native_cross_repo_uses_caller_elevation_not_resident(
    monkeypatch: pytest.MonkeyPatch, caller: Any, resident: bool,
) -> None:
    guard = load("cross_repo_guard.py")
    resident_checks: list[bool] = []

    def resident_elevation() -> bool:
        resident_checks.append(resident)
        return resident

    monkeypatch.setattr(guard, "_is_elevated", resident_elevation)
    monkeypatch.setattr(guard, "resolve_registry_root", lambda **_kwargs: Path("/fixture"))
    monkeypatch.setattr(guard, "find_repo_root", lambda *_args: Path("/caller"))
    monkeypatch.setattr(guard, "load_guarded_roots", lambda *_args, **_kwargs: [{
        "name": "foreign", "path": "/foreign", "delegate": "none",
        "elevated": True, "reason": "protected elevation posture",
    }])
    monkeypatch.setattr(guard, "active_break_glass", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        guard, "evaluate", lambda _tool, _args, _cwd, roots, **_kwargs: roots[0] if roots else None,
    )

    def sibling(name: str) -> ModuleType:
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        if name == "cross_repo_guard.py":
            return guard
        module = ModuleType(name)
        module.decide = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
        return module

    payload = event()
    if caller is not None:
        payload["_agentWorktrees"]["callerElevated"] = caller
    result = decide_pre(
        payload, load_sibling=sibling, merge=merge, deadline=time.time() + 0.5,
    )
    if caller is True:
        assert result == {}
    else:
        assert result["permissionDecision"] == "deny"
        assert result["permissionDecisionReason"] == "protected elevation posture"
    assert not resident_checks, "native policy borrowed the resident's elevation"


@pytest.mark.parametrize("case", ["expired", "between-guards", "between-events"])
def test_native_batch_stops_starting_guards_at_its_deadline(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    clock = {"monotonic": 10.0}
    monkeypatch.setattr(_api, "time", SimpleNamespace(
        time=lambda: 100.0, monotonic=lambda: clock["monotonic"],
    ))
    loaded: list[str] = []
    calls: list[str] = []

    def sibling(name: str) -> ModuleType:
        loaded.append(name)
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        module = ModuleType(name)

        def decide(*_args: Any, **_kwargs: Any) -> dict[str, str] | None:
            calls.append(name)
            if (
                case == "between-guards" and name == "statelessness_guard.py"
                or case == "between-events" and name == "pr_supersede_guard.py"
            ):
                clock["monotonic"] += 1.0
            if name == "statelessness_guard.py":
                return {"permissionDecision": "ask", "permissionDecisionReason": "protected"}
            return None

        module.decide = decide  # type: ignore[attr-defined]
        return module

    result = decide_pre(
        event(toolCalls=[{"name": "edit"}, {"name": "edit"}]),
        load_sibling=sibling, merge=load("hook_client.py")._merge_pre_decisions,
        deadline=99.0 if case == "expired" else 100.5,
    )
    assert "deadline" in result["additionalContext"]
    if case == "expired":
        assert not loaded and not calls
        assert "permissionDecision" not in result
    else:
        assert len(calls) == (1 if case == "between-guards" else 4)
        assert len(loaded) == len(calls) + 2
        assert result["permissionDecision"] == "ask"
        assert result["permissionDecisionReason"] == "protected"


@pytest.mark.parametrize("case", ["close", "api", "off"])
def test_native_supersede_never_observes_with_resident_identity(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "resident-only")
    monkeypatch.setenv("GH_HOST", "resident.example")
    guard = load("pr_supersede_guard.py")
    observations: list[list[str]] = []

    def inherited_gh(args: list[str], *unused: Any, **kwargs: Any) -> str:
        observations.append(args)
        if args[0] == "pr":
            return json.dumps({"author": {"login": "resident-user"}, "state": "OPEN"})
        return "resident-user"

    monkeypatch.setattr(guard, "_run_gh", inherited_gh)

    def sibling(name: str) -> ModuleType:
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        if name == "pr_supersede_guard.py":
            return guard
        module = ModuleType(name)
        module.decide = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
        return module

    command = (
        "gh api repos/example/project/pulls/42 -X PATCH -f state=closed"
        if case == "api" else "gh pr close 42 --repo example/project"
    )
    payload = event(toolName="powershell", toolArgs={"command": command})
    if case == "off":
        payload["_agentWorktrees"]["environment"]["PR_SUPERSEDE_GUARD"] = "off"
    result = decide_pre(
        payload, load_sibling=sibling, merge=merge, deadline=time.time() + 0.5,
    )
    assert not observations, "native policy used the resident's external observation owner"
    assert "permissionDecision" not in result
    if case == "off":
        assert result == {}
    else:
        assert "caller-scoped PR observation" in result["additionalContext"]
        assert "not authorization" in result["additionalContext"]
    assert "resident-user" not in str(result) and "resident-only" not in str(result)


@pytest.mark.parametrize("failure", ["missing", "raises", "invalid"])
def test_unavailable_guard_does_not_suppress_independent_deny(failure: str) -> None:
    def sibling(name: str) -> ModuleType | None:
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        if name == "statelessness_guard.py" and failure == "missing":
            return None
        module = ModuleType(name)

        def decide(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any] | None:
            if name == "statelessness_guard.py":
                if failure == "raises":
                    raise OSError("private diagnostic")
                return {"permissionDecision": ["allow"]}
            if name == "cross_repo_guard.py":
                return {"permissionDecision": "deny", "permissionDecisionReason": "protected"}
            return None

        module.decide = decide  # type: ignore[attr-defined]
        return module

    result = decide_pre(
        event(), load_sibling=sibling, merge=merge, deadline=time.time() + 0.5,
    )
    assert result["permissionDecision"] == "deny"
    assert "not authorization" in result["additionalContext"]
    assert "private diagnostic" not in str(result)


@pytest.mark.parametrize("failure", ["missing", "raises"])
def test_maximum_native_batch_keeps_denial_with_bounded_recovery(failure: str) -> None:
    def sibling(name: str) -> ModuleType | None:
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        failed = name in {"statelessness_guard.py", "cross_repo_guard.py"}
        if failed and failure == "missing":
            return None
        module = ModuleType(name)

        def decide(payload: dict[str, Any], **kwargs: Any) -> dict[str, str] | None:
            if failed:
                raise OSError("private diagnostic")
            if name == "anchor_write_guard.py" and payload["toolName"] == "edit":
                return {"permissionDecision": "deny", "permissionDecisionReason": "protected"}
            return None

        module.decide = decide  # type: ignore[attr-defined]
        return module

    calls = [{"name": "view"} for _ in range(127)] + [{"name": "edit"}]
    result = decide_pre(
        event(toolCalls=calls), load_sibling=sibling,
        merge=load("hook_client.py")._merge_pre_decisions, deadline=time.time() + 0.5,
    )
    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == "protected"
    frame = {
        "version": 1, "hook_generation": "fixture",
        "hook_capabilities": ["native-tool-policy-v1"], "result": result,
    }
    assert len(json.dumps(frame).encode("utf-8")) + 1 <= 65536
    for name in ("statelessness_guard.py", "cross_repo_guard.py"):
        assert result["additionalContext"].count(f"Boundary: {name} ") == 1
    assert "private diagnostic" not in result["additionalContext"]


@pytest.mark.parametrize("payload", [
    event(toolCalls=[]),
    event(toolCalls=[{"name": "edit", "cwd": "other"}]),
    event(_agentWorktrees={"nativePolicy": 1, "environment": {}}),
])
def test_incomplete_native_input_is_explicitly_unavailable(payload: dict[str, Any]) -> None:
    result = decide_pre(
        payload, load_sibling=load, merge=merge, deadline=time.time() + 0.5,
    )
    assert "permissionDecision" not in result
    assert "not authorization" in result["additionalContext"]


@pytest.mark.parametrize("case", ["late-deny", "warnings-only", "long-reason", "html-reason"])
def test_large_valid_native_output_keeps_decision_inside_response_frame(case: str) -> None:
    def sibling(name: str) -> ModuleType:
        if name in {"tool_call_forms.py", "hook_pre_policy.py"}:
            return load(name)
        module = ModuleType(name)

        def decide(payload: dict[str, Any], **kwargs: Any) -> dict[str, str] | None:
            if name == "statelessness_guard.py" and case not in {"long-reason", "html-reason"}:
                return {"additionalContext": "notice \U0001f680" * 128}
            if name == "anchor_write_guard.py" and payload["toolName"] == "edit":
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "protected " + (
                            "<>&'\"" if case == "html-reason" else "\U0001f680"
                        ) * 12000
                        if case in {"long-reason", "html-reason"} else "protected"
                    ),
                }
            return None

        module.decide = decide  # type: ignore[attr-defined]
        return module

    calls = [{"name": "view"} for _ in range(127)] + [
        {"name": "view" if case == "warnings-only" else "edit"},
    ]
    result = decide_pre(
        event(toolCalls=calls), load_sibling=sibling,
        merge=load("hook_client.py")._merge_pre_decisions, deadline=time.time() + 0.5,
    )
    frame = {
        "version": 1, "hook_generation": "fixture",
        "hook_capabilities": ["native-tool-policy-v1"], "result": result,
    }
    encoded = json.dumps(frame)
    if case == "html-reason":
        for character in "<>&'":
            encoded = encoded.replace(character, "\\u%04x" % ord(character))
    assert len(encoded.encode("utf-8")) + 1 <= 65536
    if case == "warnings-only":
        assert "permissionDecision" not in result
    else:
        assert result["permissionDecision"] == "deny"
        if case == "late-deny":
            assert result["permissionDecisionReason"] == "protected"
        else:
            assert result["permissionDecisionReason"].startswith("protected ")
    field = "permissionDecisionReason" if case in {"long-reason", "html-reason"} else "additionalContext"
    assert "truncat" in result[field]


@pytest.mark.parametrize("command", [
    "Get-Content -LiteralPath 'source.py'", "Get-Date", "",
], ids=["content-read", "clock-read", "empty"])
def test_original_anchor_read_predicate_needs_no_inventory(
    monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    guard = load("anchor_write_guard.py")

    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("a fact-independent read acquired the anchor inventory")

    monkeypatch.setattr(guard, "resolve_registry_root", forbidden)
    monkeypatch.setattr(guard, "load_worktree_anchors", forbidden)
    assert guard.decide(
        {"toolName": "powershell", "toolArgs": {"command": command}},
        env={}, home=Path("/fixture"),
    ) is None


@pytest.mark.parametrize("command", [
    "Set-Content -LiteralPath 'target.py' -Value 'data'",
    "Get-Content source.py > target.py",
], ids=["write-verb", "redirect"])
def test_original_anchor_write_predicate_keeps_inventory(
    monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    guard = load("anchor_write_guard.py")
    calls: list[str] = []

    def registry(**kwargs: Any) -> Path:
        calls.append("root")
        return Path("/fixture")

    def inventory(root: Path) -> list[dict[str, str]]:
        calls.append("inventory")
        return []

    monkeypatch.setattr(guard, "resolve_registry_root", registry)
    monkeypatch.setattr(guard, "load_worktree_anchors", inventory)
    assert guard.decide(
        {"toolName": "powershell", "toolArgs": {"command": command}},
        env={}, home=Path("/fixture"),
    ) is None
    assert calls == ["root", "inventory"]
