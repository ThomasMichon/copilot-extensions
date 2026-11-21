"""Full original-portfolio observation without changing its test selection."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any, Protocol, cast

import pytest

from hook_child_observer import (
    ChildObserver, PostExitChildObserver, Sample, is_native_subprocess, observed_subprocess,
)
from test_hook_packet_receiver_diagnostics import NativeChildPacketObserver
from test_post_tool_sender_diagnostics import ReceiverPacketObserver


class Reporter(Protocol):
    def write_line(self, line: str) -> None: ...


class LateChildObserver(ChildObserver):
    INITIAL_DELAY = 8.0


def observe_policy_dispatch(
    original: Callable[..., dict[str, Any]], policy: Any,
    payload: dict[str, Any], deadline: float | None,
    emit: Callable[[Sample], None],
) -> dict[str, Any]:
    guards: list[Sample] = []
    started = time.monotonic()
    row: Sample = {
        "outcome": "pending", "guards": guards,
        "remaining_entry_ms": None if deadline is None else round((deadline - time.time()) * 1000, 3),
    }

    def load(name: str) -> Any:
        module = policy._module(name)
        if name not in {
            "statelessness_guard.py", "cross_repo_guard.py",
            "anchor_write_guard.py", "pr_supersede_guard.py",
        } or not callable(getattr(module, "decide", None)):
            return module
        original_decide = module.decide

        def decide(*args: Any, **kwargs: Any) -> Any:
            entered = time.monotonic()
            try:
                return original_decide(*args, **kwargs)
            finally:
                if len(guards) < 16:
                    guards.append({
                        "guard": name, "elapsed_ms": round((time.monotonic() - entered) * 1000, 3),
                    })
                else:
                    row["guards_truncated"] = True

        return SimpleNamespace(decide=decide)

    try:
        proxy = SimpleNamespace(
            hook_client=policy.hook_client, _module=load, pre=policy.pre,
        )
    except Exception:
        return original(policy, payload, deadline)
    try:
        emit(row)
    except Exception:
        pass
    try:
        result = original(proxy, payload, deadline)
        row["outcome"] = "returned"
        try:
            decision = result.get("permissionDecision")
            row["decision"] = (
                decision if decision is None or (
                    isinstance(decision, str) and decision in {"allow", "ask", "deny"}
                ) else "other"
            )
            reason = result.get("permissionDecisionReason")
            row["reason_characters"] = len(reason) if isinstance(reason, str) else None
            row["has_recovery_context"] = isinstance(result.get("additionalContext"), str)
        except Exception:
            row["classification_error"] = True
        return result
    except BaseException:
        row["outcome"] = "error"
        raise
    finally:
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        row["remaining_exit_ms"] = (
            None if deadline is None else round((deadline - time.time()) * 1000, 3)
        )


class FullNativeChildObserver(NativeChildPacketObserver):
    MAX_ROWS = 64

    def __init__(self) -> None:
        self.sampling_mode = os.environ.get("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE") or "late"
        if self.sampling_mode not in {"late", "post-exit"}:
            raise ValueError("Invalid native observation mode")
        self.rows: dict[pytest.Function, list[Sample]] = {}
        self.policy_rows: dict[pytest.Function, list[Sample]] = {}
        self.policy_truncated: set[pytest.Function] = set()
        self.truncated: set[pytest.Function] = set()
        self.observed_calls = 0

    def accepts(self, item: pytest.Function) -> bool:
        if (
            item.path.name == "test_native_tool_hook_transport.py"
            and item.originalname == "test_original_guards_execute_without_any_process"
        ):
            return False
        return (
            isinstance(item.funcargs.get("monkeypatch"), pytest.MonkeyPatch)
            and is_native_subprocess(getattr(item.module, "subprocess", None))
        )

    def can_observe(self, namespace: object) -> bool:
        return is_native_subprocess(namespace)

    def subprocess_proxy(self, emit: Callable[[Sample], None]) -> SimpleNamespace:
        factory = PostExitChildObserver if self.sampling_mode == "post-exit" else LateChildObserver
        return observed_subprocess(emit, factory=factory)

    def prepare(self, item: pytest.Function, monkeypatch: pytest.MonkeyPatch) -> None:
        if (
            item.path.name != "test_native_pre_policy.py"
            or item.originalname != "test_registered_full_policy_composition"
        ):
            return
        from agent_worktrees import native_tool_policy

        original = native_tool_policy.dispatch_pre

        def emit(row: Sample) -> None:
            rows = self.policy_rows.setdefault(item, [])
            if len(rows) < self.MAX_ROWS:
                rows.append(row)
            else:
                self.policy_truncated.add(item)

        def dispatch(
            policy: Any, payload: dict[str, Any], deadline: float | None,
        ) -> dict[str, Any]:
            return observe_policy_dispatch(original, policy, payload, deadline, emit)

        monkeypatch.setattr(native_tool_policy, "dispatch_pre", dispatch)

    def emit(self, item: pytest.Function, value: Sample) -> None:
        self.observed_calls += 1
        rows = self.rows.setdefault(item, [])
        if len(rows) < self.MAX_ROWS:
            rows.append(value)
        else:
            self.truncated.add(item)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item,
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        report = yield
        if report.when != "call" or not isinstance(item, pytest.Function):
            return report
        rows = self.rows.pop(item, [])
        policy_rows = self.policy_rows.pop(item, [])
        policy_truncated = item in self.policy_truncated
        self.policy_truncated.discard(item)
        truncated = item in self.truncated
        self.truncated.discard(item)
        if rows or policy_rows or truncated or policy_truncated:
            try:
                body = "\n".join(
                    ["native_child_observation=" + json.dumps(
                        {
                            **row, "sampling_mode": self.sampling_mode,
                            "sampling_delay_ms": 8000 if self.sampling_mode == "late" else None,
                            "test_outcome": report.outcome,
                        },
                        sort_keys=True,
                    )
                    for row in rows]
                    + ["native_policy_observation=" + json.dumps(
                        {**row, "test_outcome": report.outcome}, sort_keys=True,
                    ) for row in policy_rows]
                )
                if truncated:
                    body += "\nnative_child_observation_truncated=true"
                if policy_truncated:
                    body += "\nnative_policy_observation_truncated=true"
            except Exception:
                body = "native_child_observation_report_error=true"
            report.sections.append(("Native child observation", body))
        return report

    def pytest_report_header(self) -> str:
        return "full_native_observation=active; diagnostic only, never acceptance"

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        original = int(session.exitstatus)
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        line = "full_native_observation_end=" + json.dumps({
            "original_exitstatus": original, "observed_calls": self.observed_calls,
            "diagnostic_only": True,
        }, sort_keys=True)
        if reporter is not None:
            cast(Reporter, reporter).write_line(line)
        else:
            print(line)
        if original == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def register(config: pytest.Config) -> None:
    config.pluginmanager.register(FullNativeChildObserver(), name="full-native-child-observer")
    config.pluginmanager.register(ReceiverPacketObserver(), name="full-native-receiver-observer")
