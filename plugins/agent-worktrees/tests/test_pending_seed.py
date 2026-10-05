"""``pending_seed.settle_claim`` reports a kept seed only once it is confirmed."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

from agent_worktrees import pending_seed, tracking

_NEVER_TYPED = {"ok": False, "sent": False, "reason": "not-ready-timeout"}


def test_exhausted_lock_retries_report_the_seed_lost_not_deferred(monkeypatch) -> None:
    """The sidecar lock stays busy for every restore attempt: the claimed seed
    is not back in the record, so it must not be reported as kept."""

    class Busy:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            raise TimeoutError("sidecar busy")

        def __exit__(self, *a) -> bool:
            return False

    monkeypatch.setattr(tracking, "_RecordLock", Busy)
    monkeypatch.setattr(pending_seed.time, "sleep", lambda s: None)
    assert pending_seed.restore_pending_seed(Path("wt.yaml"), "do it") is False
    report = pending_seed.settle_claim(Path("wt.yaml"), "do it", _NEVER_TYPED)
    assert report == {"seed_reason": "not-ready-timeout", "seed_lost": True}


def test_a_confirmed_restore_reports_the_seed_deferred(monkeypatch) -> None:
    record = SimpleNamespace(pending_seed=None, pending_seed_revision=0)
    saved: list[str] = []
    monkeypatch.setattr(tracking, "_RecordLock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(tracking, "load_record", lambda p: record)
    monkeypatch.setattr(tracking, "save_record", lambda rec, p: saved.append(rec.pending_seed))
    report = pending_seed.settle_claim(Path("wt.yaml"), "do it", _NEVER_TYPED)
    assert report == {"seed_reason": "not-ready-timeout", "seed_deferred": True}
    assert saved == ["do it"]


def test_an_unreadable_record_reports_the_seed_lost(monkeypatch) -> None:
    def broken(p):
        raise ValueError("corrupt record")

    monkeypatch.setattr(tracking, "_RecordLock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(tracking, "load_record", broken)
    assert pending_seed.settle_claim(Path("wt.yaml"), "do it", _NEVER_TYPED)["seed_lost"] is True
