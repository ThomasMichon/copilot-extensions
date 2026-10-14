"""Current aggregates retain explicit, background-only full claim inspection."""

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App

from worktree_manager import engine_client
from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine_dialogs import WtDetailsScreen
from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
    PickerScreenWorktreeActionsMixin,
)
from worktree_manager.production_picker.picker_tui.worktree_assets import claim_details


def _record():
    return derive.norm({
        "id": "one", "title": "Current work", "status": "active",
        "claims_assets": {"held_count": 1100, "by_kind": {"worktree": 1100},
                         "details_available": True},
    }, "host", "windows")


@pytest.mark.parametrize("failed", [False, True])
def test_worktree_details_opens_before_full_claim_io_and_surfaces_result(failed):
    pending = []
    rec = _record()
    engine = SimpleNamespace(
        _run_bg=lambda label, work, done, **kw: pending.append((work, done, kw)),
        _load_worktree_claim_details=lambda selected: [
            {"kind": "worktree", "ref": "last-child", "note": "", "state": "active"},
        ],
    )
    screen = WtDetailsScreen(rec, engine=engine)

    class DetailsApp(App):
        def on_mount(self):
            self.push_screen(screen)

    async def run():
        async with DetailsApp().run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert "1100" in screen._body().plain
            assert "loading full details" in screen._body().plain
            assert "held claims: none" not in screen._body().plain
            work, done, options = pending.pop()
            assert options == {"quiet": True}
            done(None if failed else work())
            await pilot.pause()
            body = screen._body().plain
            assert ("inspection failed" in body) if failed else ("last-child" in body)
            assert "close to see the provider error" not in body
            assert rec["asset_hints"]["details"] == []
            assert rec["asset_hints"]["held_count"] == 1100

    asyncio.run(run())


def test_worktree_details_local_query_uses_exact_identity(monkeypatch):
    from worktree_manager.production_picker import context

    monkeypatch.setattr(context, "project", lambda: "project")
    calls = []
    monkeypatch.setattr(engine_client, "run_json", lambda project, args, **kw: (
        calls.append((project, args, kw)) or {
            "outbound": [{"kind": "pr", "ref": "example/repo#1", "state": "active"}],
        }
    ))
    engine = SimpleNamespace(src=SimpleNamespace(LOCAL=("host", "windows")),
                             _provider_runner=lambda: "selected-provider")
    details = PickerScreenWorktreeActionsMixin._load_worktree_claim_details(engine, _record())
    assert details[0]["ref"] == "example/repo#1"
    assert calls[0][1] == ["claims", "one", "--json"]
    assert calls[0][2] == {"runner": "selected-provider"}


def test_worktree_details_remote_query_preserves_selected_source_identity(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    calls = []
    monkeypatch.setattr(data_ssh, "claims_argv", lambda *args, **kw: (
        calls.append((args, kw)) or ["selected-remote"]
    ))
    monkeypatch.setattr(maintenance, "_ssh_json", lambda argv: {"outbound": []})
    rec = _record()
    rec.update(source_id="remote-one", source={
        "instance_id": "instance", "transport_fingerprint": "transport",
        "venue": {"assignment": {"slot": "one"}},
    })
    engine = SimpleNamespace(src=SimpleNamespace(LOCAL=("other", "windows")))
    assert PickerScreenWorktreeActionsMixin._load_worktree_claim_details(engine, rec) == []
    assert calls == [(("host", "windows", "one"), {
        "source_id": "remote-one", "expected_instance_id": "instance",
        "expected_assignment": {"slot": "one"}, "expected_transport_fingerprint": "transport",
    })]


def test_worktree_details_full_inspection_is_not_the_bounded_summary():
    details = claim_details({"outbound": [
        {"kind": "worktree", "ref": f"child-{i}", "state": "active"} for i in range(1100)
    ]})
    assert len(details) == 1100
    assert details[-1]["ref"] == "child-1099"
    with pytest.raises(TypeError, match="omitted"):
        claim_details({})
    with pytest.raises(ValueError, match="invalid outbound claim"):
        claim_details({"outbound": ["invalid"]})
