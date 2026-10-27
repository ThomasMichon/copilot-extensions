"""The real local facade with injected native roots and owned pipe readers."""

import io
from types import SimpleNamespace

import pytest

from agent_worktrees import push_timeout as pt


class Root:
    stdin = None
    returncode = None

    def __init__(self):
        self.stdout = io.BytesIO(b"a\r\nb\rc\xff")
        self.stderr = io.BytesIO(b"err\r\n")
        self.closed = False

    def wait(self, timeout):
        self.returncode = 0
        return 0

    def close(self):
        assert self.returncode == 0 and self.stdout.closed and self.stderr.closed
        self.closed = True


def test_real_facade_decoding_and_confirmed_native_close(monkeypatch):
    root = Root()
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    assert facade.communicate(timeout=10) == ("a\nb\nc�", "err\n")
    assert not root.closed
    facade.begin_cleanup()
    facade.wait(timeout=0)
    facade.communicate(timeout=0)
    facade.close()
    assert root.closed and all(not reader.thread.is_alive() for reader in facade.readers)


def test_zero_wait_settlement_never_renews_clock_or_starts_unattempted_reader(monkeypatch):
    root = Root()
    facade = pt._ObservationProcess(["git"], command_deadline=5, cleanup_deadline=10)
    facade.attach(root, object())
    facade.begin_cleanup()
    monkeypatch.setattr(pt, "time", SimpleNamespace(monotonic=lambda: pytest.fail("renewed clock")))
    assert facade.communicate(timeout=0) == ("", "")
    facade.wait(timeout=0)
    facade.close()
    assert root.closed and all(not reader.attempted for reader in facade.readers)


def test_partial_reader_start_retains_every_record_and_zero_wait_can_retry(monkeypatch):
    root = Root()
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    first = facade.readers[0]
    start = first.thread.start

    def interrupted_start():
        start()
        raise KeyboardInterrupt

    monkeypatch.setattr(first.thread, "start", interrupted_start)
    with pytest.raises(KeyboardInterrupt):
        facade.communicate(timeout=10)
    assert facade.native is root and first.attempted and not root.closed
    first.thread.join(10)
    facade.begin_cleanup()
    facade.communicate(timeout=0)
    facade.wait(timeout=0)
    facade.close()
    assert root.closed and len(facade.readers) == 2


def test_facade_rejects_unconfirmed_close():
    facade = pt._ObservationProcess(["git"], command_deadline=5, cleanup_deadline=10)
    facade.attach(Root(), object())
    with pytest.raises(RuntimeError):
        facade.close()


def test_reader_join_interruption_keeps_objects_until_actual_completion(monkeypatch):
    root = Root()
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    first = facade.readers[0]
    join = first.thread.join
    monkeypatch.setattr(first.thread, "join", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        facade.communicate(timeout=10)
    assert facade.native is root and len(facade.readers) == 2 and not root.closed
    monkeypatch.setattr(first.thread, "join", join)
    for reader in facade.readers:
        assert reader.done.wait(10)
    facade.begin_cleanup()
    facade.communicate(timeout=0)
    facade.wait(timeout=0)
    facade.close()
    assert root.closed


def test_genuine_thread_creation_failure_settles_without_clock_or_retry(monkeypatch):
    import threading

    root = Root()
    root.returncode = 0
    facade = pt._ObservationProcess(["git"], command_deadline=5, cleanup_deadline=10)
    facade.attach(root, object())
    calls = []

    def unavailable(*args, **kwargs):
        calls.append("creation")
        raise RuntimeError("cannot create thread")

    monkeypatch.setattr(threading, "_start_new_thread", unavailable)
    with pytest.raises(RuntimeError, match="cannot create"):
        facade.communicate(timeout=10)
    original = RuntimeError("original operation")
    error = pt.ObservationCleanupError("retained")
    error.process, error.job = facade, None
    error._job_empty_confirmed = True
    error.operation_error = original
    monkeypatch.setattr(pt, "time", SimpleNamespace(monotonic=lambda: pytest.fail("renewed D")))
    assert [error.try_settle() for _ in range(3)] == [True, True, True]
    assert root.closed and calls == ["creation"]
    assert error.operation_error is original


def test_runtime_error_with_pending_launch_remains_uncertain(monkeypatch):
    import threading

    root = Root()
    root.returncode = 0
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    reader = facade.readers[0]
    start = reader.thread.start

    def pending_launch():
        with threading._active_limbo_lock:
            threading._limbo[reader.thread] = reader.thread
        raise RuntimeError("launch outcome uncertain")

    monkeypatch.setattr(reader.thread, "start", pending_launch)
    error = pt.ObservationCleanupError("retained")
    error.process, error.job, error._job_empty_confirmed = facade, None, True
    try:
        with pytest.raises(RuntimeError, match="uncertain"):
            facade.communicate(timeout=10)
        assert error.try_settle() is False
        assert not root.closed and not reader.done.is_set()
        start()
        assert reader.done.wait(10)
        assert error.try_settle() is True
        assert root.closed
    finally:
        if reader.thread.ident is not None:
            reader.thread.join(10)
        with threading._active_limbo_lock:
            threading._limbo.pop(reader.thread, None)


def test_runtime_error_after_actual_launch_does_not_claim_never_started(monkeypatch):
    root = Root()
    root.returncode = 0
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    reader = facade.readers[0]
    start = reader.thread.start

    def launched():
        start()
        raise RuntimeError("after launch")

    monkeypatch.setattr(reader.thread, "start", launched)
    try:
        with pytest.raises(RuntimeError, match="after launch"):
            facade.communicate(timeout=10)
        assert reader.done.wait(10)
        error = pt.ObservationCleanupError("retained")
        error.process, error.job, error._job_empty_confirmed = facade, None, True
        assert error.try_settle() is True
        assert not getattr(reader, "never_started", False)
        assert root.closed
    finally:
        reader.thread.join(10)


def test_creation_failure_proof_is_nonblocking_and_retried_after_registry_unlock(monkeypatch):
    import threading

    root = Root()
    root.returncode = 0
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    reader = facade.readers[0]
    monkeypatch.setattr(reader.thread, "start", lambda: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        facade.communicate(timeout=10)
    held, release = threading.Event(), threading.Event()

    def hold_registry():
        with threading._active_limbo_lock:
            held.set()
            assert release.wait(15)

    worker = threading.Thread(target=hold_registry)
    worker.start()
    error = pt.ObservationCleanupError("retained")
    error.process, error.job, error._job_empty_confirmed = facade, None, True
    try:
        assert held.wait(15)
        monkeypatch.setattr(pt, "time", SimpleNamespace(monotonic=lambda: pytest.fail("renewed D")))
        assert error.try_settle() is False
        assert not root.closed
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive()
    assert error.try_settle() is True and root.closed


def test_interruption_before_launch_remains_uncertain_until_completion(monkeypatch):
    root = Root()
    root.returncode = 0
    facade = pt._ObservationProcess(["git"], command_deadline=1e20, cleanup_deadline=1e20)
    facade.attach(root, object())
    reader = facade.readers[0]
    start = reader.thread.start
    monkeypatch.setattr(reader.thread, "start", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    error = pt.ObservationCleanupError("retained")
    error.process, error.job, error._job_empty_confirmed = facade, None, True
    try:
        with pytest.raises(KeyboardInterrupt):
            facade.communicate(timeout=10)
        assert error.try_settle() is False and not root.closed
        start()
        assert reader.done.wait(10)
        assert error.try_settle() is True and root.closed
    finally:
        if reader.thread.ident is not None:
            reader.thread.join(10)
