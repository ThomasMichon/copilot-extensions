"""Actor-free command ordering and refusal controls for the owned producer."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def producer(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    import owned_producer

    return owned_producer


@pytest.mark.parametrize(
    "command_result,overrun,expected", [(0, False, 0), (7, False, 7), (-15, False, 143), (0, True, 124)],
    ids=["success", "command-failure", "signal", "cleanup-overrun"],
)
def test_literal_command_settles_only_after_join(
    producer: ModuleType, monkeypatch: pytest.MonkeyPatch,
    command_result: int, overrun: bool, expected: int,
) -> None:
    events: list[str] = []
    directory = Path("unit-allocation")
    captured: dict[str, Any] = {}
    clock = [0.0]

    class Scope:
        def __init__(self, *args: object, **kwargs: object) -> None:
            events.append("admitted")

        def __enter__(self) -> str:
            events.append("allocated")
            return str(directory)

        def record_result(self, group: int, code: int) -> None:
            captured["result"] = code
            events.append("result")

        def record_outcome(self, code: int) -> None:
            assert code == captured["result"]

        def __exit__(self, *args: object) -> None:
            events.append("settled")

    def joined(command: list[str], **kwargs: Any) -> int:
        events.append("joined")
        captured.update(command=command, **kwargs)
        if overrun:
            clock[0] = 10.0
        return command_result

    monkeypatch.setattr(producer, "OwnedProducerSandbox", Scope)
    monkeypatch.setattr(producer, "run_contained", joined)
    monkeypatch.setattr(producer, "isolated_environment", lambda *args: {"fixture": "private"})
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(producer, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert producer.main([
        "--owner", "machine/project/worktree", "--kind", "spike", "--timeout", "5",
        "--max-processes", "3", "--max-memory-mb", "100", "--max-temp-mb", "50",
        "--", sys.executable, "-c", "literal {'value': 1}", "{scratch}", "two words", "",
    ]) == expected
    assert events == ["admitted", "allocated", "joined", "result", "settled"]
    assert captured["command"] == [
        sys.executable, "-c", "literal {'value': 1}", str(directory / "work"), "two words", "",
    ]
    assert captured["cwd"] == directory / "work" and captured["result"] == expected
    assert captured["env"] == {
        "fixture": "private", "OWNED_PRODUCER_SCRATCH": str(directory / "work"),
    }


def test_owner_refusal_never_reaches_a_command(
    producer: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unsupported artifact owner")

    def forbidden(*args: object, **kwargs: object) -> int:
        pytest.fail("unsupported owner launched a child")

    monkeypatch.setattr(producer, "OwnedProducerSandbox", unavailable)
    monkeypatch.setattr(producer, "run_contained", forbidden)
    assert producer.main([
        "--owner", "machine/project/worktree", "--kind", "report",
        "--", sys.executable, "-c", "pass",
    ]) == 125


@pytest.mark.parametrize(
    "option,complete", [("--own", "--owner"), ("--ki", "--kind"), ("--time", "--timeout")],
    ids=["owner-prefix", "kind-prefix", "timeout-prefix"],
)
def test_abbreviated_options_refuse_before_allocation(
    producer: ModuleType, monkeypatch: pytest.MonkeyPatch, option: str, complete: str,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("an undeclared option reached allocation")

    monkeypatch.setattr(producer, "OwnedProducerSandbox", forbidden)
    args = [
        "--owner", "machine/project/worktree", "--kind", "spike", "--timeout", "5",
        "--", sys.executable, "-c", "pass",
    ]
    args[args.index(complete)] = option
    with pytest.raises(SystemExit) as error:
        producer.main(args)
    assert error.value.code == 2
