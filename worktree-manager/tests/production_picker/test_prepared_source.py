"""Early data enrollment is scoped, single-use and cancellation-owned."""

from types import SimpleNamespace
import threading
import time

import pytest

from worktree_manager.production_picker.prepared_source import PreparedSource


def test_prepared_source_starts_one_loader_without_repeating_admission():
    calls = []
    local = SimpleNamespace(key=("fixture", "Win"), local=True)
    snapshot = [local]
    loader = SimpleNamespace(
        start=lambda focus_keys=None: calls.append(("start", {"focus_keys": focus_keys})),
        cancel=lambda: calls.append(("cancel",)),
        records=lambda: ["committed-row"],
    )
    source = SimpleNamespace(
        source_snapshot=lambda: calls.append(("snapshot",)) or snapshot,
        make_loader=lambda value: loader if value is snapshot else pytest.fail("snapshot changed"),
        REPO="sample",
    )
    prepared = PreparedSource(source)
    try:
        assert not calls
        prepared.start()
        prepared.start()
        assert prepared.source_snapshot() is snapshot
        started = prepared.make_loader(snapshot)
        started.start(focus_keys={local.key})
        assert started.records() == ["committed-row"]
        assert prepared.REPO == "sample"
        assert calls == [("snapshot",), ("start", {"focus_keys": {local.key}})]
    finally:
        prepared.close()
    assert calls[-1] == ("cancel",)


def test_cancel_before_binding_never_reads_or_starts_a_source():
    source = SimpleNamespace(source_snapshot=lambda: pytest.fail("cancelled binding read data"))
    prepared = PreparedSource(source)
    prepared.close()
    prepared.start()
    with pytest.raises(RuntimeError, match="cancelled"):
        prepared.source_snapshot()


def test_reload_gets_a_fresh_loader_and_old_cancellation_cannot_stop_updates():
    loaders = []
    local = SimpleNamespace(key=("fixture", "Win"), local=True)

    class Loader:
        def __init__(self):
            self.cancelled = False
            self.rows = []
            self.starts = 0

        def start(self, focus_keys=None):
            self.starts += 1
            self.rows = ["current"]

        def cancel(self):
            self.cancelled = True

        def records(self):
            return self.rows if not self.cancelled else []

    def make_loader(snapshot):
        assert snapshot == [local]
        loaders.append(Loader())
        return loaders[-1]

    prepared = PreparedSource(SimpleNamespace(
        source_snapshot=lambda: [local], make_loader=make_loader,
    ))
    try:
        prepared.start()
        initial = prepared.make_loader(prepared.source_snapshot())
        replacement = prepared.make_loader(prepared.source_snapshot())
        initial.cancel()
        replacement.start(focus_keys={local.key})
        replacement.rows.append("updated")
        assert replacement.records() == ["current", "updated"]
        assert len(loaders) == 2
        assert [loader.starts for loader in loaders] == [1, 1]
        replacement.cancel()
    finally:
        prepared.close()


def test_late_snapshot_after_cancel_never_creates_a_loader():
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    def snapshot():
        entered.set()
        assert release.wait(2)
        returned.set()
        return []

    source = SimpleNamespace(
        source_snapshot=snapshot,
        make_loader=lambda *_args: pytest.fail("late cancelled snapshot started a loader"),
    )
    prepared = PreparedSource(source)
    try:
        prepared.start()
        assert entered.wait(1)
        prepared.close()
        release.set()
        assert returned.wait(1)
        assert prepared._ready.wait(1)
    finally:
        release.set()
        prepared.close()


def test_preparation_error_is_visible_and_does_not_start_another_source():
    def snapshot():
        raise ValueError("invalid source registry")

    prepared = PreparedSource(SimpleNamespace(source_snapshot=snapshot))
    try:
        prepared.start()
        with pytest.raises(ValueError, match="invalid source registry"):
            prepared.source_snapshot()
    finally:
        prepared.close()


def test_new_binding_resets_the_preloaded_apps_user_idle_clock(monkeypatch):
    from worktree_manager.production_picker.picker_tui import engine

    app = SimpleNamespace(
        _last_input_activity=0.0, _mock_mode=False, _after_first_refresh=None,
        query_one=lambda *_args: SimpleNamespace(remove=lambda: None),
        screen=SimpleNamespace(mount=lambda *_args: None),
    )
    monkeypatch.setattr(engine, "PickerScreen", lambda *_args, **_kwargs: object())
    binding = SimpleNamespace(source=object(), live=True)
    before = time.monotonic()
    engine.PickerApp.install_binding(app, binding)
    assert before <= app._last_input_activity <= time.monotonic()
