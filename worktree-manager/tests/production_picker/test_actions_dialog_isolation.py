"""Deterministic Actions consumer contracts, not native latency measurements."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker import context, engine_group_c, project_config
from worktree_manager.production_picker.picker_tui.engine_dialogs import SubMenuScreen
from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
    PickerScreenWorktreeActionsMixin,
)


def record(source="local", wid="example-win-row"):
    return {
        "source_kind": "machine-ssh",
        "source_id": f"machine-ssh:{'example' if source == 'local' else source}:win",
        "machine": "example", "env": "Win", "id4": "row",
        "title": "Cached title", "state": "STOPPED", "last_session_id": "session",
        "raw": {"id": wid, "title": "Cached title", "last_session_id": "session"},
    }


class ScreenHost:
    screen = None

    def push_screen(self, screen, callback):
        self.screen, self.callback = screen, callback

    def close(self):
        callback, self.screen = self.callback, None
        callback(None)


class Picker(PickerScreenWorktreeActionsMixin):
    def __init__(self, rec):
        self.rec = rec
        self.data = [rec]
        self.src = SimpleNamespace(LOCAL=("example", "Win"))
        self._source_local = ("example", "Win")
        self._setup_epoch = 1
        self.real_ops = True
        self.wt_actions = []
        self.requests = []
        self.app = ScreenHost()

    def _submenu_target(self):
        return self.rec

    def _worker_menu_verbs(self, rec):
        return [], {}

    def _run_bg(self, label, work, done, **kwargs):
        self.requests.append((work, done))


@pytest.fixture
def bound(monkeypatch):
    monkeypatch.setattr(context, "_project", "example")
    monkeypatch.setattr(context, "_bootstrap", None)


def test_cold_actions_pushes_before_any_discovery(bound, monkeypatch):
    picker = Picker(record())
    picker._source_local = None
    calls = []

    class ColdSource:
        @property
        def LOCAL(self):
            calls.append("local")
            raise RuntimeError("provider not ready")

    picker.src = ColdSource()
    monkeypatch.setattr(project_config, "tracking_dir",
                        lambda *a: pytest.fail("input-thread path discovery"))
    picker.rec["execution_leg"] = {"provider": "ahp", "state": "active"}
    picker._open_submenu()
    menu = picker.app.screen
    assert isinstance(menu, SubMenuScreen) and menu._loading
    assert calls == []
    assert not menu._has_ahp
    assert "Dispose hosted session" not in menu._actions
    work, done = picker.requests.pop()
    done(work())
    assert calls == ["local"]
    assert not menu._loading
    assert not menu._rec.get("session_lock_live")


@pytest.mark.parametrize("change", [
    "close", "close-open-other", "close-reopen", "source", "source-id",
    "epoch", "project", "bootstrap",
])
def test_late_actions_result_cannot_change_another_lifetime(bound, change):
    original = record()
    picker = Picker(original)
    picker._open_submenu()
    menu = picker.app.screen
    _, done = picker.requests[-1]
    if change in {"close", "close-open-other", "close-reopen"}:
        picker.app.close()
        if change != "close":
            picker.rec = record("other" if change == "close-open-other" else "local")
            picker._open_submenu()
    elif change == "source":
        picker.src = SimpleNamespace(LOCAL=("example", "Win"))
    elif change == "source-id":
        original["source_id"] = "replacement"
    elif change == "epoch":
        picker._setup_epoch += 1
    elif change == "project":
        context.set_project("other")
    else:
        context.bind_project_bootstrap({
            "project": "example", "should_switch_cwd": False, "default_live": True,
        })
    before = deepcopy(original)
    current = picker.app.screen
    current_before = deepcopy(current._rec) if current else None
    ext = picker._wt_submenu_ext
    workers = picker._wt_submenu_workers
    done(({"id": "example-win-row", "session_lock_live": True}, True))
    assert original == before
    assert menu._loading
    assert picker._wt_submenu_ext is ext
    assert picker._wt_submenu_workers is workers
    if current:
        assert current._rec == current_before
        assert current._loading


@pytest.mark.parametrize("result", [(None, None), (None, True), (None, False)])
def test_missing_or_failed_verification_preserves_cached_snapshot(bound, result):
    original = record()
    picker = Picker(original)
    picker._open_submenu()
    menu = picker.app.screen
    _, done = picker.requests.pop()
    before = deepcopy(original)
    done(result)
    assert original == before
    assert not menu._loading
    assert menu._rec["title"] == "Cached title"
    assert not menu._rec.get("session_lock_live")


def test_remote_source_never_reconciles_local_tracking(bound, monkeypatch):
    picker = Picker(record("remote"))
    picker.rec["machine"] = "remote"
    monkeypatch.setattr(project_config, "tracking_dir",
                        lambda *a: pytest.fail("remote row used local tracking"))
    monkeypatch.setattr(engine_group_c, "picker_reconcile_local",
                        lambda *a, **kw: pytest.fail("remote row reconciled locally"))
    picker._open_submenu()
    work, done = picker.requests.pop()
    done(work())
    assert picker.app.screen._rec["is_local"] is False


def test_worker_uses_source_local_alias_canonicalization(bound, monkeypatch, tmp_path):
    picker = Picker(record())
    calls = []
    picker.src = SimpleNamespace(
        LOCAL=("raw-hostname", "Win"),
        is_local=lambda machine, env: calls.append((machine, env)) or True,
    )
    (tmp_path / "example-win-row.yaml").write_text("id: example-win-row\n")
    monkeypatch.setattr(project_config, "tracking_dir", lambda project: tmp_path)
    monkeypatch.setattr(engine_group_c, "picker_reconcile_local",
                        lambda *a, **kw: SimpleNamespace(rows=[{
                            "id": "example-win-row", "session_lock_live": True,
                        }]))
    picker._source_local = ("raw-hostname", "Win")
    picker._open_submenu()
    assert calls == []
    work, done = picker.requests.pop()
    done(work())
    assert calls == [("example", "Win")]
    assert picker.app.screen._rec["is_local"] is True
    assert picker.app.screen._rec["session_lock_live"] is True


def test_worker_uses_captured_project_and_missing_record_skips_probe(bound, monkeypatch, tmp_path):
    picker = Picker(record())
    calls = []
    monkeypatch.setattr(project_config, "tracking_dir",
                        lambda project: calls.append(project) or tmp_path)
    monkeypatch.setattr(engine_group_c, "picker_reconcile_local",
                        lambda *a, **kw: pytest.fail("missing record reconciled"))
    picker._open_submenu()
    work, done = picker.requests.pop()
    context.set_project("other")
    result = work()
    assert calls == ["example"]
    done(result)
    assert picker.app.screen._loading


def test_callback_uses_its_own_action_maps(bound):
    picker = Picker(record())
    seen = []
    picker._wt_submenu_dispatch = lambda rec, result, **maps: seen.append(maps)
    picker._open_submenu()
    callback = picker.app.callback
    menu = picker.app.screen
    picker._wt_submenu_ext = {"foreign": object()}
    picker._wt_submenu_workers = {"foreign": object()}
    callback(("Refresh", False, False))
    assert seen == [{"ext": menu._action_extensions, "workers": menu._action_workers}]
    assert seen[0]["ext"] == seen[0]["workers"] == {}


def test_superseded_dialog_callback_does_not_dispatch(bound):
    picker = Picker(record())
    seen = []
    picker._wt_submenu_dispatch = lambda *a, **kw: seen.append(a)
    picker._open_submenu()
    callback = picker.app.callback
    picker._open_submenu()
    callback(("Refresh", False, False))
    assert seen == []


def test_refinement_preserves_applicable_modifiers_and_clears_removed_ones():
    rec = {**record(), "is_local": True}
    menu = SubMenuScreen(rec, ["Resume"])
    menu._no_mux = menu._ahp = True
    menu.refresh_actions(["Resume", "View details"])
    assert menu.no_mux and menu.ahp
    menu.refresh_actions(["Reclaim", "View details"])
    assert not menu.no_mux and not menu.ahp


def test_repeated_out_of_order_completions_preserve_newest_menu(bound):
    picker = Picker(record())
    for _ in range(3):
        picker._open_submenu()
        _, old_done = picker.requests[-1]
        picker.app.close()
        picker._open_submenu()
        _, new_done = picker.requests[-1]
        new_done(({"id": "example-win-row", "session_lock_live": True}, True))
        menu = picker.app.screen
        accepted = deepcopy(menu._rec)
        old_done(({"id": "example-win-row", "session_lock_live": False}, True))
        assert menu._rec == accepted
        assert "Reclaim" in menu._actions and not menu._loading
        picker.app.close()


@pytest.mark.parametrize("failure", ["exception", "empty", "wrong-row"])
def test_provider_failure_does_not_verify_cached_row(bound, monkeypatch, tmp_path, failure):
    picker = Picker(record())
    (tmp_path / "example-win-row.yaml").write_text("id: example-win-row\n")
    monkeypatch.setattr(project_config, "tracking_dir", lambda project: tmp_path)

    def reconcile(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(rows=[] if failure == "empty" else [{"id": "other"}])

    monkeypatch.setattr(engine_group_c, "picker_reconcile_local", reconcile)
    picker._open_submenu()
    work, done = picker.requests.pop()
    done(work())
    assert picker.app.screen._rec["title"] == "Cached title"
    assert not picker.app.screen._rec.get("session_lock_live")
    assert not picker.app.screen._loading


def test_real_modal_updates_header_verbs_and_unknown_footer_together(bound):
    from textual.app import App
    from textual.widgets import Static

    async def run():
        picker = Picker(record())
        app = App()
        picker.app = app
        async with app.run_test() as pilot:
            picker._open_submenu()
            await pilot.pause()
            menu = app.screen
            _, done = picker.requests.pop()
            done(({"id": "example-win-row", "session_lock_live": True}, True))
            await pilot.pause()
            assert not menu._loading
            assert "Reclaim" in menu._actions
            assert "lock live" in menu.query_one("#sub-head", Static).render().plain
            assert not menu.query_one("#sub-foot", Static).render().plain
            picker._refresh_wt_submenu(
                menu._rec, screen=menu, status="cached · liveness not verified",
            )
            assert "not verified" in menu.query_one("#sub-foot", Static).render().plain
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not menu

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["unbound", "error", "missing", "success"])
def test_pre_mount_refinement_keeps_verification_status(bound, outcome):
    from textual.app import App
    from textual.widgets import Static

    async def run():
        picker = Picker(record())
        app = App()
        picker.app = app
        async with app.run_test() as pilot:
            if outcome == "unbound":
                context.reset()
            picker._open_submenu()
            menu = app.screen
            assert not menu.is_mounted
            if outcome != "unbound":
                _, done = picker.requests.pop()
                row = {"id": "example-win-row", "session_lock_live": True}
                done((row, True) if outcome == "success" else (None, None))
            await pilot.pause()
            assert menu.is_mounted and not menu._loading
            footer = menu.query_one("#sub-foot", Static).render().plain
            if outcome == "success":
                assert footer == ""
                assert "lock live" in menu.query_one("#sub-head", Static).render().plain
            else:
                assert "liveness not verified" in footer

    asyncio.run(run())


def test_refinement_after_composition_is_replayed_on_mount():
    from textual.app import App
    from textual.widgets import Static

    async def run():
        app = App()
        menu = SubMenuScreen(record(), ["Resume"], loading=True)
        compose = menu.compose

        def refine_after_compose():
            yield from compose()
            menu._rec["title"] = "Refined title"
            menu.refresh_actions(["Reclaim"], status="cached · liveness not verified")

        menu.compose = refine_after_compose
        async with app.run_test() as pilot:
            app.push_screen(menu)
            await pilot.pause()
            assert "not verified" in menu.query_one("#sub-foot", Static).render().plain
            assert "Refined title" in menu.query_one("#sub-head", Static).render().plain
            assert menu._actions == ["Reclaim"] and not menu._loading

    asyncio.run(run())
