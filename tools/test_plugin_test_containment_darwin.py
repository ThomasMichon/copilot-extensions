"""Portable native-ABI controls and an opt-in-by-platform macOS group witness."""
from __future__ import annotations

import ctypes
import errno
import io
import os
import select
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tools import plugin_test_containment as containment
from tools import plugin_test_containment_darwin as darwin
from tools import plugin_test_containment_posix as posix_observation

MIB = 1024 * 1024
PGID = 7000


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class Kernel:
    def __init__(self):
        self.pids = [41]
        self.snapshots = None
        self.inventory_size = None
        self.inventory_errno = 0
        self.inventory_calls = []
        self.calls = {}
        self.hook = None
        self.status = {}
        self.groups = {}
        self.resident = {}
        self.identities = {}
        self.library = SimpleNamespace(
            proc_listpids=Function(self.listpids), proc_pidinfo=Function(self.pidinfo),
        )

    def load(self, path, *, use_errno):
        assert path == "/usr/lib/libproc.dylib" and use_errno is True
        return self.library

    def listpids(self, kind, pgid, buffer, size):
        assert kind == 2 and pgid == PGID and size == ctypes.sizeof(buffer)
        self.inventory_calls.append(size)
        ctypes.set_errno(self.inventory_errno)
        if self.inventory_size is not None:
            return self.inventory_size
        pids = self.pids
        if self.snapshots is not None:
            pids = self.snapshots[min(len(self.inventory_calls) - 1, len(self.snapshots) - 1)]
        values = pids[:size // 4]
        for index, value in enumerate(values):
            buffer[index] = value
        return len(values) * 4

    def pidinfo(self, pid, flavor, zombies, pointer, size):
        structure = {13: darwin._ShortBSDInfo, 17: darwin._UniqueInfo,
                     4: darwin._TaskInfo}[flavor]
        assert size == ctypes.sizeof(structure)
        assert zombies == (0 if flavor == 4 else 1)
        key = pid, flavor
        self.calls[key] = self.calls.get(key, 0) + 1
        data = structure()
        if flavor == 13:
            data.pid, data.pgid = pid, self.groups.get(pid, PGID)
            data.status = self.status.get(pid, 2)
        elif flavor == 17:
            data.uniqueid = self.identities.get(pid, pid * 10)
            data.idversion = 1
            data.uuid[0] = 1
        else:
            data.resident_size = self.resident.get(pid, 33 * MIB)
            data.virtual_size = data.resident_size * 2
        if self.hook:
            result = self.hook(pid, flavor, self.calls[key], data)
            if result is not None:
                return result
        ctypes.memmove(pointer, ctypes.byref(data), size)
        return size


@pytest.fixture
def kernel(monkeypatch):
    fake = Kernel()
    monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(darwin.ctypes, "CDLL", fake.load)
    return fake


def test_native_struct_sizes_and_offsets():
    assert ctypes.sizeof(darwin._ShortBSDInfo) == 64
    assert darwin._ShortBSDInfo.status.offset == 12
    assert darwin._ShortBSDInfo.pgid.offset == 8
    assert ctypes.sizeof(darwin._UniqueInfo) == 56
    assert darwin._UniqueInfo.uniqueid.offset == 16
    assert darwin._UniqueInfo.idversion.offset == 32
    assert ctypes.sizeof(darwin._TaskInfo) == 96
    assert darwin._TaskInfo.resident_size.offset == 8
    assert darwin._TaskInfo.policy.offset == 48
    assert darwin._TaskInfo.priority.offset == 92


def test_constructed_api_counts_and_sums_native_bytes(kernel):
    kernel.pids = [41, 42]
    kernel.resident = {41: 12345, 42: 67890}
    assert darwin.group_usage(PGID) == (2, 80235)
    assert kernel.inventory_calls == [256, 256]
    assert kernel.calls[41, 4] == 1
    assert kernel.library.proc_listpids.restype is ctypes.c_int
    assert len(kernel.library.proc_pidinfo.argtypes) == 5


def test_complete_kernel_empty_group_is_positive_evidence(kernel):
    kernel.pids = []
    assert darwin.group_usage(PGID) == (0, 0)
    assert darwin.group_usage(PGID, live_only=True) == (0, 0)
    assert len(kernel.inventory_calls) == 4
    assert kernel.calls == {}


@pytest.mark.parametrize("live_only,expected", [(False, 2), (True, 1)])
def test_zombies_count_only_for_process_budget_without_task_reads(kernel, live_only, expected):
    kernel.pids = [41, 42]
    kernel.status[42] = 5
    assert darwin.group_usage(PGID, live_only=live_only) == (expected, 33 * MIB)
    assert (42, 4) not in kernel.calls


@pytest.mark.parametrize("live_only,count", [(False, 1), (True, 0)])
def test_explicit_transition_to_zombie_removes_resident_bytes(kernel, live_only, count):
    def terminal(pid, flavor, call, data):
        if flavor == 4:
            kernel.status[pid] = 5
    kernel.hook = terminal
    assert darwin.group_usage(PGID, live_only=live_only) == (count, 0)


def test_terminal_to_live_without_new_incarnation_is_invalid(kernel):
    kernel.status[41] = 5
    def impossible(pid, flavor, call, data):
        if flavor == 13 and call == 2:
            data.status = 2
    kernel.hook = impossible
    with pytest.raises(ValueError, match="terminal member became live"):
        darwin.group_usage(PGID)


@pytest.mark.parametrize("pgid", [0, -1, 0x80000000, True, "7000"])
def test_invalid_group_id_refuses_before_native_access(pgid, monkeypatch):
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: pytest.fail("invalid PGID reached native API"))
    with pytest.raises(ValueError, match="process-group identifier"):
        darwin.group_usage(pgid)


def test_inventory_grows_before_accepting_complete_pid_set(kernel):
    kernel.pids = list(range(100, 200))
    assert darwin.group_usage(PGID) == (100, 3300 * MIB)
    assert kernel.inventory_calls == [256, 512, 256, 512]


@pytest.mark.parametrize("size", [-1, 1, 3, 260])
def test_malformed_inventory_size_is_not_partial_success(kernel, size):
    kernel.inventory_size = size
    with pytest.raises((OSError, ValueError)):
        darwin.group_usage(PGID)


@pytest.mark.parametrize("pids", [[0], [-1], [41, 41]])
def test_invalid_or_duplicate_pids_refuse(kernel, pids):
    kernel.pids = pids
    with pytest.raises(ValueError, match="inventory identities"):
        darwin.group_usage(PGID)


def test_inventory_cap_refuses_a_full_buffer(kernel, monkeypatch):
    monkeypatch.setattr(darwin, "MAX_PIDS", 64)
    kernel.pids = list(range(1, 65))
    with pytest.raises(ValueError, match="inventory bound"):
        darwin.group_usage(PGID)


@pytest.mark.parametrize("native_errno", [errno.EPERM, errno.EIO, errno.ESRCH])
def test_zero_inventory_with_errno_is_not_empty_evidence(kernel, native_errno):
    kernel.pids = []
    kernel.inventory_errno = native_errno
    with pytest.raises(OSError) as caught:
        darwin.group_usage(PGID)
    assert caught.value.errno == native_errno


@pytest.mark.parametrize("flavor", [13, 17, 4])
@pytest.mark.parametrize("native_errno", [errno.EPERM, errno.EINVAL, errno.EIO, 0])
@pytest.mark.parametrize("live_only", [False, True])
def test_uninspectable_live_member_never_becomes_zero_or_partial(
    kernel, flavor, native_errno, live_only,
):
    kernel.pids = [40, 41]
    def denied(pid, selected, call, data):
        if pid == 41 and selected == flavor:
            ctypes.set_errno(native_errno)
            return 0
    kernel.hook = denied
    with pytest.raises(OSError) as caught:
        darwin.group_usage(PGID, live_only=live_only)
    assert caught.value.errno == (native_errno or errno.EIO)


@pytest.mark.parametrize("flavor", [13, 17, 4])
def test_disappeared_member_restarts_whole_census(kernel, flavor):
    def gone(pid, selected, call, data):
        if selected == flavor:
            kernel.pids = []
            ctypes.set_errno(errno.ESRCH)
            return 0
    kernel.hook = gone
    assert darwin.group_usage(PGID) == (0, 0)
    assert len(kernel.inventory_calls) == 3


def test_unconfirmed_disappearance_exhausts_bounded_passes(kernel):
    def gone(pid, flavor, call, data):
        ctypes.set_errno(errno.ESRCH)
        return 0
    kernel.hook = gone
    with pytest.raises(darwin.GroupObservationChanged, match="churn exceeded") as caught:
        darwin.group_usage(PGID)
    assert len(kernel.inventory_calls) == darwin.MAX_PASSES
    error = caught.value
    assert (error.pid, error.flavor, error.phase) == (41, 17, "pidinfo")
    assert error.native_errno == errno.ESRCH
    assert error.last.reason == "member disappeared"
    assert error.__cause__ is error.last


@pytest.mark.parametrize("flavor", [13, 17, 4])
@pytest.mark.parametrize("size", [-1, 1])
def test_partial_native_member_reads_refuse(kernel, flavor, size):
    kernel.hook = lambda pid, selected, call, data: size if selected == flavor else None
    with pytest.raises(ValueError, match="partial member inspection"):
        darwin.group_usage(PGID)


@pytest.mark.parametrize("mode", ["pid", "state", "uniqueid"])
def test_malformed_identity_refuses(kernel, mode):
    def malformed(pid, flavor, call, data):
        if flavor == 13 and mode == "pid":
            data.pid += 1
        elif flavor == 13 and mode == "state":
            data.status = 0
        elif flavor == 17 and mode == "uniqueid":
            data.uniqueid = 0
    kernel.hook = malformed
    with pytest.raises(ValueError, match="identity|incarnation"):
        darwin.group_usage(PGID)


@pytest.mark.parametrize("field", ["uniqueid", "idversion", "uuid"])
def test_pid_reuse_or_exec_around_rss_never_returns_mixed_rows(kernel, field):
    generation = 1
    def change(pid, flavor, call, data):
        nonlocal generation
        if flavor == 4:
            generation += 1
        elif flavor == 17:
            if field == "uuid":
                data.uuid[0] = generation
            else:
                setattr(data, field, generation)
    kernel.hook = change
    with pytest.raises(ValueError, match="churn exceeded"):
        darwin.group_usage(PGID)
    assert kernel.calls[41, 4] == darwin.MAX_PASSES


def test_group_change_during_rss_forces_fresh_census(kernel):
    def moved(pid, flavor, call, data):
        if flavor == 4:
            kernel.groups[pid] = PGID + 1
    kernel.hook = moved
    with pytest.raises(darwin.GroupObservationChanged, match="churn exceeded") as caught:
        darwin.group_usage(PGID)
    assert caught.value.pid == 41
    assert caught.value.flavor == 13
    assert caught.value.phase == "group-membership"
    assert caught.value.last.observed_pgid == PGID + 1


def test_inventory_churn_is_bounded(kernel):
    kernel.snapshots = [[41], [42]] * darwin.MAX_PASSES
    with pytest.raises(darwin.GroupObservationChanged, match="churn exceeded") as caught:
        darwin.group_usage(PGID)
    assert len(kernel.inventory_calls) == 2 * darwin.MAX_PASSES
    assert (caught.value.pid, caught.value.phase) == (41, "inventory-after")


def test_exhaustion_preserves_last_change_not_first_symptom(kernel):
    def changes(pid, flavor, call, data):
        if flavor == 17 and call == 1:
            kernel.groups[pid] = PGID + 1
            ctypes.set_errno(errno.ESRCH)
            return 0
    kernel.hook = changes
    with pytest.raises(darwin.GroupObservationChanged) as caught:
        darwin.group_usage(PGID)
    assert caught.value.last.reason == "member group changed"
    assert caught.value.flavor == 13
    assert caught.value.phase == "group-membership"
    assert "observed_pgid=7001" in str(caught.value)


def test_transient_inventory_churn_discards_prior_rows(kernel):
    kernel.snapshots = [[41], [42], [42], [42]]
    kernel.resident = {41: MIB, 42: 2 * MIB}
    assert darwin.group_usage(PGID) == (1, 2 * MIB)


@pytest.mark.parametrize("mode", ["library", "symbol", "abi", "platform"])
def test_unavailable_native_inspector_refuses(kernel, monkeypatch, mode):
    if mode == "library":
        def absent(*args, **kwargs):
            raise OSError("unavailable")
        monkeypatch.setattr(ctypes, "CDLL", absent)
    elif mode == "symbol":
        del kernel.library.proc_pidinfo
    elif mode == "platform":
        monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="linux"))
    else:
        original = ctypes.sizeof
        monkeypatch.setattr(ctypes, "sizeof",
                            lambda value: 95 if value is darwin._TaskInfo else original(value))
    with pytest.raises((OSError, ValueError)):
        darwin.group_usage(PGID)


@pytest.mark.parametrize("live_only", [False, True])
def test_darwin_dispatch_uses_native_without_spawning_or_ps_fallback(kernel, monkeypatch, live_only):
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin"))
    def forbidden(*args, **kwargs):
        pytest.fail("Darwin observation attempted subprocess or non-native fallback")
    monkeypatch.setattr(containment, "_linux_group_usage", forbidden)
    monkeypatch.setattr(containment, "_ps_group_usage", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    observe = containment._posix_live_group_usage if live_only else containment._posix_group_usage
    assert observe(PGID) == (1, 33 * MIB)


def test_darwin_native_error_never_falls_back_to_ps(kernel, monkeypatch):
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(containment, "_ps_group_usage", lambda *a, **k: pytest.fail("ps fallback"))
    kernel.inventory_size = 0
    kernel.inventory_errno = errno.EPERM
    with pytest.raises(OSError):
        containment._posix_group_usage(PGID)


def test_standalone_containment_dispatch_resolves_sibling_helper(kernel, monkeypatch):
    monkeypatch.setattr(posix_observation, "__package__", None)
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setitem(sys.modules, "plugin_test_containment_darwin", darwin)
    assert containment._posix_group_usage(PGID) == (1, 33 * MIB)


@pytest.mark.parametrize("platform", ["linux", "freebsd"])
@pytest.mark.parametrize("live_only", [False, True])
def test_other_posix_dispatch_is_unchanged(monkeypatch, platform, live_only):
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(containment, "_darwin_group_usage", lambda *a, **k: pytest.fail("Darwin dispatch"))
    calls = []
    def linux(pgid, **kwargs):
        calls.append(("linux", pgid, kwargs.get("live_only", False)))
        return None
    def ps(pgid, **kwargs):
        calls.append(("ps", pgid, kwargs.get("live_only", False)))
        return 2, 12345
    monkeypatch.setattr(containment, "_linux_group_usage", linux)
    monkeypatch.setattr(containment, "_ps_group_usage", ps)
    observe = containment._posix_live_group_usage if live_only else containment._posix_group_usage
    assert observe(PGID) == (2, 12345)
    assert calls == [("linux", PGID, live_only), ("ps", PGID, live_only)]


def _known_churn():
    return darwin.GroupObservationChanged(darwin._CensusChanged(
        "member disappeared", pid=41, flavor=17, phase="pidinfo", native_errno=errno.ESRCH,
    ))


@pytest.fixture
def cleanup_clock(monkeypatch):
    events = []
    tick = [0]
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(containment.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(containment.os, "killpg",
                        lambda pid, sig: events.append(("signal", sig)), raising=False)
    def identity(pid):
        events.append(("identity", pid))
        return pid, pid
    monkeypatch.setattr(containment, "_posix_group_identity", identity)
    monkeypatch.setattr(containment.time, "monotonic", lambda: tick[0] * 0.05)
    def sleep(seconds):
        assert seconds == 0.05
        tick[0] += 1
        events.append(("sleep", seconds))
    monkeypatch.setattr(containment.time, "sleep", sleep)
    return SimpleNamespace(events=events, tick=tick)


def test_cleanup_typed_churn_then_complete_empty_is_verified(cleanup_clock, monkeypatch):
    rows = iter([(1, 0), _known_churn(), _known_churn(), (0, 0)])
    def observe(pid):
        row = next(rows)
        if isinstance(row, Exception):
            raise row
        return row
    monkeypatch.setattr(containment, "_posix_live_group_usage", observe)
    containment._terminate_posix_group(PGID, (PGID, PGID))
    assert cleanup_clock.events == [
        ("identity", PGID), ("signal", containment.signal.SIGTERM),
        ("identity", PGID), ("signal", 9), ("sleep", 0.05), ("sleep", 0.05),
    ]


def test_cleanup_persistent_churn_deadline_is_unknown_not_live_or_success(cleanup_clock, monkeypatch):
    churn = _known_churn()
    calls = [0]
    def observe(pid):
        calls[0] += 1
        if calls[0] == 1:
            return 1, 0
        raise churn
    monkeypatch.setattr(containment, "_posix_live_group_usage", observe)
    with pytest.raises(containment.UnreapedProcessError, match="remained unknown") as caught:
        containment._terminate_posix_group(PGID, (PGID, PGID))
    assert caught.value.__cause__ is churn
    assert churn.last.reason == "member disappeared"
    assert cleanup_clock.tick[0] == 40
    assert [event for event in cleanup_clock.events if event[0] == "signal"] == [
        ("signal", containment.signal.SIGTERM), ("signal", 9),
    ]


def test_term_churn_deadline_still_verifies_identity_before_kill(cleanup_clock, monkeypatch):
    def observe(pid):
        if ("signal", 9) not in cleanup_clock.events:
            raise _known_churn()
        return 0, 0
    monkeypatch.setattr(containment, "_posix_live_group_usage", observe)
    containment._terminate_posix_group(PGID, (PGID, PGID))
    assert cleanup_clock.tick[0] == 40
    assert cleanup_clock.events[-2:] == [("identity", PGID), ("signal", 9)]


def test_term_and_kill_churn_each_retain_original_two_second_bound(cleanup_clock, monkeypatch):
    monkeypatch.setattr(containment, "_posix_live_group_usage",
                        lambda pid: (_ for _ in ()).throw(_known_churn()))
    with pytest.raises(containment.UnreapedProcessError, match="remained unknown"):
        containment._terminate_posix_group(PGID, (PGID, PGID))
    assert cleanup_clock.tick[0] == 80
    assert cleanup_clock.events.count(("identity", PGID)) == 2


def test_churn_never_waives_identity_refusal_before_kill(cleanup_clock, monkeypatch):
    def identity(pid):
        if cleanup_clock.tick[0] >= 40:
            return pid, pid + 1
        return pid, pid
    monkeypatch.setattr(containment, "_posix_group_identity", identity)
    monkeypatch.setattr(containment, "_posix_live_group_usage",
                        lambda pid: (_ for _ in ()).throw(_known_churn()))
    with pytest.raises(containment.UnreapedProcessError, match="stale or reused"):
        containment._terminate_posix_group(PGID, (PGID, PGID))
    assert ("signal", 9) not in cleanup_clock.events


@pytest.mark.parametrize("failure", [
    PermissionError(errno.EPERM, "fixture denial"),
    OSError(errno.ENOTSUP, "fixture unavailable"),
    ValueError("group churn exceeded but not typed"),
    None,
])
@pytest.mark.parametrize("after_kill", [False, True])
def test_cleanup_other_unknown_states_remain_immediately_fatal(
    cleanup_clock, monkeypatch, failure, after_kill,
):
    def observe(pid):
        if after_kill and ("signal", 9) not in cleanup_clock.events:
            return 1, 0
        if failure is not None:
            raise failure
        return None
    monkeypatch.setattr(containment, "_posix_live_group_usage", observe)
    with pytest.raises(containment.UnreapedProcessError, match="Cannot verify"):
        containment._terminate_posix_group(PGID, (PGID, PGID))
    assert cleanup_clock.tick[0] == 0
    assert (("signal", 9) in cleanup_clock.events) is after_kill


@pytest.mark.parametrize("platform", ["linux", "freebsd", "win32"])
def test_typed_churn_retry_is_darwin_cleanup_only(cleanup_clock, monkeypatch, platform):
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(containment, "_posix_live_group_usage",
                        lambda pid: (_ for _ in ()).throw(_known_churn()))
    with pytest.raises(containment.UnreapedProcessError, match="Cannot verify"):
        containment._terminate_posix_group(PGID, (PGID, PGID))
    assert cleanup_clock.tick[0] == 0


def test_resource_polling_churn_is_still_fatal_not_zero_or_skipped(kernel, monkeypatch):
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin"))
    kernel.snapshots = [[41], [42]] * darwin.MAX_PASSES
    with pytest.raises(darwin.GroupObservationChanged):
        containment._posix_group_usage(PGID)


@pytest.fixture
def poll_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[])
    def sleep(seconds):
        assert seconds >= 0
        clock.sleeps.append(seconds)
        clock.now += seconds
    clock.time = SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
    monkeypatch.setattr(containment, "time", clock.time)
    monkeypatch.setattr(posix_observation, "time", clock.time)
    monkeypatch.setattr(darwin, "time", clock.time)
    return clock


def test_poll_cycle_transient_churn_returns_only_complete_fresh_counters(poll_clock, monkeypatch):
    calls = []
    def observe(pid, *, deadline):
        calls.append((pid, deadline))
        poll_clock.now += 0.1
        if len(calls) == 1:
            raise _known_churn()
        return 3, 1234567
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    assert posix_observation._darwin_poll_group_usage(PGID, deadline=1.0) == (3, 1234567)
    assert calls == [(PGID, 1.0), (PGID, 1.0)]
    assert poll_clock.sleeps == [0.05]
    assert poll_clock.now == pytest.approx(0.25)


def test_poll_cycle_persistent_churn_stops_at_deadline_with_last_cause(poll_clock, monkeypatch):
    errors = []
    def observe(pid, *, deadline):
        error = _known_churn()
        errors.append(error)
        raise error
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    with pytest.raises(darwin.GroupObservationChanged) as caught:
        posix_observation._darwin_poll_group_usage(PGID, deadline=0.11)
    assert caught.value is errors[-1]
    assert caught.value.last.native_errno == errno.ESRCH
    assert poll_clock.now == pytest.approx(0.11)
    assert poll_clock.sleeps == pytest.approx([0.05, 0.05, 0.01])


@pytest.mark.parametrize("failure", [
    PermissionError(errno.EPERM, "fixture denial"),
    OSError(errno.ENOTSUP, "fixture unavailable"),
    ValueError("fixture malformed"),
    TimeoutError(errno.ETIMEDOUT, "native error, not cycle expiration"),
])
def test_poll_cycle_other_errors_are_immediate(poll_clock, monkeypatch, failure):
    def observe(*args, **kwargs):
        raise failure
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    with pytest.raises(type(failure)) as caught:
        posix_observation._darwin_poll_group_usage(PGID, deadline=1.0)
    assert caught.value is failure
    assert poll_clock.sleeps == []


@pytest.mark.parametrize("row", [None, (1,), (-1, 0), (0, -1), (True, 1)])
def test_poll_cycle_never_accepts_unknown_or_partial_counters(poll_clock, monkeypatch, row):
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", lambda *a, **k: row)
    with pytest.raises(ValueError, match="complete non-negative"):
        posix_observation._darwin_poll_group_usage(PGID, deadline=1.0)
    assert poll_clock.sleeps == []


def test_poll_cycle_no_budget_does_not_start_census(poll_clock, monkeypatch):
    monkeypatch.setattr(posix_observation, "_darwin_group_usage",
                        lambda *a, **k: pytest.fail("census began without cycle budget"))
    with pytest.raises(darwin._ObservationDeadline):
        posix_observation._darwin_poll_group_usage(PGID, deadline=0.0)


def test_poll_cycle_discards_late_complete_result(poll_clock, monkeypatch):
    def observe(*args, **kwargs):
        poll_clock.now = 1.1
        return 2, MIB
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    with pytest.raises(darwin._ObservationDeadline, match="outside its cycle"):
        posix_observation._darwin_poll_group_usage(PGID, deadline=1.0)
    assert poll_clock.sleeps == []


def test_poll_cycle_deadline_preserves_prior_known_change(poll_clock, monkeypatch):
    change = _known_churn()
    calls = [0]
    def observe(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise change
        poll_clock.now = 1.0
        raise darwin._ObservationDeadline(errno.ETIMEDOUT, "fixture cycle expired")
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    with pytest.raises(darwin.GroupObservationChanged) as caught:
        posix_observation._darwin_poll_group_usage(PGID, deadline=1.0)
    assert caught.value is change
    assert caught.value.last.pid == 41


def test_native_cycle_expiration_stops_further_calls_and_rejects_late_empty(
    kernel, poll_clock,
):
    kernel.pids = []
    original = kernel.library.proc_listpids.function
    def slow(*args):
        poll_clock.now = 0.2
        return original(*args)
    kernel.library.proc_listpids.function = slow
    with pytest.raises(darwin._ObservationDeadline):
        darwin.group_usage(PGID, deadline=0.1)
    assert len(kernel.inventory_calls) == 1
    assert kernel.calls == {}


@pytest.mark.parametrize("failure", [errno.EPERM, errno.EINVAL])
def test_native_error_after_deadline_is_not_mislabeled_as_churn(kernel, poll_clock, failure):
    def denied(pid, flavor, call, data):
        poll_clock.now = 0.2
        ctypes.set_errno(failure)
        return 0
    kernel.hook = denied
    with pytest.raises(OSError) as caught:
        darwin.group_usage(PGID, deadline=0.1)
    assert caught.value.errno == failure
    assert not isinstance(caught.value, darwin._ObservationDeadline)


def test_native_deadline_after_known_churn_retains_last_numeric_cause(kernel, poll_clock):
    def gone(pid, flavor, call, data):
        poll_clock.now = 0.2
        ctypes.set_errno(errno.ESRCH)
        return 0
    kernel.hook = gone
    with pytest.raises(darwin.GroupObservationChanged) as caught:
        darwin.group_usage(PGID, deadline=0.1)
    assert (caught.value.pid, caught.value.flavor, caught.value.native_errno) == (41, 17, errno.ESRCH)
    assert len(kernel.inventory_calls) == 1


@pytest.fixture
def poll_process(poll_clock, monkeypatch):
    events = []
    stderr = io.StringIO()
    class Ready:
        def unlink(self, **kwargs):
            events.append("ready-unlink")
        def write_text(self, *args, **kwargs):
            events.append("ready")
    class Sandbox:
        def mkdir(self, **kwargs):
            pass
        def __truediv__(self, value):
            return Ready()
    descriptors = iter([(10, 11), (12, 13)])
    monkeypatch.setattr(containment, "os", SimpleNamespace(
        name="posix", pipe=lambda: next(descriptors), set_blocking=lambda *a: None,
        close=lambda fd: events.append(("close", fd)),
    ))
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin", stderr=stderr))
    monkeypatch.setattr(containment.subprocess, "Popen",
                        lambda *a, **k: SimpleNamespace(pid=PGID))
    monkeypatch.setattr(containment, "_worker_command", lambda *a: ["fixture"])
    monkeypatch.setattr(containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(containment, "_terminate_posix_group", lambda *a: events.append("cleanup"))
    monkeypatch.setattr(containment, "_reap_controller", lambda *a: events.append("reaped"))
    monkeypatch.setattr(containment, "_tree_size", lambda *a: 0)
    codes = iter([None, 0])
    monkeypatch.setattr(containment, "_posix_exit_code", lambda *a: next(codes))
    def run(**limits):
        return containment._run_contained_process(
            ["fixture"], cwd=".", env={}, sandbox=Sandbox(),
            limits=containment.Limits(**{"wall_seconds": 3.0, "poll_seconds": 1.0, **limits}),
        )
    return SimpleNamespace(run=run, events=events, stderr=stderr)


def test_poll_retries_consume_idle_cadence_without_growing_cycles(
    poll_process, poll_clock, monkeypatch,
):
    codes = iter([None, None, 0])
    monkeypatch.setattr(containment, "_posix_exit_code", lambda *a: next(codes))
    attempts = []
    def observe(pid, *, deadline):
        attempts.append((poll_clock.now, deadline))
        poll_clock.now += 0.1
        if len(attempts) == 1:
            raise _known_churn()
        return 2, MIB
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    assert poll_process.run() == 0
    assert [started for started, _ in attempts] == pytest.approx([0.0, 0.15, 1.0])
    assert [deadline for _, deadline in attempts] == [1.0, 1.0, 2.0]
    assert poll_clock.now == pytest.approx(2.0)
    assert poll_clock.sleeps == pytest.approx([0.05, 0.75, 0.9])
    assert poll_process.events.count("cleanup") == 1


@pytest.mark.parametrize("row,limits,diagnostic", [
    ((4, 0), {"max_processes": 2}, "process-count limit exceeded"),
    ((1, 2 * MIB), {"max_memory_mb": 1}, "memory limit exceeded"),
])
def test_poll_complete_counters_after_churn_still_enforce_limits(
    poll_process, poll_clock, monkeypatch, row, limits, diagnostic,
):
    calls = [0]
    def observe(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise _known_churn()
        return row
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    assert poll_process.run(**limits) == 124
    assert diagnostic in poll_process.stderr.getvalue()
    assert poll_clock.sleeps == [0.05]


def test_poll_deadline_is_clamped_to_remaining_wall_budget(
    poll_process, poll_clock, monkeypatch,
):
    deadlines = []
    def observe(pid, *, deadline):
        deadlines.append(deadline)
        raise _known_churn()
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", observe)
    with pytest.raises(darwin.GroupObservationChanged):
        poll_process.run(wall_seconds=0.11)
    assert set(deadlines) == {0.11}
    assert poll_clock.now == pytest.approx(0.11)
    assert poll_process.events.count("cleanup") == 1


def test_slow_temp_scan_cannot_consume_the_due_darwin_census_window(
    poll_process, poll_clock, monkeypatch,
):
    codes = iter([None, None, 0])
    monkeypatch.setattr(containment, "_posix_exit_code", lambda *a: next(codes))
    events = []
    def scan(sandbox):
        events.append(("temp", poll_clock.now))
        poll_clock.now += 0.4
        return 0
    def census(pid, *, deadline):
        events.append(("census", poll_clock.now, deadline))
        return 2, MIB
    monkeypatch.setattr(containment, "_tree_size", scan)
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", census)
    assert poll_process.run(poll_seconds=0.25) == 0
    assert [event[0] for event in events] == ["census", "temp", "census"]
    assert events[0] == ("census", 0.0, 0.25)
    assert events[2][1:] == pytest.approx((0.4, 0.65))
    assert poll_clock.sleeps == pytest.approx([0.0, 0.25])
    assert poll_clock.now == pytest.approx(0.65)


@pytest.mark.parametrize("later_row,limits,diagnostic", [
    ((4, MIB), {"max_processes": 2}, "process-count limit exceeded"),
    ((2, 2 * MIB), {"max_memory_mb": 1}, "memory limit exceeded"),
])
def test_slow_temp_overrun_requires_fresh_counters_on_next_cycle(
    poll_process, poll_clock, monkeypatch, later_row, limits, diagnostic,
):
    codes = iter([None, None])
    monkeypatch.setattr(containment, "_posix_exit_code", lambda *a: next(codes))
    calls = []
    def scan(sandbox):
        poll_clock.now += 0.4
        return 0
    def census(pid, *, deadline):
        calls.append((poll_clock.now, deadline))
        if len(calls) == 1:
            raise _known_churn()
        return (2, MIB) if len(calls) == 2 else later_row
    monkeypatch.setattr(containment, "_tree_size", scan)
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", census)
    assert poll_process.run(poll_seconds=0.25, **limits) == 124
    assert diagnostic in poll_process.stderr.getvalue()
    assert [started for started, _ in calls] == pytest.approx([0.0, 0.05, 0.45])
    assert [deadline for _, deadline in calls] == pytest.approx([0.25, 0.25, 0.70])
    assert poll_clock.sleeps == [0.05, 0.0]


@pytest.mark.parametrize("wall", [0.1, 0.3])
def test_slow_temp_scan_does_not_renew_wall_budget_or_hide_elapsed_exit(
    poll_process, poll_clock, monkeypatch, wall,
):
    deadlines = []
    def scan(sandbox):
        poll_clock.now += 0.4
        return 0
    def census(pid, *, deadline):
        deadlines.append(deadline)
        return 2, MIB
    monkeypatch.setattr(containment, "_tree_size", scan)
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", census)
    assert poll_process.run(wall_seconds=wall, poll_seconds=0.25) == 124
    assert "wall-clock limit exceeded" in poll_process.stderr.getvalue()
    assert deadlines == [min(0.25, wall)]
    assert poll_clock.now == 0.4
    assert poll_clock.sleeps == [0.0]


def test_darwin_slow_temp_scan_still_enforces_storage_limit(
    poll_process, poll_clock, monkeypatch,
):
    events = []
    def scan(sandbox):
        events.append("temp")
        poll_clock.now += 0.4
        return 2 * MIB
    def census(pid, *, deadline):
        events.append("census")
        return 2, MIB
    monkeypatch.setattr(containment, "_tree_size", scan)
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", census)
    assert poll_process.run(poll_seconds=0.25, max_temp_mb=1) == 124
    assert "temporary-storage limit exceeded (1 MiB)" in poll_process.stderr.getvalue()
    assert events == ["census", "temp"]
    assert poll_clock.sleeps == []


def test_darwin_temp_and_resource_checks_keep_their_original_due_times(
    poll_process, poll_clock, monkeypatch,
):
    codes = iter([None, None, None, 0])
    monkeypatch.setattr(containment, "_posix_exit_code", lambda *a: next(codes))
    temp_times, resource_times = [], []
    def scan(sandbox):
        temp_times.append(poll_clock.now)
        poll_clock.now += 0.4
        return 0
    def census(pid, *, deadline):
        resource_times.append(poll_clock.now)
        return 2, MIB
    monkeypatch.setattr(containment, "_tree_size", scan)
    monkeypatch.setattr(posix_observation, "_darwin_group_usage", census)
    assert poll_process.run() == 0
    assert resource_times == [0.0, 1.0, 2.0]
    assert temp_times == [0.0, 2.0]
    assert poll_clock.sleeps == pytest.approx([0.6, 1.0, 0.6])
    assert poll_clock.now == 3.0


@pytest.mark.parametrize("platform", ["linux", "freebsd", "win32"])
def test_non_darwin_temp_order_and_slow_scan_sleep_are_unchanged(
    poll_process, poll_clock, monkeypatch, platform,
):
    monkeypatch.setattr(containment, "sys",
                        SimpleNamespace(platform=platform, stderr=poll_process.stderr))
    events = []
    def scan(sandbox):
        events.append("temp")
        poll_clock.now += 0.4
        return 0
    def census(pid):
        events.append("census")
        return 2, MIB
    monkeypatch.setattr(containment, "_tree_size", scan)
    monkeypatch.setattr(containment, "_posix_group_usage", census)
    assert poll_process.run(poll_seconds=0.25) == 0
    assert events == ["temp", "census"]
    assert poll_clock.sleeps == [0.25]
    assert poll_clock.now == 0.65


@pytest.mark.parametrize("platform", ["linux", "freebsd", "win32"])
def test_non_darwin_poll_retains_its_existing_sleep_behavior(
    poll_process, poll_clock, monkeypatch, platform,
):
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform=platform, stderr=sys.stderr))
    monkeypatch.setattr(containment, "_darwin_poll_group_usage",
                        lambda *a, **k: pytest.fail("Darwin poll on another platform"))
    def observe(pid):
        poll_clock.now += 0.25
        return 2, MIB
    monkeypatch.setattr(containment, "_posix_group_usage", observe)
    assert poll_process.run() == 0
    assert poll_clock.sleeps == [1.0]
    assert poll_clock.now == 1.25


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual macOS libproc/RSS")
def test_native_owned_group_dirty_resident_bytes_and_complete_retirement(monkeypatch):
    source = (
        "import sys\n"
        "memory=bytearray(32*1024*1024)\n"
        "for offset in range(0,len(memory),4096): memory[offset]=1\n"
        "sys.stdout.write('R'); sys.stdout.flush()\n"
        "sys.stdin.read(1)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", source], start_new_session=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.read(1) == b"R"
        assert os.getpgid(child.pid) == child.pid
        with monkeypatch.context() as no_spawn:
            no_spawn.setattr(subprocess, "Popen",
                             lambda *a, **k: pytest.fail("native sampler spawned a process"))
            count, resident = darwin.group_usage(child.pid)
            assert darwin.group_usage(child.pid, live_only=True)[0] == 1
        assert count == 1 and resident >= 32 * MIB
        witness = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(child.pid)],
            capture_output=True, text=True, check=True, timeout=5,
        )
        assert abs(resident - int(witness.stdout.strip()) * 1024) <= 8 * MIB
    finally:
        child.communicate(input=b"x", timeout=10)
    assert child.returncode == 0
    assert darwin.group_usage(child.pid) == (0, 0)
    assert darwin.group_usage(child.pid, live_only=True) == (0, 0)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual macOS unreaped members")
def test_native_unreaped_zombie_counts_for_budget_but_not_live_cleanup():
    child = subprocess.Popen(
        [sys.executable, "-B", "-c",
         "import sys; sys.stdout.write('R'); sys.stdout.flush(); sys.stdin.read(1)"],
        start_new_session=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.read(1) == b"R"
        child.stdin.write(b"x")
        child.stdin.flush()
        native = darwin._Native()
        deadline = time.monotonic() + 5
        while True:
            try:
                terminal = native.member(child.pid, child.pid).status == 5
            except darwin._CensusChanged:
                terminal = False
            if terminal:
                break
            assert time.monotonic() < deadline, "Owned child did not reach an unreaped terminal state"
            time.sleep(0.01)
        assert darwin.group_usage(child.pid) == (1, 0)
        assert darwin.group_usage(child.pid, live_only=True) == (0, 0)
    finally:
        child.communicate(timeout=10)
    assert child.returncode == 0
    assert darwin.group_usage(child.pid) == (0, 0)
