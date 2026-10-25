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
