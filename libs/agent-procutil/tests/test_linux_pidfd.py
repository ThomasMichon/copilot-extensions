"""Optional Python wrappers never weaken pinned Linux process operations."""

import ctypes
import errno
import os
import sys
from types import SimpleNamespace

import pytest

from agent_procutil import _linux_pidfd as binding

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfds")


def test_python_signal_wrapper_is_preferred(monkeypatch):
    calls = []
    monkeypatch.setattr(
        binding.signal, "pidfd_send_signal", lambda *args: calls.append(args), raising=False,
    )

    def refuse(*args, **kwargs):
        pytest.fail("Python pidfd signaling must not call libc")

    monkeypatch.setattr(binding.ctypes, "CDLL", refuse)
    binding.send_pidfd_signal(42, 0)
    assert calls == [(42, 0)]


def test_real_libc_signal_query_without_python_wrappers(monkeypatch):
    monkeypatch.delattr(binding.os, "pidfd_open", raising=False)
    monkeypatch.delattr(binding.signal, "pidfd_send_signal", raising=False)
    descriptor = binding.open_pidfd(os.getpid())
    try:
        binding.send_pidfd_signal(descriptor, 0)
        assert not os.get_inheritable(descriptor)
    finally:
        os.close(descriptor)


def test_missing_native_signal_api_refuses_without_numeric_pid_fallback(monkeypatch):
    monkeypatch.delattr(binding.signal, "pidfd_send_signal", raising=False)
    monkeypatch.setattr(binding.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace())
    with pytest.raises(RuntimeError, match="libc pidfd signaling is unavailable"):
        binding.send_pidfd_signal(42, 0)


def test_libc_signal_error_keeps_kernel_errno_and_signature(monkeypatch):
    calls = []
    monkeypatch.delattr(binding.signal, "pidfd_send_signal", raising=False)

    def send(*args):
        calls.append(args)
        ctypes.set_errno(errno.EPERM)
        return -1

    monkeypatch.setattr(
        binding.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(pidfd_send_signal=send),
    )
    with pytest.raises(PermissionError) as raised:
        binding.send_pidfd_signal(42, 0)
    assert raised.value.errno == errno.EPERM
    assert calls == [(42, 0, None, 0)]
    assert send.argtypes == [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    assert send.restype == ctypes.c_int
