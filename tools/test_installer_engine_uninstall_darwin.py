"""Darwin inspector contracts; native qualification is explicitly Darwin-only."""
from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / "libs" / "installer-engine"
          / "installer_engine_uninstall_darwin.py")
SPEC = importlib.util.spec_from_file_location("uninstall_darwin_test_subject", SOURCE)
assert SPEC is not None and SPEC.loader is not None
darwin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(darwin)


def argument_buffer(argv, *, environment=b"PRIVATE_SENTINEL=not-an-argument\0",
                    word_size=8, path=b"/opt/example/python"):
    path = path + b"\0"
    path += b"\0" * (-len(path) % word_size)
    return (struct.pack("=i", len(argv)) + path
            + b"\0".join(arg.encode("utf-8", "surrogateescape") for arg in argv)
            + b"\0" + environment)


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class Kernel:
    """Writes through actual ctypes buffers and validates native call boundaries."""

    def __init__(self):
        self.self_pid = os.getpid()
        self.pids = [self.self_pid]
        self.snapshots = None
        self.list_calls = []
        self.info_calls = {}
        self.info_hook = None
        self.bsd_calls = []
        self.bsd_error = {}
        self.bsd_hook = None
        self.path_calls = 0
        self.path_hook = None
        self.arg_error = None
        self.arg_data = argument_buffer(["python", "a b", "it's", "", "--flag"])
        self.argmax = 4096
        self.arg_size = None
        self.query_size = None
        self.argmax_size = 4
        self.list_size = None
        self.info_size = None
        self.libproc = SimpleNamespace(
            proc_listpids=Function(self.listpids),
            proc_pidinfo=Function(self.pidinfo),
            proc_pidpath=Function(self.pidpath),
        )
        self.system = SimpleNamespace(sysctl=Function(self.sysctl))

    def load(self, path, *, use_errno):
        assert use_errno is True
        assert path in ("/usr/lib/libproc.dylib", "/usr/lib/libSystem.B.dylib")
        return self.libproc if path.endswith("libproc.dylib") else self.system

    def listpids(self, kind, uid, buffer, size):
        assert kind in (4, 5) and uid == 1000
        assert size == ctypes.sizeof(buffer)
        self.list_calls.append((kind, size))
        pids = self.pids
        if self.snapshots is not None:
            pids = self.snapshots[min((len(self.list_calls) - 1) // 2,
                                      len(self.snapshots) - 1)]
        if self.list_size is not None:
            return self.list_size
        values = pids[:size // 4]
        for index, value in enumerate(values):
            buffer[index] = value
        return len(values) * 4

    def pidinfo(self, pid, flavor, arg, pointer, size):
        if flavor in (3, 13):
            structure = darwin._BSDInfo if flavor == 3 else darwin._ShortBSDInfo
            assert arg == 0 and size == ctypes.sizeof(structure)
            self.bsd_calls.append((pid, flavor))
            if flavor in self.bsd_error:
                ctypes.set_errno(self.bsd_error[flavor])
                return 0
            data = structure()
            data.pid = pid
            data.uid = data.ruid = data.svuid = 1000
            data.flags, data.status = 16, 2
            if self.bsd_hook:
                result = self.bsd_hook(pid, flavor, data)
                if result is not None:
                    return result
            ctypes.memmove(pointer, ctypes.byref(data), size)
            return size
        assert flavor == 18 and arg == 0 and size == 192
        self.info_calls[pid] = self.info_calls.get(pid, 0) + 1
        data = darwin._Info()
        data.bsd.pid = pid
        data.bsd.ppid = 1
        data.bsd.uid = data.bsd.ruid = data.bsd.svuid = 1000
        data.bsd.start_sec = 10
        data.bsd.status = 2
        data.bsd.flags = 0x10
        data.unique.uniqueid = pid
        data.unique.idversion = 1
        data.unique.uuid[0] = 1
        if self.info_hook:
            result = self.info_hook(pid, self.info_calls[pid], data)
            if result is not None:
                return result
        ctypes.memmove(pointer, ctypes.byref(data), size)
        return self.info_size if self.info_size is not None else size

    def pidpath(self, pid, buffer, size):
        assert size == 4096
        self.path_calls += 1
        if self.path_hook:
            return self.path_hook(pid, self.path_calls, buffer)
        path = b"/opt/example/python"
        ctypes.memmove(buffer, path + b"\0", len(path) + 1)
        return len(path)

    def sysctl(self, keys, count, buffer, size_pointer, new, new_size):
        assert new is None and new_size == 0
        size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_size_t)).contents
        if tuple(keys[:count]) == (1, 8):
            assert size.value == 4
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value = self.argmax
            size.value = self.argmax_size
            return 0
        assert tuple(keys[:2]) == (1, 49) and count == 3
        if self.arg_error is not None:
            ctypes.set_errno(self.arg_error)
            return -1
        if buffer is None:
            assert size.value == 0
            required = (len(self.arg_data) + 3) // 4 * 4
            size.value = required if self.query_size is None else self.query_size
            return 0
        assert size.value == self.argmax + 4
        assert len(self.arg_data) <= size.value
        ctypes.memmove(buffer, self.arg_data, len(self.arg_data))
        size.value = len(self.arg_data) if self.arg_size is None else self.arg_size
        return 0


@pytest.fixture
def kernel(monkeypatch):
    fake = Kernel()
    monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(darwin, "os", SimpleNamespace(
        getpid=lambda: fake.self_pid, getuid=lambda: 1000, geteuid=lambda: 1000,
    ))
    monkeypatch.setattr(darwin.ctypes, "CDLL", fake.load)
    return fake


def test_abi_layout():
    assert ctypes.sizeof(darwin._BSDInfo) == 136
    assert ctypes.sizeof(darwin._UniqueInfo) == 56
    assert ctypes.sizeof(darwin._Info) == 192
    assert darwin._Info.unique.offset == 136
    assert darwin._BSDInfo.start_sec.offset == 120
    assert darwin._UniqueInfo.uniqueid.offset == 16
    assert darwin._UniqueInfo.idversion.offset == 32


@pytest.mark.parametrize("word_size", [4, 8])
@pytest.mark.parametrize("argv", [
    ["python", "space within", "apostrophe's", "", "", "last"],
    ["python", "", ""],
    ["python", "\udcff"],
    ["", "python", "", "last"],
    [""],
])
def test_exact_argument_boundaries_exclude_environment(argv, word_size):
    result = darwin._parse_procargs(argument_buffer(argv, word_size=word_size), word_size)
    assert result == argv
    assert "PRIVATE_SENTINEL" not in repr(result)


@pytest.mark.parametrize("word_size", [4, 8])
@pytest.mark.parametrize("path_size", range(1, 17))
def test_executable_alignment_does_not_consume_empty_argv0(word_size, path_size):
    argv = ["", "middle", "", ""]
    data = argument_buffer(argv, word_size=word_size, path=b"/" * path_size)
    assert darwin._parse_procargs(data, word_size) == argv


@pytest.mark.parametrize("data", [
    b"", b"\0" * 5,
    struct.pack("=i", 0) + b"/x\0x\0",
    struct.pack("=i", -1) + b"/x\0x\0",
    struct.pack("=i", darwin.MAX_ARGC + 1) + b"/x\0x\0",
    struct.pack("=i", 1) + b"no-terminator",
    struct.pack("=i", 1) + b"\0x\0",
    struct.pack("=i", 2) + b"/x\0x\0",
    struct.pack("=i", 1) + b"/x\0x",
    struct.pack("=i", 1) + b"/x\0\0",
])
def test_malformed_argument_buffers_refuse_without_content(data):
    with pytest.raises(RuntimeError, match="Darwin process inspection") as caught:
        darwin._parse_procargs(data, 8)
    assert "no-terminator" not in str(caught.value)


def test_argument_size_bound(monkeypatch):
    monkeypatch.setattr(darwin, "MAX_ARG_BYTES", 10)
    with pytest.raises(RuntimeError, match="size"):
        darwin._parse_procargs(argument_buffer(["python"]), 8)


def test_real_construction_and_inspection(kernel):
    rows = darwin.inspect_processes()
    assert rows == [{
        "pid": kernel.self_pid, "ppid": 1, "image": "/opt/example/python",
        "command": "python a b it's  --flag",
        "argv": ["python", "a b", "it's", "", "--flag"],
    }]
    assert len(kernel.list_calls) == 4
    assert kernel.libproc.proc_pidinfo.restype is ctypes.c_int
    assert len(kernel.system.sysctl.argtypes) == 6
    assert kernel.info_calls[kernel.self_pid] == 2


@pytest.mark.parametrize("failure", [OSError, AttributeError])
def test_missing_native_inspector_refuses(kernel, monkeypatch, failure):
    def missing(*args, **kwargs):
        raise failure("not exposed")
    monkeypatch.setattr(darwin.ctypes, "CDLL", missing)
    with pytest.raises(RuntimeError, match="inspector unavailable"):
        darwin.inspect_processes()


def test_missing_native_symbol_refuses(kernel):
    del kernel.libproc.proc_pidpath
    with pytest.raises(RuntimeError, match="inspector unavailable"):
        darwin.inspect_processes()


def test_non_darwin_refuses(monkeypatch):
    monkeypatch.setattr(darwin, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(RuntimeError, match="64-bit macOS"):
        darwin.inspect_processes()


def test_unverified_abi_refuses(kernel, monkeypatch):
    original = ctypes.sizeof
    monkeypatch.setattr(ctypes, "sizeof",
                        lambda value: 4 if value is ctypes.c_void_p else original(value))
    with pytest.raises(RuntimeError, match="64-bit macOS"):
        darwin.inspect_processes()


@pytest.mark.parametrize("argmax", [0, -1, darwin.MAX_ARG_BYTES + 1])
def test_kernel_argument_bound_refuses(kernel, argmax):
    kernel.argmax = argmax
    with pytest.raises(RuntimeError, match="argument bound"):
        darwin.inspect_processes()


@pytest.mark.parametrize("size", [0, 3, 5])
def test_partial_argmax_refuses(kernel, size):
    kernel.argmax_size = size
    with pytest.raises(RuntimeError, match="partial sysctl"):
        darwin.inspect_processes()


@pytest.mark.parametrize("size", [0, 4097 + 4])
def test_invalid_argument_size_refuses(kernel, size):
    kernel.arg_size = size
    with pytest.raises(RuntimeError, match="partial sysctl"):
        darwin.inspect_processes()


@pytest.mark.parametrize("size", [0, 5, 4096 + 5])
def test_invalid_preflight_argument_size_refuses(kernel, size):
    kernel.query_size = size
    with pytest.raises(RuntimeError, match="native argument size"):
        darwin.inspect_processes()


def test_changed_preflight_argument_size_refuses(kernel):
    kernel.query_size = len(kernel.arg_data) - 1
    with pytest.raises(RuntimeError, match="argument size changed"):
        darwin.inspect_processes()


@pytest.mark.parametrize("word_size", [0, 16])
def test_invalid_argument_abi_refuses(word_size):
    with pytest.raises(RuntimeError, match="argument ABI"):
        darwin._parse_procargs(argument_buffer(["python"]), word_size)


def test_nonzero_alignment_padding_refuses():
    data = argument_buffer(["python"])
    data = data[:25] + b"x" + data[26:]
    with pytest.raises(RuntimeError, match="alignment"):
        darwin._parse_procargs(data, 8)


@pytest.mark.parametrize("size", [0, -1, 1, 1028])
def test_invalid_inventory_refuses(kernel, size):
    kernel.list_size = size
    with pytest.raises(RuntimeError, match="inventory"):
        darwin.inspect_processes()


@pytest.mark.parametrize("pids", [[], [0], [-1], [1, 1]])
def test_empty_invalid_duplicate_inventory_refuses(kernel, pids):
    kernel.pids = pids
    with pytest.raises(RuntimeError, match="inventory"):
        darwin.inspect_processes()


def test_inventory_must_include_inspector(kernel):
    kernel.pids = [1]
    with pytest.raises(RuntimeError, match="incomplete"):
        darwin.inspect_processes()


def test_effective_and_real_uid_censuses_are_both_required(kernel):
    first, second = [kernel.self_pid], [kernel.self_pid, 90001]
    original = kernel.libproc.proc_listpids.function
    def filtered(kind, uid, buffer, size):
        kernel.pids = first if kind == 4 else second
        return original(kind, uid, buffer, size)
    kernel.libproc.proc_listpids.function = filtered
    assert darwin._Native().inventory() == set(second)


def test_inventory_grows_without_truncating(kernel):
    kernel.pids = [kernel.self_pid, *range(100000, 100300)]
    assert darwin._Native().inventory() == set(kernel.pids)
    assert kernel.list_calls == [(4, 1024), (4, 2048), (5, 1024), (5, 2048)]


def test_inventory_cap_refuses_full_buffer(kernel, monkeypatch):
    monkeypatch.setattr(darwin, "MAX_PIDS", 256)
    kernel.pids = list(range(1, 257))
    with pytest.raises(RuntimeError, match="bound exceeded"):
        darwin.inspect_processes()


def test_inventory_churn_is_bounded(kernel):
    first, second = [kernel.self_pid], [kernel.self_pid, 90001]
    kernel.snapshots = [first, second] * darwin.MAX_CENSUS_PASSES
    with pytest.raises(RuntimeError, match="churn exceeded"):
        darwin.inspect_processes()
    assert len(kernel.list_calls) == 12


def test_inventory_transient_churn_restarts_census(kernel):
    first, second = [kernel.self_pid], [kernel.self_pid, 90001]
    kernel.snapshots = [first, second, second, second]
    assert {row["pid"] for row in darwin.inspect_processes()} == set(second)


@pytest.mark.parametrize("field,value", [
    ("pid", 0), ("ppid", 0xffffffff), ("start_sec", 0),
    ("start_usec", 1000000), ("status", 0),
    ("uid", 1001), ("ruid", 1001), ("svuid", 1001),
])
def test_unverified_identity_or_ownership_refuses(kernel, field, value):
    def change(pid, call, data):
        setattr(data.bsd, field, value)
    kernel.info_hook = change
    with pytest.raises(RuntimeError, match="identity|ownership"):
        darwin.inspect_processes()


@pytest.mark.parametrize("size", [1, 136, 191, 193])
def test_partial_native_identity_refuses(kernel, size):
    kernel.info_size = size
    with pytest.raises(RuntimeError, match="partial identity"):
        darwin.inspect_processes()


@pytest.mark.parametrize("error", [errno.EPERM, errno.EACCES, errno.EIO, 0])
def test_uninspectable_current_user_candidate_refuses(kernel, error):
    def inaccessible(pid, call, data):
        ctypes.set_errno(error)
        return 0
    kernel.info_hook = inaccessible
    with pytest.raises(RuntimeError, match="native read refused"):
        darwin.inspect_processes()


@pytest.mark.parametrize("mode", ["gone", "zombie", "invalid-then-gone"])
def test_only_evidenced_disappearance_or_terminal_candidate_skips(kernel, mode):
    kernel.pids.append(90001)
    def terminal(pid, call, data):
        if pid != 90001:
            return None
        if mode == "zombie":
            data.bsd.status = 5
        else:
            ctypes.set_errno(errno.ESRCH)
            return 0
    kernel.info_hook = terminal
    if mode == "invalid-then-gone":
        # Fail after the first identity read of the candidate, not the inspector.
        def image(pid, call, buffer):
            if pid == 90001:
                ctypes.set_errno(errno.EINVAL)
                return 0
            path = b"/opt/example/python"
            ctypes.memmove(buffer, path + b"\0", len(path) + 1)
            return len(path)
        kernel.path_hook = image
        def after_read(pid, call, data):
            if pid == 90001 and call > 1:
                ctypes.set_errno(errno.ESRCH)
                return 0
        kernel.info_hook = after_read
    assert [row["pid"] for row in darwin.inspect_processes()] == [kernel.self_pid]


@pytest.mark.parametrize("error", [errno.EPERM, errno.EACCES, errno.EIO, errno.EINVAL])
def test_argument_failure_never_silently_disappears(kernel, error):
    kernel.arg_error = error
    with pytest.raises(RuntimeError, match="native read refused"):
        darwin.inspect_processes()


@pytest.mark.parametrize("failure", [errno.EPERM, errno.EACCES, errno.EIO, errno.ENOMEM, 0])
def test_non_einval_later_disappearance_is_diagnostic_not_skip(kernel, failure):
    kernel.arg_error = failure
    def gone(pid, call, data):
        if call == 2:
            ctypes.set_errno(errno.ESRCH)
            return 0
    kernel.info_hook = gone
    with pytest.raises(RuntimeError, match="native read refused") as caught:
        darwin.inspect_processes()
    message = str(caught.value)
    assert f"errno={failure} " in message
    assert "confirmation=gone " in message
    assert f"confirmation_errno={errno.ESRCH} " in message
    assert kernel.info_calls[kernel.self_pid] == 2


@pytest.mark.parametrize("failure", [errno.EPERM, errno.EIO])
def test_non_einval_terminal_confirmation_does_not_admit_skip(kernel, failure):
    kernel.arg_error = failure
    def terminal(pid, call, data):
        if call == 2:
            data.bsd.status = 5
    kernel.info_hook = terminal
    with pytest.raises(RuntimeError, match="confirmation=zombie "):
        darwin.inspect_processes()


@pytest.mark.parametrize("phase", [
    "identity-before", "image-before", "argument-size", "argument-read",
    "image-after", "identity-after",
])
def test_native_failure_diagnostic_names_exact_phase_and_numeric_errno(kernel, phase):
    if phase.startswith("identity"):
        fail_call = 1 if phase == "identity-before" else 2
        def identity(pid, call, data):
            if call == fail_call:
                ctypes.set_errno(errno.EIO)
                return 0
        kernel.info_hook = identity
    elif phase.startswith("image"):
        fail_call = 1 if phase == "image-before" else 2
        def image(pid, call, buffer):
            if call == fail_call:
                ctypes.set_errno(errno.EIO)
                return 0
            value = b"/opt/example/python"
            ctypes.memmove(buffer, value + b"\0", len(value) + 1)
            return len(value)
        kernel.path_hook = image
    elif phase == "argument-size":
        kernel.arg_error = errno.EIO
    else:
        original = kernel.system.sysctl.function
        def argument_read(keys, count, buffer, size_pointer, new, new_size):
            if count == 3 and buffer is not None:
                ctypes.set_errno(errno.EIO)
                return -1
            return original(keys, count, buffer, size_pointer, new, new_size)
        kernel.system.sysctl.function = argument_read
    with pytest.raises(RuntimeError, match="native read refused") as caught:
        darwin.inspect_processes()
    message = str(caught.value)
    assert f"phase={phase} " in message
    assert f"errno={errno.EIO} " in message
    assert "confirmation=live confirmation_errno=0 confirmation_status=2" in message
    expected_identity = -1 if phase == "identity-before" else 1
    assert f"confirmation_identity={expected_identity} " in message
    expected_calls = 3 if phase == "identity-after" else 2
    assert kernel.info_calls[kernel.self_pid] == expected_calls


@pytest.mark.parametrize("confirmation", ["changed", "ownership-refused", "partial"])
def test_independent_confirmation_failure_still_refuses_original_error(kernel, confirmation):
    kernel.arg_error = errno.EIO
    def observe(pid, call, data):
        if call == 2:
            if confirmation == "changed":
                data.bsd.start_sec += 1
            elif confirmation == "ownership-refused":
                data.bsd.uid += 1
            else:
                return 1
    kernel.info_hook = observe
    with pytest.raises(RuntimeError, match="native read refused") as caught:
        darwin.inspect_processes()
    message = str(caught.value)
    assert f"errno={errno.EIO} " in message
    expected = "changed" if confirmation == "changed" else "unverified"
    assert f"confirmation={expected} " in message
    assert kernel.info_calls[kernel.self_pid] == 2


def test_native_read_diagnostic_never_formats_exception_payload(kernel):
    def unreadable(*args):
        raise OSError(errno.EIO, "PRIVATE_ARGUMENT_AND_ENVIRONMENT")
    # Construct with the genuine deterministic native seam before introducing
    # the argument failure, so this exercises the production row error path.
    native = darwin._Native()
    kernel.system.sysctl.function = unreadable
    with pytest.raises(RuntimeError, match="native read refused") as caught:
        darwin._row(native, kernel.self_pid)
    assert "PRIVATE_ARGUMENT_AND_ENVIRONMENT" not in str(caught.value)
    assert "argument-read" in str(caught.value)


def test_failure_evidence_binds_exact_pid_and_preserves_refusal(kernel):
    kernel.arg_error = errno.EPERM
    with pytest.raises(darwin._ReadRefused) as caught:
        darwin.inspect_processes()
    error = caught.value
    assert error.pid == kernel.self_pid
    assert error.phase == "argument-size"
    assert error.native_errno == errno.EPERM
    assert error.evidence["uid_inventory_member"] == 1
    assert error.evidence["ruid_inventory_member"] == 1
    assert error.evidence["bsd3_valid"] == error.evidence["bsd13_valid"] == 1
    assert kernel.bsd_calls == [(kernel.self_pid, 3), (kernel.self_pid, 13)]


@pytest.mark.parametrize("uid_member,ruid_member", [(False, False), (False, True),
                                                   (True, False), (True, True)])
def test_failure_membership_is_diagnostic_and_never_omits_row(kernel, uid_member, ruid_member):
    target = 90001
    native = darwin._Native()
    kernel.pids = [kernel.self_pid]
    original = kernel.libproc.proc_listpids.function
    def filtered(kind, uid, buffer, size):
        member = uid_member if kind == 4 else ruid_member
        kernel.pids = [kernel.self_pid, *([target] if member else [])]
        return original(kind, uid, buffer, size)
    kernel.libproc.proc_listpids.function = filtered
    def inaccessible(pid, call, data):
        ctypes.set_errno(errno.EPERM)
        return 0
    kernel.info_hook = inaccessible
    kernel.bsd_error = {3: errno.EPERM, 13: errno.ESRCH}
    with pytest.raises(darwin._ReadRefused) as caught:
        darwin._row(native, target)
    error = caught.value
    assert error.pid == target
    assert error.phase == "identity-before"
    assert error.evidence["uid_inventory_member"] == int(uid_member)
    assert error.evidence["ruid_inventory_member"] == int(ruid_member)
    assert error.evidence["bsd3_errno"] == errno.EPERM
    assert error.evidence["bsd13_errno"] == errno.ESRCH
    assert error.evidence["bsd3_valid"] == error.evidence["bsd13_valid"] == 0
    assert "bsd3_uid" not in error.evidence
    assert "bsd13_uid" not in error.evidence
    assert len(kernel.list_calls) == 2
    assert kernel.info_calls[target] == 2


@pytest.mark.parametrize("mode", ["empty", "denied", "duplicate", "partial", "full"])
def test_invalid_fresh_inventory_is_unknown_not_disappearance(kernel, mode, monkeypatch):
    native = darwin._Native()
    if mode == "empty":
        kernel.pids = []
    elif mode == "denied":
        def denied(*args):
            ctypes.set_errno(errno.EPERM)
            return 0
        kernel.libproc.proc_listpids.function = denied
    elif mode == "duplicate":
        kernel.pids = [kernel.self_pid, kernel.self_pid]
    elif mode == "partial":
        kernel.list_size = 3
    else:
        monkeypatch.setattr(darwin, "MAX_PIDS", 256)
        kernel.pids = list(range(1, 257))
    evidence = darwin._failure_evidence(native, 90001)
    assert evidence["uid_inventory_member"] == -1
    assert evidence["ruid_inventory_member"] == -1
    if mode == "denied":
        assert evidence["uid_inventory_errno"] == errno.EPERM
        assert evidence["ruid_inventory_errno"] == errno.EPERM


def test_fresh_inventory_proof_requires_complete_buffer_growth(kernel):
    native = darwin._Native()
    kernel.pids = [kernel.self_pid, *range(100000, 100300)]
    evidence = darwin._failure_evidence(native, 90001)
    assert evidence["uid_inventory_member"] == evidence["ruid_inventory_member"] == 0
    assert evidence["uid_inventory_self"] == evidence["ruid_inventory_self"] == 1
    assert kernel.list_calls == [(4, 1024), (4, 2048), (5, 1024), (5, 2048)]


@pytest.mark.parametrize("mode", ["wrong-pid", "partial", "denied"])
def test_bsd_diagnostics_do_not_decode_unverified_record(kernel, mode):
    native = darwin._Native()
    def unverified(pid, flavor, data):
        if mode == "wrong-pid":
            data.pid = pid + 1
        elif mode == "partial":
            return 1
        else:
            ctypes.set_errno(errno.EPERM)
            return 0
    kernel.bsd_hook = unverified
    evidence = darwin._failure_evidence(native, 90001)
    assert evidence["bsd3_valid"] == evidence["bsd13_valid"] == 0
    assert "bsd3_uid" not in evidence
    assert "bsd13_uid" not in evidence
    assert "bsd13_ppid" not in evidence
    assert "bsd13_pgid" not in evidence
    assert "bsd13_comm_is_ps" not in evidence


def test_bsd_diagnostics_preserve_uid_transition_without_names_or_argv(kernel):
    native = darwin._Native()
    def credentials(pid, flavor, data):
        data.uid = data.ruid = data.svuid = 0
        data.comm = b"PRIVATE_NAME"
    kernel.bsd_hook = credentials
    evidence = darwin._failure_evidence(native, 90001)
    assert evidence["bsd3_uid"] == evidence["bsd13_uid"] == 0
    assert evidence["bsd3_ruid"] == evidence["bsd13_ruid"] == 0
    assert evidence["bsd3_svuid"] == evidence["bsd13_svuid"] == 0
    assert all(type(value) is int for value in evidence.values())
    assert "PRIVATE_NAME" not in json.dumps(evidence)
    assert kernel.info_calls == {}
    assert kernel.path_calls == 0


@pytest.mark.parametrize("comm,expected", [
    (b"ps", 1), (b"", 0), (b"psx", 0), (b"xps", 0), (b"PS", 0),
    (b"ps -eo", 0), (b"PRIVATE_NAME", 0), (b"\xff", 0),
])
def test_bsd13_sampler_metadata_is_exact_numeric_and_privacy_allowlisted(kernel, comm, expected):
    native = darwin._Native()
    def credentials(pid, flavor, data):
        data.ppid = 12345
        data.pgid = 23456
        data.comm = comm
    kernel.bsd_hook = credentials
    evidence = darwin._failure_evidence(native, 90001)
    assert evidence["bsd13_ppid"] == 12345
    assert evidence["bsd13_pgid"] == 23456
    assert evidence["bsd13_comm_is_ps"] == expected
    permitted = {"census_uid", "caller_ruid", "caller_uid"}
    for prefix in ("uid_inventory", "ruid_inventory"):
        permitted.update(prefix + "_" + key for key in ("member", "self", "errno"))
    for prefix in ("bsd3", "bsd13"):
        permitted.update(prefix + "_" + key for key in (
            "bytes", "errno", "valid", "uid", "ruid", "svuid", "flags", "status",
        ))
    permitted.update({"bsd13_ppid", "bsd13_pgid", "bsd13_comm_is_ps"})
    assert set(evidence) == permitted
    assert all(type(value) is int for value in evidence.values())
    assert "PRIVATE_NAME" not in json.dumps(evidence)
    assert "bsd3_ppid" not in evidence


def test_privileged_ps_metadata_does_not_change_guard_or_failure_phase(kernel):
    target = 90001
    native = darwin._Native()
    def denied(pid, call, data):
        ctypes.set_errno(errno.EPERM)
        return 0
    def credentials(pid, flavor, data):
        data.uid, data.ruid, data.svuid = 0, 1000, 0
        data.ppid, data.pgid = 12345, 23456
        data.comm = b"ps"
    kernel.info_hook = denied
    kernel.bsd_hook = credentials
    with pytest.raises(darwin._ReadRefused) as caught:
        darwin._row(native, target)
    error = caught.value
    assert error.pid == target
    assert error.phase == "identity-before"
    assert error.native_errno == errno.EPERM
    assert "phase=identity-before " in str(error)
    assert error.evidence["bsd13_uid"] == error.evidence["bsd13_svuid"] == 0
    assert error.evidence["bsd13_ruid"] == 1000
    assert error.evidence["bsd13_comm_is_ps"] == 1
    assert error.evidence["bsd13_ppid"] == 12345
    assert error.evidence["bsd13_pgid"] == 23456
    assert kernel.info_calls[target] == 2
    assert kernel.path_calls == 0


def test_einval_permission_denial_confirmation_is_not_gone(kernel):
    kernel.arg_error = errno.EINVAL
    def inaccessible(pid, call, data):
        if call == 2:
            ctypes.set_errno(errno.EPERM)
            return 0
    kernel.info_hook = inaccessible
    with pytest.raises(RuntimeError, match="native read refused") as caught:
        darwin.inspect_processes()
    assert "confirmation=unverified " in str(caught.value)
    assert f"confirmation_errno={errno.EPERM} " in str(caught.value)
    assert kernel.info_calls[kernel.self_pid] == 2


def test_process_observed_terminal_after_arguments_can_skip(kernel):
    kernel.pids.append(90001)
    def terminal(pid, call, data):
        if pid == 90001 and call == 2:
            data.bsd.status = 5
    kernel.info_hook = terminal
    assert [row["pid"] for row in darwin.inspect_processes()] == [kernel.self_pid]


@pytest.mark.parametrize("field", [
    "start_sec", "start_usec", "pid", "uid", "ppid", "flags", "uuid", "uniqueid", "idversion",
])
def test_reused_pid_birth_ownership_exec_incarnation_refuses(kernel, field):
    def changed(pid, call, data):
        if call == 2:
            if field == "uuid":
                data.unique.uuid[0] = 2
            elif field == "flags":
                data.bsd.flags = 0
            elif field in ("uniqueid", "idversion"):
                setattr(data.unique, field, getattr(data.unique, field) + 1)
            else:
                setattr(data.bsd, field, getattr(data.bsd, field) + 1)
    kernel.info_hook = changed
    with pytest.raises(RuntimeError, match="changed|identity|ownership"):
        darwin.inspect_processes()


@pytest.mark.parametrize("mode", ["changed", "unterminated", "partial", "relative"])
def test_changed_or_malformed_executable_refuses(kernel, mode):
    def path(pid, call, buffer):
        value = b"/opt/example/python" if call == 1 else b"/opt/example/other"
        if mode == "unterminated":
            ctypes.memmove(buffer, b"/" * 4096, 4096)
            return 4095
        if mode == "partial":
            ctypes.memmove(buffer, b"/x\0", 3)
            return 3
        if mode == "relative":
            value = b"relative"
        ctypes.memmove(buffer, value + b"\0", len(value) + 1)
        return len(value)
    kernel.path_hook = path
    with pytest.raises(RuntimeError, match="executable|incarnation"):
        darwin.inspect_processes()


def test_caller_ownership_change_refuses(kernel, monkeypatch):
    monkeypatch.setattr(darwin.os, "geteuid", lambda: 1001)
    with pytest.raises(RuntimeError, match="caller ownership"):
        darwin.inspect_processes()


def test_caller_ownership_change_during_inspection_refuses(kernel, monkeypatch):
    observations = iter([1000, 1001])
    monkeypatch.setattr(darwin.os, "geteuid", lambda: next(observations))
    with pytest.raises(RuntimeError, match="caller ownership changed"):
        darwin.inspect_processes()


def test_no_live_inspector_cannot_succeed(kernel):
    kernel.info_hook = lambda pid, call, data: setattr(data.bsd, "status", 5)
    with pytest.raises(RuntimeError, match="missing from live census"):
        darwin.inspect_processes()


@pytest.mark.parametrize("malformed", [False, True])
def test_native_environment_buffer_is_cleared(kernel, monkeypatch, malformed):
    buffers = []
    original = darwin.ctypes.create_string_buffer
    def capture(*args):
        result = original(*args)
        buffers.append(result)
        return result
    monkeypatch.setattr(darwin.ctypes, "create_string_buffer", capture)
    native = darwin._Native()
    if malformed:
        kernel.arg_data = b"\0" * 100
        with pytest.raises(RuntimeError, match="argument count"):
            native.argv(kernel.self_pid, 8)
    else:
        assert native.argv(kernel.self_pid, 8) == ["python", "a b", "it's", "", "--flag"]
    assert buffers[-1].raw == b"\0" * native.arg_bytes


class _ShortBSDInfo(ctypes.Structure):
    # Apple's proc_info.h: PROC_PIDT_SHORTBSDINFO (13), independent UID offsets.
    _fields_ = [
        *((name, ctypes.c_uint32) for name in ("pid", "ppid", "pgid", "status")),
        ("comm", ctypes.c_char * 16),
        *((name, ctypes.c_uint32) for name in (
            "flags", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved",
        )),
    ]


def _credential_metadata(bsd):
    return {name: int(getattr(bsd, name))
            for name in ("uid", "ruid", "svuid", "flags", "status")}


def _observe_ownership_refusal(native, observations):
    """Capture only the exact credential record the production guard will read."""
    original = native.pidinfo
    def observe(pid, flavor, arg, pointer, size):
        returned = original(pid, flavor, arg, pointer, size)
        if flavor == 18 and returned == size == ctypes.sizeof(darwin._Info):
            data = ctypes.cast(pointer, ctypes.POINTER(darwin._Info)).contents
            metadata = _credential_metadata(data.bsd)
            if (data.bsd.pid == pid and len(observations) < 1
                    and (data.bsd.uid, data.bsd.ruid, data.bsd.svuid) != (native.uid,) * 3):
                observations.append((native, pid, metadata, darwin._identity(data)))
        return returned
    native.pidinfo = observe


def _ownership_diagnostic(native, pid, guard_metadata, guard_identity):
    """Six bounded identity-only reads; never call argv/image or decode names."""
    result = {"expected_uid": native.uid, "guard": guard_metadata}
    for scope, target in (("candidate", pid), ("inspector", darwin.os.getpid())):
        rows = []
        for flavor, structure in ((18, darwin._Info), (3, darwin._BSDInfo),
                                  (13, _ShortBSDInfo)):
            data = structure()
            ctypes.set_errno(0)
            returned = native.pidinfo(target, flavor, 0, ctypes.byref(data),
                                      ctypes.sizeof(data))
            row = {"flavor": flavor, "returned_bytes": returned,
                   "errno": ctypes.get_errno()}
            bsd = data.bsd if flavor == 18 else data
            valid = returned == ctypes.sizeof(data) and bsd.pid == target
            row["valid"] = valid
            if valid:
                row.update(_credential_metadata(bsd))
                if scope == "candidate" and flavor == 18:
                    row["guard_identity_matches"] = darwin._identity(data) == guard_identity
            rows.append(row)
        result[scope] = rows
    return result


def test_diagnostic_short_bsd_abi():
    assert ctypes.sizeof(_ShortBSDInfo) == 64
    assert ctypes.sizeof(darwin._ShortBSDInfo) == 64
    assert _ShortBSDInfo.uid.offset == 36
    assert _ShortBSDInfo.ruid.offset == 44
    assert _ShortBSDInfo.svuid.offset == 52
    assert darwin._ShortBSDInfo.uid.offset == 36
    assert darwin._ShortBSDInfo.ruid.offset == 44
    assert darwin._ShortBSDInfo.svuid.offset == 52
    assert darwin._BSDInfo.uid.offset == 20
    assert darwin._BSDInfo.ruid.offset == 28
    assert darwin._BSDInfo.svuid.offset == 36


def test_diagnostic_preserves_mixed_privilege_refusal_and_exact_record(kernel):
    native = darwin._Native()
    observations = []
    _observe_ownership_refusal(native, observations)
    kernel.info_hook = lambda pid, call, data: setattr(data.bsd, "svuid", 0)
    with pytest.raises(RuntimeError, match="unverified current-user ownership"):
        native.info(kernel.self_pid)
    assert len(observations) == 1
    assert observations[0][2] == {
        "uid": 1000, "ruid": 1000, "svuid": 0, "flags": 16, "status": 2,
    }


@pytest.mark.parametrize("returned", [0, 1, 135, 191])
def test_diagnostic_does_not_decode_partial_identity(kernel, returned):
    native = darwin._Native()
    calls = []
    def partial(pid, flavor, arg, pointer, size):
        calls.append((pid, flavor, size))
        ctypes.set_errno(errno.EPERM)
        return returned
    native.pidinfo = partial
    result = _ownership_diagnostic(native, 90001, {}, ())
    assert len(calls) == 6
    assert all(not row["valid"] for scope in ("candidate", "inspector")
               for row in result[scope])
    assert "svuid" not in json.dumps(result)


def test_diagnostic_is_bounded_and_exposes_only_credential_allowlist(kernel):
    native = darwin._Native()
    calls = []
    def credentials(pid, flavor, arg, pointer, size):
        calls.append((pid, flavor))
        structure = {18: darwin._Info, 3: darwin._BSDInfo, 13: _ShortBSDInfo}[flavor]
        data = structure()
        bsd = data.bsd if flavor == 18 else data
        bsd.pid = pid
        bsd.uid = bsd.ruid = 1000
        bsd.svuid = 0 if pid == 90001 else 1000
        bsd.flags, bsd.status = 16, 2
        bsd.comm = b"PRIVATE_NAME"
        ctypes.memmove(pointer, ctypes.byref(data), size)
        return size
    native.pidinfo = credentials
    result = _ownership_diagnostic(native, 90001, {}, ())
    assert len(calls) == 6
    assert all(row["svuid"] == 0 for row in result["candidate"])
    assert all(row["svuid"] == 1000 for row in result["inspector"])
    permitted = {"flavor", "returned_bytes", "errno", "valid", "uid", "ruid",
                 "svuid", "flags", "status", "guard_identity_matches"}
    assert all(set(row) <= permitted for scope in ("candidate", "inspector")
               for row in result[scope])
    assert "PRIVATE_NAME" not in json.dumps(result)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual macOS libproc/sysctl")
def test_native_darwin_self_and_owned_live_process(monkeypatch):
    observations = []
    original_native = darwin._Native
    def diagnostic_native():
        native = original_native()
        _observe_ownership_refusal(native, observations)
        return native
    monkeypatch.setattr(darwin, "_Native", diagnostic_native)
    argv = [sys.executable, "-c",
            "import sys; sys.stdout.write('R'); sys.stdout.flush(); sys.stdin.read(1)",
            "space within", "apostrophe's", "", "--literal"]
    environment = {**os.environ, "PRIVATE_SENTINEL": "not-an-argument"}
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=environment)
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.read(1) == b"R"
        try:
            rows = {row["pid"]: row for row in darwin.inspect_processes()}
        except RuntimeError:
            if observations:
                report = _ownership_diagnostic(*observations[0])
                print("DARWIN_OWNERSHIP_DIAGNOSTIC " + json.dumps(report, sort_keys=True))
            raise
        assert os.getpid() in rows
        assert rows[child.pid]["ppid"] == os.getpid()
        assert rows[child.pid]["argv"][1:] == argv[1:]
        assert rows[child.pid]["image"].startswith("/")
        assert "PRIVATE_SENTINEL" not in repr(rows[child.pid])
    finally:
        child.communicate(input=b"x", timeout=10)
    assert child.returncode == 0
