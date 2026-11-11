"""Host daemon admission preserves startup output and ambiguous custody."""

import io
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_remote_driver.session_host import launcher_spawn


def _phase():
    return {
        "spawn_attempted": False, "creation_refused": False,
        "cleanup_safe": False, "disposable": True,
    }


def test_host_borrows_pipe_through_canonical_admission_and_closes_write_copies(tmp_path, monkeypatch):
    child = Mock()
    borrowed = []
    environment = {"literal": "retained"}

    def admitted(argv, **kwargs):
        assert argv == ["selected-host", "literal argument"]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"] is environment
        assert kwargs["console_children"] is True
        assert "contained_fallback" not in kwargs
        assert kwargs["instance"].runtime_root == tmp_path
        assert kwargs["log_path"] == tmp_path / "host-launch.log"
        stdin, stdout, stderr = kwargs["stdio_fds"]
        assert os.read(stdin, 1) == b"" and stdout == stderr
        borrowed.extend((stdin, stdout))
        os.write(stdout, b"native startup failure\n")
        return child

    monkeypatch.setattr(launcher_spawn, "spawn_daemon", admitted)
    phase = _phase()
    process, stream = launcher_spawn.spawn_host(
        ["selected-host", "literal argument"], cwd=str(tmp_path), environment=environment,
        state_dir=tmp_path, phase=phase,
    )
    try:
        assert process is child
        assert stream.read() == b"native startup failure\n"
        assert phase["spawn_attempted"] and not phase["creation_refused"]
        for descriptor in borrowed:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        stream.close()


@pytest.mark.parametrize("evidence,refused", [
    ({}, False),
    ({"process_created": False}, False),
    ({"process_created": True, "cleanup_confirmed": True, "admission_sent": True}, False),
    ({"process_created": False, "cleanup_confirmed": True, "admission_sent": False}, True),
])
def test_only_attributed_precreation_refusal_releases_disposable_state(
    tmp_path, monkeypatch, evidence, refused,
):
    failure = OSError("fixture admission failure")
    for key, value in evidence.items():
        setattr(failure, key, value)
    descriptors = []
    pipe = os.pipe

    def retained_pipe():
        selected = pipe()
        descriptors.extend(selected)
        return selected

    monkeypatch.setattr(launcher_spawn.os, "pipe", retained_pipe)
    monkeypatch.setattr(launcher_spawn, "spawn_daemon", Mock(side_effect=failure))
    phase = _phase()
    with pytest.raises(OSError) as caught:
        launcher_spawn.spawn_host(
            ["fixture-host"], cwd=str(tmp_path), environment={}, state_dir=tmp_path, phase=phase,
        )
    assert caught.value is failure
    assert phase["spawn_attempted"] and phase["creation_refused"] is refused
    assert not phase["cleanup_safe"]
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("cleanup_error", [False, True])
def test_pre_resume_refusal_keeps_original_evidence_until_exact_instance_settles(
    tmp_path, monkeypatch, cleanup_error,
):
    failure = OSError("strict outer Job retained")
    failure.process_created = True
    failure.cleanup_confirmed = True
    failure.admission_sent = False
    failure.instance_receipt = object()
    observed = []

    def reconcile(phase, error):
        assert error is failure
        observed.append(error.instance_receipt)
        if cleanup_error:
            raise OSError("fixture registry unavailable")
        phase["cleanup_safe"] = True

    monkeypatch.setattr(launcher_spawn, "spawn_daemon", Mock(side_effect=failure))
    monkeypatch.setattr(launcher_spawn, "observe_exit", reconcile)
    phase = _phase()
    with pytest.raises(OSError) as caught:
        launcher_spawn.spawn_host(
            ["fixture-host"], cwd=str(tmp_path), environment={}, state_dir=tmp_path, phase=phase,
        )
    assert observed == [failure.instance_receipt]
    assert caught.value is failure
    assert phase["cleanup_safe"] is not cleanup_error
    assert not phase["creation_refused"]


def test_original_owner_failure_does_not_leave_the_borrowed_startup_stream_unowned(
    tmp_path, monkeypatch, capsys,
):
    from agent_remote_driver.session_host import launcher

    launcher.main(["--support-json"])
    declaration = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(launcher.subprocess, "run", Mock(return_value=SimpleNamespace(
        returncode=0, stdout=json.dumps(declaration),
    )))
    stream = io.BytesIO(b"bounded bootstrap output")
    monkeypatch.setattr(launcher_spawn, "spawn_host", Mock(return_value=(object(), stream)))
    owner = Mock(session_id="fixture-native")
    owner.retain_process.side_effect = RuntimeError("fixture custody unavailable")
    try:
        with pytest.raises(RuntimeError, match="fixture custody unavailable"):
            launcher.launch_session_host(
                ["fixture-must-not-start"], state_dir=tmp_path, session_id=owner.session_id,
                acquisition_launch=owner,
            )
        deadline = time.monotonic() + 2
        while not stream.closed and time.monotonic() < deadline:
            time.sleep(.01)
        assert stream.closed
    finally:
        stream.close()
