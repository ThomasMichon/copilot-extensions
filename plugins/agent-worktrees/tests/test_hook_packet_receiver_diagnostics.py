"""Install an opt-in observer before the unchanged original packet."""

import json
import os
from collections.abc import Callable, Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from hook_child_observer import Sample, observed_subprocess
from test_post_tool_sender_diagnostics import ReceiverPacketObserver


class NativeChildPacketObserver:
    def accepts(self, item: pytest.Function) -> bool:
        targets = {
            ("test_post_tool_shell.py",
             "test_registered_prepared_cell_post_tool_uses_selected_original_owners"): {"read"},
            ("test_managed_session.py",
             "test_registered_file_local_write_reuses_original_guard_facts"): {
                 "patch-input", "structured",
             },
        }
        target = targets.get((item.path.name, item.originalname or ""))
        return target is not None and item.callspec.params.get("case") in target

    def emit(self, item: pytest.Function, value: Sample) -> None:
        print("native_child_observation=" + json.dumps(value, sort_keys=True))

    def can_observe(self, namespace: object) -> bool:
        return True

    def subprocess_proxy(self, emit: Callable[[Sample], None]) -> SimpleNamespace:
        return observed_subprocess(emit)

    def prepare(self, item: pytest.Function, monkeypatch: pytest.MonkeyPatch) -> None:
        pass

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        if not isinstance(item, pytest.Function) or not self.accepts(item):
            yield
            return
        monkeypatch = cast(pytest.MonkeyPatch, item.funcargs["monkeypatch"])
        self.prepare(item, monkeypatch)

        def emit(value: Sample) -> None:
            self.emit(item, value)

        proxy = self.subprocess_proxy(emit)
        original_namespace = getattr(item.module, "subprocess")
        observed_run = proxy.run

        def run(*args: Any, **kwargs: Any) -> Any:
            command = args[0] if args else kwargs.get("args")
            selected = (
                isinstance(command, list) and len(command) >= 4
                and isinstance(command[0], str)
                and Path(command[0]).stem.lower() in {"pwsh", "powershell"}
                and command[1:3] == ["-NoProfile", "-NonInteractive"]
                and kwargs.get("timeout") == 10
            )
            run_selected = (
                observed_run if selected and self.can_observe(original_namespace)
                else original_namespace.run
            )
            return run_selected(*args, **kwargs)

        proxy.run = run
        monkeypatch.setattr(item.module, "subprocess", proxy)
        yield


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("AGENT_WORKTREES_SENDER_DIAGNOSTIC") != "1",
    reason="Explicit original-packet receiver observation; not acceptance",
)
def test_enable_original_packet_receiver_observer(pytestconfig: pytest.Config) -> None:
    pytestconfig.pluginmanager.register(
        ReceiverPacketObserver(), name="prepared-read-receiver-observer",
    )
    pytestconfig.pluginmanager.register(
        NativeChildPacketObserver(), name="prepared-native-child-observer",
    )
    pytest.fail("Original-packet receiver observation is diagnostic only, never native acceptance")
