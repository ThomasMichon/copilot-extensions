"""Durable wake steering reaches the supported original-worker CLI."""

from __future__ import annotations

import argparse
import re
import shlex

import httpx
import pytest

from agent_dispatch import __main__ as cli
from tests import test_relevance_closeout_cli as worker

caller = worker.caller
managed_assignment = worker.managed_assignment
assignment = worker.assignment
independent_readiness = worker.independent_readiness


def _instruction(value: worker.Caller) -> list[str]:
    [wake] = value.queue.list_wakes(value.task.id)
    assert "`<agent-dispatch catalog argv[0]> --help`" in wake.message
    assert "Only if that executing CLI lists `relevance-closeout`" in wake.message
    assert "retain custody and report the unsupported boundary" in wake.message
    assert "fall back to abandon" in wake.message
    assert "agent-dispatch show " not in wake.message
    assert "agent-dispatch steer take " not in wake.message
    match = re.search(
        r"`<agent-dispatch catalog argv\[0\]> (relevance-closeout [^`]+)`", wake.message
    )
    assert match is not None
    return shlex.split(match[1])


@pytest.mark.parametrize("caller", ["", "suspended", "submitted", "unassigned"], indirect=True)
def test_wake_discovers_cli_then_closes_out_through_original_request(
    caller: worker.Caller,
    capsys: pytest.CaptureFixture,
) -> None:
    arguments = _instruction(caller)
    [original_wake] = caller.queue.list_wakes(caller.task.id)
    with pytest.raises(SystemExit) as discovery:
        cli.main(["--help"])
    assert discovery.value.code == 0
    assert "relevance-closeout" in capsys.readouterr().out
    assert not caller.requests and not caller.commands
    assert cli.main(arguments) == 0
    assert caller.finalized and caller.queue.get(caller.task.id).status == "abandoned"
    assert caller.queue.list_wakes(caller.task.id) == [original_wake]
    assert not caller.queue.latest_reservation(caller.task.id).release_requested


def test_prior_cli_help_is_safe_and_does_not_consume_or_rewrite_closeout(
    caller: worker.Caller,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _instruction(caller)
    before = (
        caller.queue.relevance_snapshot(caller.task.id),
        caller.queue.list_wakes(caller.task.id),
    )
    legacy = argparse.ArgumentParser()
    legacy.add_subparsers(dest="command").add_parser("show")
    monkeypatch.setattr(cli, "build_parser", lambda: legacy)
    with pytest.raises(SystemExit) as discovery:
        cli.main(["--help"])
    assert discovery.value.code == 0
    assert "relevance-closeout" not in capsys.readouterr().out
    assert not caller.requests and not caller.commands
    assert (
        caller.queue.relevance_snapshot(caller.task.id),
        caller.queue.list_wakes(caller.task.id),
    ) == before


def test_supported_cli_with_rolled_back_receiver_retains_original_wake(
    caller: worker.Caller,
) -> None:
    arguments = _instruction(caller)
    before = (
        caller.queue.relevance_snapshot(caller.task.id),
        caller.queue.list_wakes(caller.task.id),
    )
    caller.rewrite = lambda *_: httpx.Response(404, json={"detail": "Not Found"})
    assert cli.main(arguments) == 1
    assert not caller.posts and not caller.commands
    assert (
        caller.queue.relevance_snapshot(caller.task.id),
        caller.queue.list_wakes(caller.task.id),
    ) == before
