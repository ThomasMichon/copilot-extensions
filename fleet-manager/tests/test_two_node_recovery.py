"""Installed journal integration, not authenticated resident service transport."""

from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from fleet_manager.ledger import OperationLedger
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent


WORKER = """
import json, os, sys, time
from pathlib import Path
import fleet_manager
from fleet_manager.ledger import OperationLedger
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent, TargetReceipt

assert Path(fleet_manager.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
root, action, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
def intent(target):
    return OperationIntent.from_dict({
        'schema': 'fleet-manager.operation-intent', 'version': 1,
        'operation': 'op-' + target, 'authority': 'fleet-1', 'principal': 'actor-1',
        'account': 'account-1', 'target': target, 'generation': 1,
        'command': 'task.submit', 'parameters': {'objective': 'objective-1'}})
def node(target):
    return NodeJournal(root / (target + '.db'), authority='fleet-1',
                       installation='install-' + target, target=target)

if action == 'receive':
    g = OperationLedger(root / 'gateway.db', authority='fleet-1', installation='gateway-1')
    if phase == 'during':
        original = g._event
        def event(c, operation, name):
            original(c, operation, name)
            if name == 'succeeded':
                os._exit(27)
        g._event = event
    for target in ('slow', 'fast'):
        after = g.result_cursor(target=target, installation='install-' + target)
        g.reconcile_results(target=target, installation='install-' + target,
                            after=after, entries=node(target).results(after=after))
    if phase == 'after':
        os._exit(27)
else:
    n, r = node(action), intent(action)
    n.accept(r)
    if action == 'slow':
        assert n.begin_external(r)
        (root / 'ready').touch()
        deadline = time.monotonic() + 15
        while not (root / 'resolve').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('test resolution signal was not received')
            time.sleep(0.01)
        assert not node(action).begin_external(r)
        value = n.inspect(r).to_dict()
        value.update(status='succeeded', sequence=3, result_ref='slow-evidence-1')
        n.reconcile(r, TargetReceipt.from_dict(value))
    else:
        def effect(c):
            c.execute('CREATE TABLE effects(value TEXT)')
            c.execute("INSERT INTO effects VALUES('fast-result-1')")
            return 'fast-result-1'
        n.commit_cooperative(r, effect)
        def forbidden(c):
            raise AssertionError('duplicate delivery executed the effect')
        n.commit_cooperative(r, forbidden)
"""


def intent(target):
    return OperationIntent.from_dict({
        "schema": "fleet-manager.operation-intent", "version": 1,
        "operation": f"op-{target}", "authority": "fleet-1", "principal": "actor-1",
        "account": "account-1", "target": target, "generation": 1,
        "command": "task.submit", "parameters": {"objective": "objective-1"},
    })


def command(root, action, phase="normal"):
    return [sys.executable, "-I", "-c", WORKER, str(root), action, phase]


def run(root, action, phase="normal", expected=0):
    result = subprocess.run(
        command(root, action, phase), capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize("phase", ["during", "after"])
def test_two_nodes_progress_and_replay_across_receiver_process_crash(tmp_path, phase):
    g = OperationLedger(tmp_path / "gateway.db", authority="fleet-1", installation="gateway-1")
    for target in ("slow", "fast"):
        g.admit(intent(target))
    slow = subprocess.Popen(
        command(tmp_path, "slow"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists():
            assert slow.poll() is None, "slow node exited before UNKNOWN was durable"
            assert time.monotonic() < deadline, "slow node did not reach UNKNOWN"
            time.sleep(0.01)
        run(tmp_path, "fast")
        run(tmp_path, "receive", phase, expected=27)
        assert slow.poll() is None
        assert g.inspect(intent("slow"))["state"] == "uncertain"
        assert g.result_cursor(target="slow", installation="install-slow") == 2
        assert g.inspect(intent("fast"))["state"] == (
            "pending" if phase == "during" else "outcome")
        assert g.result_cursor(target="fast", installation="install-fast") == (
            0 if phase == "during" else 2)

        run(tmp_path, "receive")
        assert slow.poll() is None
        assert g.inspect(intent("slow"))["state"] == "uncertain"
        assert g.inspect(intent("fast"))["receipt"]["result_ref"] == "fast-result-1"
        assert g.result_cursor(target="fast", installation="install-fast") == 2
        (tmp_path / "resolve").touch()
        _, stderr = slow.communicate(timeout=10)
        assert slow.returncode == 0, stderr
        run(tmp_path, "receive")
        run(tmp_path, "receive")

        for target, sequence in (("slow", 3), ("fast", 2)):
            state = g.inspect(intent(target))
            assert state["state"] == "outcome"
            assert state["receipt"]["sequence"] == sequence
            assert state["receipt"]["request_digest"] == intent(target).request_digest
            assert g.result_cursor(target=target, installation=f"install-{target}") == sequence
            n = NodeJournal(tmp_path / f"{target}.db", authority="fleet-1",
                            installation=f"install-{target}", target=target)
            assert not n.begin_external(intent(target))
        with closing(sqlite3.connect(tmp_path / "fast.db")) as connection:
            assert connection.execute("SELECT value FROM effects").fetchall() == [("fast-result-1",)]
        with closing(sqlite3.connect(g.path)) as connection:
            assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 2
            assert connection.execute("SELECT event FROM audit ORDER BY id").fetchall() == [
                ("admitted",), ("admitted",), ("accepted",), ("unknown",),
                ("accepted",), ("succeeded",), ("succeeded",),
            ]
    finally:
        if slow.poll() is None:
            slow.kill()
        slow.communicate(timeout=5)
