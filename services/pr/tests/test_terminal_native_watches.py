"""Terminal workspace evidence never replaces the native watch notification."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import pytest

from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests_service.terminal_transition import adopt

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
    from services.pr.tests.test_terminal_admission import configured, expires, gateway, register, request
else:
    from test_host import Configuration, configuration as configuration
    from test_terminal_admission import configured, expires, gateway, register, request


@pytest.mark.parametrize("until", ["merged", "head_changed"])
def test_unmerged_workspace_retirement_keeps_fresh_native_predicates_and_payload(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, until: str,
) -> None:
    _, _, config, baseline, evidence = configured(configuration)
    receipt = register(baseline, config, "original-native", until=(until,))
    scope = config.bindings[0].scope
    original = ObservationTarget(scope.account, scope.repo, 42, frozenset({receipt["registration_id"]}))
    baseline.observe(original, PRSnapshot(head_sha="a" * 40), expires_at=expires())
    adopt(config)
    selected, _ = gateway(config, evidence, monkeypatch)
    # The normalized fixture is ADO evidence, whose abandoned state is unmerged.
    evidence["sections"]["pull_request"]["data"]["status"] = "abandoned"

    async def close() -> None:
        response = await selected.handle(request(config, "load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }, workspace=True))
        assert response.status == 200
        await selected.drain(timeout=2)

    asyncio.run(close())
    document = json.loads(selected.store.path.read_bytes())
    assert document["terminal_retention"]["records"][0]["evidence"]["state"] == "closed-unmerged"
    assert not selected.store.execute(scope, "pending", {}, expires_at=expires())["events"]
    target = selected.store.observation_targets(expires_at=expires())[0]
    native: dict[str, Any] = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "repo": scope.repo, "pr": 42, "provider": "azure-devops",
        "pr_state": "closed" if until == "merged" else "open",
        "merged": until == "merged", "mergeable": None, "head_sha": "b" * 40,
        "base_ref": "main", "checks_state": "", "title": "native notification", "draft": False,
    }
    selected.store.observe(target, PRSnapshot(
        pr_state=native["pr_state"], merged=native["merged"], head_sha=native["head_sha"],
        provider="azure-devops", native_snapshot=native,
    ), expires_at=expires())
    event = selected.store.execute(scope, "pending", {}, expires_at=expires())["events"][0]
    assert event["transitions"] == [until]
    assert event["snapshot"] == native and event["snapshot"]["head_sha"] == "b" * 40
    assert event["registration_id"] == receipt["registration_id"]
    assert config.store().execute(scope, "pending", {}, expires_at=expires())["events"] == [event]


@pytest.mark.parametrize("expiry", ["before_capture", "during_acquisition"])
def test_original_watch_timeout_wins_over_workspace_terminal_evidence(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, expiry: str,
) -> None:
    _, _, config, baseline, evidence = configured(configuration)
    receipt = baseline.execute(config.bindings[0].scope, "register", {
        "request_id": "deadline", "issued_at": time.time(), "number": 42,
        "until": ("merged",), "deadline_at": time.time() + 1,
    }, expires_at=expires())
    adopt(config)
    selected, workspace = gateway(config, evidence, monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(selected.store, "clock", lambda: clock[0])
    if expiry == "before_capture":
        clock[0] = receipt["deadline_at"] + .01
    else:
        read = workspace.read_owner.read

        def acquire(*args: Any, **kwargs: Any) -> Any:
            result = read(*args, **kwargs)
            clock[0] = receipt["deadline_at"] + .01
            return result

        monkeypatch.setattr(workspace.read_owner, "read", acquire)
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    async def close() -> None:
        response = await selected.handle(request(config, "load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }, workspace=True))
        assert response.status == 200
        await selected.drain(timeout=2)

    asyncio.run(close())
    events = selected.store.execute(
        config.bindings[0].scope, "pending", {}, expires_at=expires(),
    )["events"]
    assert len(events) == 1
    assert events[0]["timed_out"] is True and not events[0]["transitions"]
    assert events[0]["registration_id"] == receipt["registration_id"]
    scope = config.bindings[0].scope
    selected.store.observe(
        ObservationTarget(scope.account, scope.repo, 42, frozenset({receipt["registration_id"]})),
        PRSnapshot(pr_state="closed", merged=True), expires_at=expires(),
    )
    assert selected.store.execute(scope, "pending", {}, expires_at=expires())["events"] == events


def test_native_read_captured_before_workspace_reopen_cannot_freeze_stale_success(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, config, baseline, evidence = configured(configuration)
    receipt = register(baseline, config, "original-epoch")
    adopt(config)
    selected, _ = gateway(config, evidence, monkeypatch)
    scope = config.bindings[0].scope

    def evidence_at(state: str, second: int) -> None:
        evidence["sections"]["pull_request"]["data"]["status"] = state
        for section in evidence["sections"].values():
            section.update(
                observed_from=f"2026-10-30T00:00:{second:02d}.100001Z",
                observed_to=f"2026-10-30T00:00:{second:02d}.900001Z",
            )

    async def scenario() -> None:
        evidence_at("active", 0)
        first = await selected.handle(request(config, "load", {
            "number": 42, "expected_current": None, "budget_seconds": 10,
        }, workspace=True))
        assert first.status == 200
        original_generation = json.loads(first.body)["result"]["descriptor"]["id"]
        captured = selected.store.observation_targets(expires_at=expires())[0]
        assert captured.retention_epoch is not None
        for state, second in (("abandoned", 2), ("active", 4)):
            evidence_at(state, second)
            response = await selected.handle(request(config, "load", {
                "number": 42, "expected_current": original_generation, "budget_seconds": 10,
            }, workspace=True))
            assert response.status == 200
        before = selected.store.path.read_bytes()
        stale = selected.store.observe(captured, PRSnapshot(
            pr_state="closed", merged=True, native_snapshot={"old": "retired native payload"},
        ), expires_at=expires())
        assert stale["status"] == "stale_observation"
        assert selected.store.path.read_bytes() == before
        assert not selected.store.execute(scope, "pending", {}, expires_at=expires())["events"]
        fresh = selected.store.observation_targets(expires_at=expires())[0]
        assert fresh.retention_epoch != captured.retention_epoch
        native: dict[str, Any] = {
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": scope.repo, "pr": 42, "provider": "azure-devops",
            "pr_state": "closed", "merged": True, "mergeable": None,
            "head_sha": "b" * 40, "base_ref": "main", "checks_state": "",
            "title": "fresh native notification", "draft": False,
        }
        selected.store.observe(fresh, PRSnapshot(
            pr_state="closed", merged=True, head_sha="b" * 40, native_snapshot=native,
        ), expires_at=expires())
        event = selected.store.execute(scope, "pending", {}, expires_at=expires())["events"][0]
        assert event["registration_id"] == receipt["registration_id"]
        assert event["snapshot"] == native and event["transitions"] == ["merged"]
        await selected.drain(timeout=2)

    asyncio.run(scenario())
