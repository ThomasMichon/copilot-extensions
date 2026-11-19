"""Incompatible owners never fall back to unclaimed writable scratch."""

from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import artifact_scratch


@pytest.mark.parametrize("support", [
    {}, {"allocator": 1},
    {"schema": artifact_scratch.SCHEMA, "allocator": True, "artifact_retention": 1},
    {"schema": artifact_scratch.SCHEMA, "allocator": 1, "artifact_retention": False},
])
def test_missing_or_ambiguous_owner_capabilities_refuse_before_allocation(
    monkeypatch: pytest.MonkeyPatch, support: dict[str, object],
) -> None:
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    from plugin_test_owned_scratch import OwnedTestSandbox
    from plugin_test_containment import ContainmentError
    monkeypatch.setattr(artifact_scratch, "capabilities", lambda: support)
    monkeypatch.setattr(artifact_scratch, "allocate", lambda *a, **k: pytest.fail("unnegotiated allocation"))
    with pytest.raises(ContainmentError, match="does not support"):
        OwnedTestSandbox("machine/project/worktree", repo, stage="tests")


def test_early_failure_is_retained_after_it_leaves_the_result_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    from plugin_test_owned_scratch import OwnedTestSandbox
    captured: list[bytes | None] = []
    scope = OwnedTestSandbox.__new__(OwnedTestSandbox)
    scope.provider = SimpleNamespace(
        settle_after_cleanup=lambda *a, **k: captured.append(k["diagnostic"]),
        dispose_consumed=lambda *a, **k: pytest.fail("failed evidence was deleted"),
    )
    scope.allocation = {"token": "fixture"}
    scope.ledger, scope.owner, scope.forbidden, scope.stage = Path("unused"), "owner", (), "tests"
    scope.results, scope.group_count, scope.failed = [], 0, False
    for group in range(17):
        scope.record_result(group, int(group == 0))
    assert all(result["exit_code"] == 0 for result in scope.results)
    scope.__exit__(None, None, None)
    assert captured[0] is not None


def test_preparation_preserves_limits_and_the_remaining_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import plugin_test_owned_scratch as owned
    from plugin_test_containment import ContainmentError, Limits

    captured: list[Limits] = []

    def run(*args: object, **kwargs: Any) -> int:
        captured.append(kwargs["limits"])
        return 0

    monkeypatch.setattr(owned, "run_contained", run)
    limits = Limits(wall_seconds=20, max_processes=3, max_memory_mb=100, max_temp_mb=50)
    owned.prepare_owned("fixture", "uv", False, retained=repo, environment={}, sandbox=repo,
                        limits=limits, wall_seconds=4)
    actual = captured[0]
    assert actual.wall_seconds == 4
    assert (actual.max_processes, actual.max_memory_mb, actual.max_temp_mb) == (3, 100, 50)
    with pytest.raises(ContainmentError, match="deadline"):
        owned.prepare_owned("fixture", "uv", False, retained=repo, environment={}, sandbox=repo,
                            limits=limits, wall_seconds=0)
    assert len(captured) == 1


@pytest.mark.parametrize("tail", [
    [], ["python", "-c", "pass"], ["--", "python", "-c", "pass"],
])
def test_owned_producer_rejects_ambiguous_commands_before_allocation(
    monkeypatch: pytest.MonkeyPatch, tail: list[str],
) -> None:
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import owned_producer

    monkeypatch.setattr(owned_producer, "OwnedProducerSandbox", lambda *a, **k: pytest.fail("allocation"))
    with pytest.raises(SystemExit) as error:
        owned_producer.main(["--owner", "machine/project/worktree", "--kind", "spike", *tail])
    assert error.value.code == 2


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_owned_producer_rejects_invalid_deadlines_before_allocation(
    monkeypatch: pytest.MonkeyPatch, timeout: str,
) -> None:
    import sys

    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import owned_producer

    monkeypatch.setattr(owned_producer, "OwnedProducerSandbox", lambda *a, **k: pytest.fail("allocation"))
    with pytest.raises(SystemExit) as error:
        owned_producer.main([
            "--owner", "machine/project/worktree", "--kind", "build",
            "--timeout", timeout, "--", sys.executable, "-c", "pass",
        ])
    assert error.value.code == 2


def test_owned_producer_refuses_unsupported_owner_before_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import owned_producer

    monkeypatch.setattr(artifact_scratch, "capabilities", lambda: {})
    monkeypatch.setattr(artifact_scratch, "allocate", lambda *a, **k: pytest.fail("allocation"))
    monkeypatch.setattr(owned_producer, "run_contained", lambda *a, **k: pytest.fail("command"))
    assert owned_producer.main([
        "--owner", "machine/project/worktree", "--kind", "report",
        "--", sys.executable, "-c", "pass",
    ]) == 125


@pytest.mark.parametrize("result,overrun,expected", [(0, False, 0), (7, False, 7), (-15, False, 143), (0, True, 124)])
def test_owned_producer_preserves_arguments_and_settles_after_join(
    monkeypatch: pytest.MonkeyPatch, result: int, overrun: bool, expected: int,
) -> None:
    from argparse import Namespace
    import sys
    from types import SimpleNamespace

    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import owned_producer

    events: list[str] = []
    sandbox = Path(__file__).resolve().parent
    captured: dict[str, Any] = {}
    clock = [0.0]

    class Scope:
        def __init__(self, *a: object, **k: object) -> None:
            events.append("admitted")

        def __enter__(self) -> str:
            events.append("allocated")
            return str(sandbox)

        def record_result(self, group: int, code: int) -> None:
            captured["result"] = code
            events.append("result")

        def record_outcome(self, code: int) -> None:
            assert code == captured["result"]

        def __exit__(self, *a: object) -> None:
            events.append("settled")

    def run(command: list[str], **kwargs: Any) -> int:
        events.append("joined")
        captured.update(command=command, **kwargs)
        if overrun:
            clock[0] = 100.0
        return result

    monkeypatch.setattr(owned_producer, "OwnedProducerSandbox", Scope)
    monkeypatch.setattr(owned_producer, "run_contained", run)
    monkeypatch.setattr(owned_producer, "isolated_environment", lambda *a: {"fixture": "private"})
    monkeypatch.setattr(owned_producer, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: None)
    args = Namespace(
        owner="machine/project/worktree", kind="spike", timeout=10,
        max_processes=3, max_memory_mb=100, max_temp_mb=50,
        command=[sys.executable, "-c", "print({'kept': 'literal'})", "{scratch}", "two words", ""],
    )
    assert owned_producer.execute(args) == expected
    assert events == ["admitted", "allocated", "joined", "result", "settled"]
    assert captured["command"] == [
        sys.executable, "-c", "print({'kept': 'literal'})", str(sandbox / "work"), "two words", "",
    ]
    assert captured["cwd"] == sandbox / "work"
    assert captured["env"] == {"fixture": "private", "OWNED_PRODUCER_SCRATCH": str(sandbox / "work")}
    assert captured["result"] == expected
    limits = captured["limits"]
    assert (limits.wall_seconds, limits.max_processes, limits.max_memory_mb, limits.max_temp_mb) == (10, 3, 100, 50)
