"""Native ownership identities, scoped admission and settled refusal controls."""

from contextlib import contextmanager
import os
import subprocess
from unittest.mock import Mock

import pytest

import agent_procutil as pu
from agent_procutil import _job_scopes as scopes


@pytest.mark.parametrize("value", ["", ";", "arbitrary", "Local\\ce-owned-job-" + "0" * 31,
                                      ";".join(["Local\\ce-owned-job-" + "0" * 32] * 2)])
def test_invalid_scope_metadata_refuses(value):
    with pytest.raises(ValueError, match="scope metadata"):
        scopes.scope_names({scopes.ENVIRONMENT_KEY: value})


def test_explicit_child_environment_cannot_drop_outer_cleanup_scopes(monkeypatch):
    outer, inner = scopes.new_name(), scopes.new_name()
    monkeypatch.setenv(scopes.ENVIRONMENT_KEY, outer)
    child = scopes.child_environment({"literal": "retained"}, append=inner)
    assert scopes.scope_names(child) == (outer, inner)
    assert child["literal"] == "retained"
    assert scopes.scope_names(os.environ) == (outer,)


def test_scope_aliases_merge_and_invalid_aliases_cannot_hide(monkeypatch):
    outer, first, second = scopes.new_name(), scopes.new_name(), scopes.new_name()
    monkeypatch.setenv(scopes.ENVIRONMENT_KEY, outer)
    environment = {scopes.ENVIRONMENT_KEY: first, scopes.ENVIRONMENT_KEY.lower(): second}
    child = scopes.child_environment(environment)
    assert scopes.scope_names(child) == (outer, first, second)
    assert list(child) == [scopes.ENVIRONMENT_KEY]
    environment[scopes.ENVIRONMENT_KEY.lower()] = "malformed"
    with pytest.raises(ValueError, match="scope metadata"):
        scopes.child_environment(environment)


@pytest.mark.parametrize("retained", [None, 0, 1])
def test_every_scope_checked_and_query_handles_closed(monkeypatch, retained):
    names = (scopes.new_name(), scopes.new_name())
    events = []
    kernel = Mock()
    kernel.OpenJobObjectW.side_effect = lambda access, inherit, name: (
        events.append(("open", access, inherit, name)) or names.index(name) + 10
    )

    def membership(process, job, inside):
        events.append(("check", process, job))
        inside._obj.value = job == retained + 10 if retained is not None else False
        return True

    kernel.IsProcessInJob.side_effect = membership
    kernel.CloseHandle.side_effect = lambda handle: events.append(("close", handle)) or True
    monkeypatch.setattr(scopes, "_api", lambda: kernel)
    environment = {scopes.ENVIRONMENT_KEY: ";".join(names)}
    if retained is None:
        with scopes.query_scopes(environment) as verify:
            verify(123)
        assert [event for event in events if event[0] == "check"] == [
            ("check", 123, 10), ("check", 123, 11),
        ]
    else:
        with pytest.raises(OSError, match="cleanup Job"):
            with scopes.query_scopes(environment) as verify:
                verify(123)
    assert events[:2] == [("open", 4, False, name) for name in names]
    assert events[-2:] == [("close", 11), ("close", 10)]


def test_unavailable_scope_closes_already_pinned_scopes(monkeypatch):
    kernel = Mock()
    kernel.OpenJobObjectW.side_effect = [10, 0]
    monkeypatch.setattr(scopes, "_api", lambda: kernel)
    monkeypatch.setattr(scopes.ctypes, "WinError", lambda code: OSError("scope unavailable"), raising=False)
    environment = {scopes.ENVIRONMENT_KEY: ";".join((scopes.new_name(), scopes.new_name()))}
    with pytest.raises(OSError, match="unavailable"):
        with scopes.query_scopes(environment):
            pytest.fail("unavailable scope admitted")
    kernel.CloseHandle.assert_called_once_with(10)


@pytest.fixture
def native_admission(monkeypatch, declared_mock_daemon):
    from agent_procutil import _daemon_instance_identity as identity
    from agent_procutil import _daemon_instance_store as store

    process = Mock(pid=123, _handle=456)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "contained_test_mode", lambda: False)
    monkeypatch.setattr(identity, "created_process_identity", lambda child: (child.pid, "windows:1234"))
    monkeypatch.setattr(store, "finish_failed_launch", lambda *args, **kwargs: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    return process


@pytest.mark.parametrize("replacement", [False, True])
def test_direct_native_admission_closes_queries_before_resume(
    tmp_path, monkeypatch, native_admission, replacement,
):
    events = []
    name, supplied = scopes.new_name(), scopes.new_name()
    environment = {scopes.ENVIRONMENT_KEY.lower(): supplied} if replacement else None
    expected = (name, supplied) if replacement else (name,)
    monkeypatch.setenv(scopes.ENVIRONMENT_KEY, name)

    @contextmanager
    def queries(environment):
        assert scopes.scope_names(environment) == expected
        events.append("open")
        yield lambda handle: events.append(("verify", handle))
        events.append("close")

    monkeypatch.setattr(scopes, "query_scopes", queries)
    monkeypatch.setattr(pu, "_resume_suspended_process", lambda pid: events.append(("resume", pid)) or True)
    child = pu.spawn_daemon(["fixture.exe"], cwd=tmp_path, log_path=tmp_path / "log", env=environment)
    assert child is native_admission
    assert events == ["open", ("verify", 456), "close", ("resume", 123)]
    kwargs = subprocess.Popen.call_args.kwargs
    assert kwargs["creationflags"] & pu._CREATE_SUSPENDED
    assert kwargs["creationflags"] & pu._CREATE_BREAKAWAY_FROM_JOB
    assert kwargs["close_fds"]
    assert all(key.upper() != scopes.ENVIRONMENT_KEY for key in kwargs["env"])


@pytest.mark.parametrize("breakaway, contained", [(False, False), (True, True)])
def test_contained_daemon_replacement_environment_retains_outer_scopes(
    tmp_path, monkeypatch, native_admission, breakaway, contained,
):
    outer, supplied = scopes.new_name(), scopes.new_name()
    monkeypatch.setenv(scopes.ENVIRONMENT_KEY, outer)
    monkeypatch.setattr(pu, "contained_test_mode", lambda: contained)
    queries, resume = Mock(), Mock()
    monkeypatch.setattr(scopes, "query_scopes", queries)
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)
    child = pu.spawn_daemon(
        ["fixture.exe"], cwd=tmp_path, log_path=tmp_path / "log",
        env={"literal": "retained", scopes.ENVIRONMENT_KEY.lower(): supplied}, breakaway=breakaway,
    )
    assert child is native_admission
    environment = subprocess.Popen.call_args.kwargs["env"]
    assert scopes.scope_names(environment) == (outer, supplied)
    assert environment["literal"] == "retained"
    assert [key for key in environment if key.upper() == scopes.ENVIRONMENT_KEY] == [scopes.ENVIRONMENT_KEY]
    queries.assert_not_called()
    resume.assert_not_called()


def test_shared_outer_refusal_never_resumes_and_settles_exact_child(
    tmp_path, monkeypatch, native_admission,
):
    @contextmanager
    def retained_scope(environment):
        def verify(handle):
            assert handle == 456
            raise OSError("daemon retained a launcher-owned cleanup Job")
        yield verify

    monkeypatch.setattr(scopes, "query_scopes", retained_scope)
    resume = Mock()
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)
    with pytest.raises(pu.DaemonSpawnError) as caught:
        pu.spawn_daemon(["fixture.exe"], cwd=tmp_path, log_path=tmp_path / "log")
    assert caught.value.cleanup_confirmed
    assert caught.value.process_created is True
    assert caught.value.admission_sent is False
    native_admission.kill.assert_called_once_with()
    native_admission.wait.assert_called_once_with(timeout=5)
    resume.assert_not_called()
    assert subprocess.Popen.call_count == 1
