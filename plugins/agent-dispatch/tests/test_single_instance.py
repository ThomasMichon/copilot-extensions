"""Tests for the crash-safe single-instance lock (supervisor daemon guard)."""

from __future__ import annotations

from agent_dispatch.single_instance import (
    SingleInstance,
    is_locked,
    lock_path_for,
)


def test_lock_path_slugifies_scope(tmp_path):
    p = lock_path_for(tmp_path, "supervisor:lambda-core:default")
    assert p.parent == tmp_path
    assert p.name == "supervisor-lambda-core-default.lock"


def test_second_acquire_is_refused_while_held(tmp_path):
    lp = tmp_path / "s.lock"
    a = SingleInstance(lp)
    b = SingleInstance(lp)
    assert a.acquire() is True
    assert b.acquire() is False  # a live holder blocks a second acquirer
    a.release()
    assert b.acquire() is True  # freed once the holder releases
    b.release()


def test_acquire_is_idempotent_for_holder(tmp_path):
    lp = tmp_path / "s.lock"
    a = SingleInstance(lp)
    assert a.acquire() is True
    assert a.acquire() is True  # same instance re-acquire is a no-op success
    a.release()


def test_is_locked_probe(tmp_path):
    lp = tmp_path / "s.lock"
    assert is_locked(lp) is False  # nothing holds it (and no file yet)
    holder = SingleInstance(lp)
    holder.acquire()
    assert is_locked(lp) is True
    holder.release()
    assert is_locked(lp) is False


def test_context_manager(tmp_path):
    lp = tmp_path / "s.lock"
    with SingleInstance(lp) as ok:
        assert ok is True
        assert is_locked(lp) is True
    assert is_locked(lp) is False
