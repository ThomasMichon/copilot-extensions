"""Portable native-ABI controls and an opt-in-by-platform macOS group witness."""
from __future__ import annotations

import ctypes
import errno
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
    with pytest.raises(ValueError, match="churn exceeded"):
        darwin.group_usage(PGID)
    assert len(kernel.inventory_calls) == darwin.MAX_PASSES


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
    with pytest.raises(ValueError, match="churn exceeded"):
        darwin.group_usage(PGID)


def test_inventory_churn_is_bounded(kernel):
    kernel.snapshots = [[41], [42]] * darwin.MAX_PASSES
    with pytest.raises(ValueError, match="churn exceeded"):
        darwin.group_usage(PGID)
    assert len(kernel.inventory_calls) == 2 * darwin.MAX_PASSES


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
