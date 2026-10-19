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
        calls.append((project, args, kw)) or ({
            "obligations": [], "unresolved": [],
        } if "transitive" in args else {
            "outbound": [{"kind": "pr", "ref": "example/repo#1", "state": "active"}],
        })
    ))
    engine = SimpleNamespace(src=SimpleNamespace(LOCAL=("host", "windows")),
                             _provider_runner=lambda: "selected-provider")
    details = PickerScreenWorktreeActionsMixin._load_worktree_claim_details(engine, _record())
    assert details[0]["ref"] == "example/repo#1"
    assert calls[0][1] == ["claims", "one", "--json"]
    assert calls[0][2] == {"runner": "selected-provider"}
    assert calls[1][1] == ["claims", "transitive", "one", "--json"]
    assert calls[1][2] == calls[0][2]


def test_worktree_details_remote_query_preserves_selected_source_identity(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    calls = []
    monkeypatch.setattr(data_ssh, "claims_argv", lambda *args, **kw: (
        calls.append((args, kw)) or ["transitive" if kw.get("transitive") else "selected-remote"]
    ))
    monkeypatch.setattr(maintenance, "_ssh_json", lambda argv: (
        {"obligations": [], "unresolved": []} if argv == ["transitive"] else {"outbound": []}
    ))
    rec = _record()
    rec.update(source_id="remote-one", source={
        "instance_id": "instance", "transport_fingerprint": "transport",
        "venue": {"assignment": {"slot": "one"}},
    })
    engine = SimpleNamespace(src=SimpleNamespace(LOCAL=("other", "windows")))
    assert PickerScreenWorktreeActionsMixin._load_worktree_claim_details(engine, rec) == []
    identity = {
        "source_id": "remote-one", "expected_instance_id": "instance",
        "expected_assignment": {"slot": "one"}, "expected_transport_fingerprint": "transport",
    }
    assert calls == [(("host", "windows", "one"), identity),
                     (("host", "windows", "one"), {**identity, "transitive": True})]


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


@pytest.mark.parametrize("remote", [False, True])
def test_descendant_inspection_uses_existing_provider_query_and_preserves_owners(monkeypatch, remote):
    from worktree_manager.production_picker import context
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    monkeypatch.setattr(context, "project", lambda: "project")
    direct = {"outbound": [
        {"kind": "pr", "ref": "https://github.com/example/repo/pull/1", "state": "active"},
        {"kind": "workdir", "ref": "retained", "state": "at-rest"},
    ]}
    inherited = {"obligations": [
        {"kind": "pr", "ref": "https://github.com/example/repo/pull/1", "state": "active",
         "path": ["parent"]},
        {"kind": "pr", "ref": "https://github.com/example/child/pull/2", "state": "active",
         "path": ["parent", "child"]},
        {"kind": "pr", "ref": "https://github.com/example/leaf/pull/3", "state": "active",
         "path": ["parent", "child", "grandchild"]},
    ], "unresolved": [
        {"path": ["parent", "child"], "ref": "remote/repo/other",
         "reason": "cross-machine child"},
    ]}
    calls = []
    rec = _record()
    rec["raw"]["claims_projection"] = {
        "inherited_pr_count": 2, "unresolved_count": 1, "complete": False,
    }
    if remote:
        monkeypatch.setattr(data_ssh, "claims_argv", lambda *a, **kw: (
            calls.append(kw) or ["transitive" if kw.get("transitive") else "direct"]
        ))
        monkeypatch.setattr(maintenance, "_ssh_json", lambda argv: (
            inherited if argv == ["transitive"] else direct
        ))
    else:
        monkeypatch.setattr(engine_client, "run_json", lambda project, args, **kw: (
            calls.append(args) or (inherited if "transitive" in args else direct)
        ))
    engine = SimpleNamespace(
        src=SimpleNamespace(LOCAL=("elsewhere" if remote else "host", "windows")),
        _provider_runner=lambda: "selected-provider",
    )
    details = PickerScreenWorktreeActionsMixin._load_worktree_claim_details(engine, rec)
    assert len(calls) == 2
    if remote:
        assert calls[1]["transitive"] is True
    else:
        assert calls == [["claims", "one", "--json"], ["claims", "transitive", "one", "--json"]]
    assert len(details) == 5  # no second copy of the root's direct PR
    assert details[1]["state"] == "at-rest"
    assert details[3]["path"] == ["parent", "child", "grandchild"]
    assert details[4]["note"] == "cross-machine child"
    screen = WtDetailsScreen(rec)
    screen._loading = False
    screen._details = details
    body = screen._body()
    assert "owner parent -> child -> grandchild" in body.plain
    assert "unresolved" in body.plain and "cross-machine child" in body.plain
    assert any("https://github.com/example/leaf/pull/3" in str(s.style) for s in body.spans)


@pytest.mark.parametrize("reason", ["record not found", "cycle detected", "cross-machine child"])
def test_detail_keeps_unresolved_edges_visible(reason):
    from worktree_manager.production_picker.picker_tui.worktree_assets import descendant_details

    details = descendant_details({
        "obligations": [],
        "unresolved": [{"path": ["parent"], "ref": "host/repo/child", "reason": reason}],
    })
    assert details[0]["state"] == "unresolved"
    assert details[0]["note"] == reason
    with pytest.raises(TypeError, match="descendant"):
        descendant_details({"obligations": []})


@pytest.mark.parametrize("ref", ["example/leaf#3", "cref1:pr:github:example/leaf#3"])
def test_descendant_detail_uses_producer_url_without_rewriting_ref_or_owner(ref: str) -> None:
    from worktree_manager.production_picker.picker_tui.worktree_assets import descendant_details

    rec = _record()
    url = "https://github.com/example/leaf/pull/3"
    details = descendant_details({
        "obligations": [{
            "kind": "pr", "ref": ref, "url": url, "state": "active",
            "path": ["parent", "child", "grandchild"],
        }],
        "unresolved": [],
    })
    screen = WtDetailsScreen(rec)
    screen._loading = False
    screen._details = details
    body = screen._body()
    assert ref in body.plain
    assert "owner parent -> child -> grandchild" in body.plain
    assert any(url in str(span.style) for span in body.spans)
    assert details[0]["ref"] == ref


@pytest.mark.parametrize("url", ["javascript:alert(1)", "", None])
def test_descendant_detail_does_not_link_unsupported_urls(url: str | None) -> None:
    screen = WtDetailsScreen(_record())
    screen._loading = False
    screen._details = [{
        "kind": "pr", "ref": "example/leaf#3", "url": url,
        "path": ["parent", "child"],
    }]
    body = screen._body()
    assert "example/leaf#3" in body.plain
    assert not any("link " in str(span.style) for span in body.spans)
