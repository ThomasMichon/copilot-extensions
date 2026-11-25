import os
import subprocess
import sys
import threading
from types import SimpleNamespace
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


def transport_module():
    import importlib

    return importlib.import_module("update_driver.participant")


def test_short_unix_socket_address_preserves_legacy_location(monkeypatch, tmp_path):
    transport = transport_module()
    root = Path(tmp_path.anchor) / "q"
    monkeypatch.setattr(
        transport.tempfile, "TemporaryDirectory",
        lambda **kw: pytest.fail("short endpoint allocated another directory"),
    )
    address, owner = transport._unix_socket_address(root)
    assert Path(address).parent == root and Path(address).name.startswith("w-")
    assert owner is None


@pytest.mark.parametrize("name", ["a" * 200, "\u00e9" * 80])
def test_long_unix_socket_address_uses_owned_short_directory(monkeypatch, tmp_path, name):
    transport = transport_module()
    directory = SimpleNamespace(
        name=str(Path(tmp_path.anchor) / "udw-synthetic"), cleanup=lambda: None,
    )
    seen = []
    monkeypatch.setattr(transport.tempfile, "TemporaryDirectory", lambda **kw: (
        seen.append(kw) or directory
    ))
    address, owner = transport._unix_socket_address(tmp_path / name)
    assert owner is directory and address == str(Path(directory.name) / "s")
    assert len(os.fsencode(address)) < transport._UNIX_SOCKET_LIMIT
    assert seen == [{"prefix": "udw-"}]


def test_unavailable_short_temporary_address_is_explicit_and_cleaned(monkeypatch, tmp_path):
    transport = transport_module()
    cleaned = []
    directory = SimpleNamespace(
        name=str(tmp_path / ("t" * 200)), cleanup=lambda: cleaned.append(True),
    )
    monkeypatch.setattr(transport.tempfile, "TemporaryDirectory", lambda **kw: directory)
    with pytest.raises(ParticipantError, match="native socket limit"):
        transport._unix_socket_address(tmp_path / ("q" * 200))
    assert cleaned == [True]


def test_listener_creation_failure_releases_its_owned_temporary_directory(monkeypatch, tmp_path):
    transport = transport_module()
    cleaned = []
    directory = SimpleNamespace(cleanup=lambda: cleaned.append(True))
    monkeypatch.setattr(transport, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(transport, "_unix_socket_address", lambda root: ("short", directory))

    def unavailable(*args, **kwargs):
        raise OSError("synthetic listener refusal")

    monkeypatch.setattr(transport, "Listener", unavailable)
    with pytest.raises(OSError, match="synthetic listener refusal"):
        transport.Witness(tmp_path, "request")
    assert cleaned == [True]


@pytest.mark.parametrize("code", [0, 7])
def test_normal_borrowed_execution_certifies_cleanup_without_masking_exit(area, code):
    root = prepare(area)

    def execute(tx):
        with borrowed_custody(root=root, environ=tx.participant_env()) as custody:
            with custody.execution_scope():
                with custody.execution_scope():
                    custody.assert_owned()
        return code

    drain(root=root, executor=execute)
    request, = update_driver.status(root=root)
    assert request.state == "completed" and request.exit_code == code


def test_abandoned_borrowed_execution_fences_even_if_executor_returns_status(area):
    root = prepare(area)
    later = enqueue(
        RequestSpec("later"), runner=TrustedRunner((sys.executable,), str(area)), root=root,
    )
    observed = []

    def execute(tx):
        observed.append(tx.request.id)
        with borrowed_custody(root=root, environ=tx.participant_env()) as custody:
            with pytest.raises(RuntimeError, match="unconfirmed cleanup"):
                with custody.execution_scope():
                    raise RuntimeError("unconfirmed cleanup")
            with pytest.raises(ParticipantError, match="cleanup is unconfirmed"):
                custody.assert_owned()
        return 7

    with pytest.raises(ParticipantError):
        drain(root=root, executor=execute)
    requests = update_driver.status(root=root)
    assert len(observed) == 1
    assert requests[0].state == "interrupted" and requests[0].exit_code is None
    assert update_driver.status(later.id, root=root).state == "pending"
    with pytest.raises(update_driver.InterruptedError):
        drain(root=root, executor=execute)


def test_downlevel_owner_cannot_silently_accept_execution_custody(monkeypatch, area):
    transport = transport_module()
    custody = transport.BorrowedCustody.__new__(transport.BorrowedCustody)
    custody._process_id = os.getpid()
    custody._connection = object()
    custody._execution_failed = False
    custody.root = area
    custody.request_id = "fixture"
    frames = []
    monkeypatch.setattr(transport, "_send", lambda connection, value: frames.append(value))
    monkeypatch.setattr(transport, "_receive", lambda connection: {
        "valid": True, "nonce": frames[-1]["nonce"],
    })
    with pytest.raises(ParticipantError, match="does not support execution custody"):
        with custody.execution_scope():
            pytest.fail("downlevel owner admitted mutation")
    assert frames[-1]["execution"] == "begin"


@pytest.mark.skipif(os.name == "nt", reason="Native Unix endpoint and filesystem permissions")
def test_long_execution_root_preserves_live_participant_scope_and_cleanup(area):
    import json

    root = prepare(area / ("long-execution-space-" * 7))
    endpoint_directories = []

    def execute(tx):
        witness = tx.participant_env()
        metadata = json.loads(witness[KEY])
        endpoint = Path(metadata["address"])
        assert len(os.fsencode(str(endpoint))) < transport_module()._UNIX_SOCKET_LIMIT
        assert endpoint.parent != root
        assert endpoint.parent.stat().st_mode & 0o077 == 0
        assert metadata["root"] == str(root) and metadata["request_id"] == tx.request.id
        endpoint_directories.append(endpoint.parent)
        with borrowed_custody(root=root, environ=witness) as custody:
            custody.assert_owned()
        return 0

    drain(root=root, executor=execute)
    request, = update_driver.status(root=root)
    assert request.state == "completed" and request.exit_code == 0
    assert all(not directory.exists() for directory in endpoint_directories)


@pytest.mark.skipif(os.name == "nt", reason="Native Unix private endpoint permissions")
@pytest.mark.parametrize("unsafe", ["permissions", "foreign-owner", "link"])
def test_short_endpoint_rejects_nonprivate_or_unowned_directories(area, monkeypatch, unsafe):
    transport = transport_module()
    directory = area / "udw-owned"
    directory.mkdir(mode=0o700)
    endpoint = directory / "s"
    if unsafe == "permissions":
        directory.chmod(0o755)
    elif unsafe == "foreign-owner":
        monkeypatch.setattr(transport.os, "getuid", lambda: directory.stat().st_uid + 1)
    else:
        target = area / "udw-link"
        target.symlink_to(directory, target_is_directory=True)
        endpoint = target / "s"
    with pytest.raises(ParticipantError, match="owned private directory"):
        transport._validate_unix_address(str(endpoint), area / "execution")


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
