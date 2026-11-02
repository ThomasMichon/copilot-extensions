"""Mock-only kqueue/native-birth contract; not native macOS qualification."""

from __future__ import annotations

import errno
import select
from types import SimpleNamespace

import pytest

from zdd import darwin_identity, process_exit

TOKEN = "1780000000:123456"


@pytest.fixture
def native(monkeypatch):
    calls = []
    state = SimpleNamespace(birth=TOKEN, events=[], registration_error=None, on_register=None)

    def control(changes, count, timeout):
        calls.append(("control", changes, count, timeout))
        if changes:
            if state.registration_error:
                raise state.registration_error
            if state.on_register:
                state.on_register()
            return []
        events, state.events = state.events, []
        return events

    def birth(pid):
        calls.append(("birth", pid))
        return state.birth

    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(select, "kqueue", lambda: SimpleNamespace(
        control=control, close=lambda: calls.append(("close",)),
    ), raising=False)
    monkeypatch.setattr(select, "kevent", lambda ident, **kw: SimpleNamespace(
        ident=ident, **kw,
    ), raising=False)
    for name, value in {
        "KQ_FILTER_PROC": -5, "KQ_EV_ADD": 1, "KQ_EV_ONESHOT": 16,
        "KQ_NOTE_EXIT": 0x80000000, "KQ_EV_ERROR": 0x4000,
    }.items():
        monkeypatch.setattr(select, name, value, raising=False)
    monkeypatch.setattr(darwin_identity, "birth_time", birth)
    return state, calls


def _exit(**changes):
    return SimpleNamespace(**{
        "ident": 101, "filter": -5, "fflags": 0x80000000, "flags": 0, "data": 0,
        **changes,
    })


def test_native_birth_checks_bracket_watch_and_retain_one_shot_exit(native):
    state, calls = native
    with process_exit.open_process(101, TOKEN) as reference:
        assert calls[0] == ("birth", 101)
        event = calls[1][1][0]
        assert (event.ident, event.filter, event.flags, event.fflags) == (
            101, -5, 1 | 16, 0x80000000,
        )
        assert calls[1][2:] == (0, 0)
        assert calls[2] == ("birth", 101)
        state.birth = "1780000001:0"
        state.events = [_exit()]
        assert reference.wait(0.01) is True
        observed = len(calls)
        assert reference.wait(0) is True
        assert len(calls) == observed
    assert calls[-1] == ("close",)
    assert [call for call in calls if call[0] == "birth"] == [("birth", 101)] * 2


@pytest.mark.parametrize(
    "birth,error",
    [("1780000001:0", ProcessLookupError), (None, OSError)],
)
def test_mismatched_or_unavailable_native_birth_refuses_capture(native, birth, error):
    state, calls = native
    state.birth = birth
    with pytest.raises(error):
        process_exit.open_process(101, TOKEN, allow_exited=True)
    assert calls[-1] == ("close",)


@pytest.mark.parametrize("allow_exited", [False, True])
def test_exit_during_capture_requires_explicit_observation(native, allow_exited):
    state, calls = native
    state.events = [_exit()]
    if allow_exited:
        with process_exit.open_process(101, TOKEN, allow_exited=True) as reference:
            assert reference.wait(0) is True
    else:
        with pytest.raises(ProcessLookupError, match="exited"):
            process_exit.open_process(101, TOKEN)
    assert calls[-1] == ("close",)


@pytest.mark.parametrize("event", [
    _exit(ident=202), _exit(filter=0), _exit(fflags=0),
    _exit(flags=0x4000, data=errno.EPERM),
])
def test_malformed_or_denied_exit_never_becomes_evidence(native, event):
    state, calls = native
    with process_exit.open_process(101, TOKEN) as reference:
        state.events = [event]
        with pytest.raises((ValueError, OSError)):
            reference.wait(0)
    assert calls[-1] == ("close",)


def test_denied_registration_closes_watch_without_postcheck(native):
    state, calls = native
    state.registration_error = PermissionError(errno.EPERM, "denied")
    with pytest.raises(PermissionError):
        process_exit.open_process(101, TOKEN)
    assert calls[-1] == ("close",)
    assert [call for call in calls if call[0] == "birth"] == [("birth", 101)]


def test_old_watch_exit_cannot_be_bound_to_new_birth_with_allow_exited(native):
    state, calls = native
    state.birth = "1779999999:0"

    def reuse_after_old_watch_attaches():
        state.birth = TOKEN
        state.events = [_exit()]

    state.on_register = reuse_after_old_watch_attaches
    with pytest.raises(ProcessLookupError, match="identity"):
        process_exit.open_process(101, TOKEN, allow_exited=True)
    assert calls == [("birth", 101), ("close",)]


def test_pid_reused_during_registration_fails_postcheck_even_with_exit_event(native):
    state, calls = native

    def reuse_during_registration():
        state.birth = "1780000001:0"
        state.events = [_exit()]

    state.on_register = reuse_during_registration
    with pytest.raises(ProcessLookupError, match="identity"):
        process_exit.open_process(101, TOKEN, allow_exited=True)
    assert [call for call in calls if call[0] == "birth"] == [("birth", 101)] * 2
    assert calls[-1] == ("close",)


def test_missing_kqueue_is_explicitly_unsupported(native, monkeypatch):
    _state, calls = native
    monkeypatch.delattr(select, "kqueue")
    with pytest.raises(NotImplementedError, match="kqueue"):
        process_exit.open_process(101, TOKEN)
    assert calls == []
