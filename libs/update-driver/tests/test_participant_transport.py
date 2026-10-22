import os
import subprocess
import sys
import threading
from contextvars import Context
from pathlib import Path

import pytest

import agent_procutil
import single_instance_lease
import update_driver
from update_driver import (
    ParticipantError, RequestSpec, TrustedRunner, borrowed_custody, drain,
    enqueue, participant, participant_environment,
)

KEY = "UPDATE_DRIVER_WITNESS"


def prepare(area):
    root = area / "state"
    enqueue(
        RequestSpec("fixture"), runner=TrustedRunner((sys.executable,), str(area)), root=root,
    )
    return root


def source_environment():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        str(Path(package.__file__).resolve().parent.parent)
        for package in (update_driver, agent_procutil, single_instance_lease)
    )
    return env


def run_child(script, root, env):
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)], env=env,
        capture_output=True, text=True, timeout=10, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_ambient_consumption_and_explicit_nested_process_handoff(area, monkeypatch):
    root = prepare(area)
    assert participant_environment() == {}

    def execute(tx):
        original = tx.participant_env()
        monkeypatch.setenv(KEY, original[KEY])
        with borrowed_custody(root=root) as custody:
            assert KEY not in os.environ
            custody.assert_owned()
            handoff = participant_environment()
            assert handoff == original
            # An ordinary asynchronous/background child would inherit exactly
            # this clean environment: no transaction capability is ambient.
            assert run_child(
                "import os; assert 'UPDATE_DRIVER_WITNESS' not in os.environ; print('clean')",
                root, source_environment(),
            ) == "clean"
            explicit = source_environment()
            explicit.update(handoff)
            assert run_child(
                "import os,sys; from pathlib import Path; "
                "from update_driver import borrowed_custody,participant_environment; "
                "custody=borrowed_custody(root=Path(sys.argv[1])); "
                "assert custody is not None; "
                "custody.__enter__(); "
                "assert 'UPDATE_DRIVER_WITNESS' not in os.environ; "
                "assert participant_environment(); custody.__exit__(None,None,None); "
                "assert participant_environment()=={}; print('authorized')",
                root, explicit,
            ) == "authorized"
            assert participant_environment() == handoff
        assert participant_environment() == {}
        assert KEY not in os.environ
        return 0

    drain(root=root, executor=execute)
    assert participant_environment() == {}


def test_explicit_transport_does_not_consume_a_different_ambient_token(area, monkeypatch):
    root = prepare(area)

    def execute(tx):
        transport = tx.participant_env()
        original = dict(transport)
        monkeypatch.setenv(KEY, "unrelated-ambient-token")
        with borrowed_custody(root=root, environ=transport):
            assert os.environ[KEY] == "unrelated-ambient-token"
            assert transport == {}
            assert participant_environment() == original
        assert participant_environment() == {}
        assert os.environ[KEY] == "unrelated-ambient-token"
        return 0

    drain(root=root, executor=execute)


def test_scoped_nesting_restores_outer_context_but_never_ambient_transport(area, monkeypatch):
    root = prepare(area)

    def execute(tx):
        transport = tx.participant_env()
        monkeypatch.setenv(KEY, transport[KEY])
        with borrowed_custody(root=root) as custody:
            outer = participant_environment()
            with participant(root=root, witness=outer[KEY]):
                assert participant_environment() == outer
                assert KEY not in os.environ
            assert participant_environment() == outer
            observations = []
            thread = threading.Thread(
                target=lambda: observations.append(Context().run(participant_environment)),
            )
            thread.start()
            thread.join(5)
            assert not thread.is_alive() and observations == [{}]
            assert participant_environment() == outer
            process_id = custody._process_id
            try:
                custody._process_id = -1
                assert participant_environment() == {}
                with pytest.raises(ParticipantError, match="different process"):
                    custody.assert_owned()
            finally:
                custody._process_id = process_id
        assert participant_environment() == {} and KEY not in os.environ
        return 0

    drain(root=root, executor=execute)


def test_handoff_reasserts_current_custody(area, monkeypatch):
    root = prepare(area)

    def execute(tx):
        with borrowed_custody(root=root, environ=tx.participant_env()) as custody:
            def lost():
                raise ParticipantError("synthetic custody loss")

            monkeypatch.setattr(custody, "assert_owned", lost)
            with pytest.raises(ParticipantError, match="synthetic custody loss"):
                participant_environment()
        assert participant_environment() == {}
        return 0

    drain(root=root, executor=execute)
