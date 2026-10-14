"""Receipt scheduling preserves unresolved generations without starving siblings."""

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_dispatch import companion
from agent_dispatch.companion import CompanionIndeterminate, DefaultCompanionController
from tests.test_bounded_receipt_reconcile import _receipt


def test_unknown_receipts_do_not_starve_later_withdrawals(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    for rid in ("a", "b", "c"):
        _receipt(controller, rid)
    calls = Counter()

    def retire(receipt, **kwargs):
        rid = receipt["registration_id"]
        calls[rid] += 1
        if rid in {"a", "b"}:
            raise CompanionIndeterminate("injected unresolved retirement")

    monkeypatch.setattr(controller, "_retire_receipt", retire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(6):
            controller.reconcile_receipts_bounded(
                set(), executor=pool, deadline=time.monotonic() + 2,
            )
            assert len(controller._receipt_operations) <= 2
            for rid, future in tuple(controller._receipt_operations.items()):
                try:
                    future.result(1)
                except CompanionIndeterminate:
                    assert rid in {"a", "b"}
    completed = controller._operation_record("c")
    assert completed is not None and completed["state"] == "complete"
    assert not controller._receipt_path("c").exists()
    for rid in ("a", "b"):
        assert controller._operation_record(rid)["state"] == "unknown"
        assert controller._receipt_path(rid).exists()
        with pytest.raises(CompanionIndeterminate, match="not confirmed"):
            controller._assert_receipt_available(rid)
    assert calls == {"a": 1, "b": 1, "c": 1}


def test_unmanaged_retirement_requires_exit_confirmation(tmp_path, monkeypatch):
    clock = [time.monotonic()]
    signals = []

    def token(pid):
        return "owner" if pid == os.getpid() else "child-generation"

    controller = DefaultCompanionController(
        tmp_path / "receipts", token_source=token,
        monotonic=lambda: clock[0], sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    _receipt(controller)
    before = controller._receipt_path("withdrawn").read_bytes()
    monkeypatch.setattr(companion, "_terminate_windows_tree", lambda pid: signals.append(pid))
    monkeypatch.setattr(companion, "_terminate_posix_group", lambda pid: signals.append(pid))
    with ThreadPoolExecutor(max_workers=2) as pool:
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=clock[0] + 20)
        with pytest.raises(CompanionIndeterminate, match="retirement is not confirmed"):
            controller._receipt_operations["withdrawn"].result(1)
    assert signals == [100]
    assert controller._receipt_path("withdrawn").read_bytes() == before
    assert controller._operation_record("withdrawn")["state"] == "unknown"
    with pytest.raises(CompanionIndeterminate, match="not confirmed"):
        controller._assert_receipt_available("withdrawn")
