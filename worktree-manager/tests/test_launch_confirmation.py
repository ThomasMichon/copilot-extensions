from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
import pytest
from textual.app import App
from textual.screen import Screen
from textual.widgets import Static

from worktree_manager import launch_confirmation as confirmation
from worktree_manager.launch_attempts import LaunchAttemptError, LaunchAttempts
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.engine_dialogs import ScopeDlgScreen, SubMenuScreen
from worktree_manager.production_picker.picker_tui.engine_focus import FocusGroup
from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
    PickerScreenMaintenanceActionsMixin,
)
from worktree_manager.production_picker.picker_tui.seed_prompt_field import SeedPromptField


@pytest.fixture(autouse=True)
def private_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("demo")
    yield tmp_path
    context.reset()


async def settled(pilot, predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await pilot.pause(0.01)


def activate(dialog):
    if isinstance(dialog, SubMenuScreen):
        dialog.on_option_list_option_selected(SimpleNamespace(option_index=0))
        return
    dialog.on_focus_group_activated(
        FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm"),
    )


def resume_dialog(*, enabled=True):
    def decision(_record, *, no_mux, ahp, seed_prompt):
        return {
            "action": "resume", "is_local": True, "worktree_id": "wt-original",
            "machine": "target", "env": "Win",
            "options": {"no_mux": no_mux, "ahp": ahp, "seed_prompt": seed_prompt},
        }

    engine = SimpleNamespace(
        real_ops=enabled, src=SimpleNamespace(), _resume_decision=decision,
        _run_bg=lambda *_args, **_kwargs: None,
    )
    dialog = SubMenuScreen(
        {"is_local": True, "machine": "target", "env": "Win", "raw": {"id": "wt-original"}},
        ["Resume"], engine=engine,
    )
    dialog._no_mux = True
    return dialog


def accepted(dialog):
    return [True] if isinstance(dialog, ScopeDlgScreen) else [
        ("Resume", True, False, dialog._prompt_rec["primary"].text.strip())
    ]


@pytest.mark.parametrize("scope", [False, True])
def test_composer_saves_exact_snapshot_before_dismissal_and_stays_responsive(
    private_root, monkeypatch, scope,
):
    async def run():
        raw = "  exact 'text' \u03bb\nsecond line\n "
        dialog = (
            ScopeDlgScreen({
                "target": ("target", "Win"), "section": 1,
                "opts": [{"label": "No Mux", "on": True}],
            }, show_prompt=True, launch_local=True)
            if scope else resume_dialog()
        )
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        record = LaunchAttempts.record_intent
        calls = []

        def blocked(store, attempt_id, **intent):
            assert threading.get_ident() != main_thread
            calls.append((attempt_id, intent))
            entered.set()
            assert release.wait(10)
            record(store, attempt_id, **intent)

        monkeypatch.setattr(LaunchAttempts, "record_intent", blocked)
        outcomes, ticks = [], []
        app = App()
        async with app.run_test() as pilot:
            app.set_interval(0.01, lambda: ticks.append(True))
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            field = dialog.query_one(SeedPromptField)
            field.load_text(raw)
            activate(dialog)
            await settled(pilot, entered.is_set)
            saving = app.screen
            try:
                assert saving is not dialog and dialog.is_mounted
                assert "Saving launch intent" in str(saving.query_one(Static).render())
                count = len(ticks)
                activate(dialog)
                dialog.action_cancel()
                await pilot.press("x", "enter", "escape")
                await settled(pilot, lambda: len(ticks) > count + 1)
                assert field.text == raw and outcomes == [] and len(calls) == 1
                assert LaunchAttempts(private_root).entries() == []
            finally:
                release.set()
            await settled(pilot, lambda: bool(outcomes))
            assert outcomes == accepted(dialog)
            entry = LaunchAttempts(private_root).detail(
                dialog.launch_decision["attempt_id"], include_input=True,
            )
            assert entry["surface"] == ("new-dialog" if scope else "worktree-actions")
            assert entry["input"]["prompt"] == raw
            assert entry["input"]["options"]["no_mux"] is True
            assert entry["machine"] == "target" and entry["environment"] == "Win"
            assert dialog.launch_decision["options"]["seed_prompt"] == raw.strip()
            assert len(LaunchAttempts(private_root).entries()) == 1
    asyncio.run(run())


def test_error_after_commit_keeps_draft_and_reuses_identity_on_explicit_retry(
    private_root, monkeypatch, caplog,
):
    async def run():
        record = LaunchAttempts.record_intent
        ids = []

        def ambiguous(store, attempt_id, **intent):
            ids.append(attempt_id)
            record(store, attempt_id, **intent)
            if len(ids) == 1:
                raise LaunchAttemptError("private draft must not escape")

        monkeypatch.setattr(LaunchAttempts, "record_intent", ambiguous)
        app, outcomes, notices = App(), [], []
        dialog = resume_dialog()
        monkeypatch.setattr(dialog, "notify", lambda text, **_: notices.append(text))
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            dialog.query_one(SeedPromptField).load_text("private draft")
            activate(dialog)
            await settled(pilot, lambda: bool(notices))
            assert app.screen is dialog and outcomes == []
            assert dialog.query_one(SeedPromptField).text == "private draft"
            assert "private draft" not in notices[0] and "private draft" not in caplog.text
            activate(dialog)
            await settled(pilot, lambda: bool(outcomes))
            assert ids[0] == ids[1] and outcomes == accepted(dialog)
            assert len(LaunchAttempts(private_root).entries()) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", [OSError, LaunchAttemptError])
def test_storage_failure_never_dismisses_or_changes_draft(private_root, monkeypatch, failure):
    async def run():
        def fail(*_args, **_kwargs):
            raise failure("private input must not become an error message")

        monkeypatch.setattr(LaunchAttempts, "record_intent", fail)
        app, outcomes, notices = App(), [], []
        dialog = resume_dialog()
        monkeypatch.setattr(dialog, "notify", lambda text, **_: notices.append(text))
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            field = dialog.query_one(SeedPromptField)
            field.load_text("original private draft")
            activate(dialog)
            await settled(pilot, lambda: bool(notices))
            assert app.screen is dialog and outcomes == []
            assert field.text == "original private draft"
            assert not LaunchAttempts(private_root).path.exists()
            assert "private input" not in notices[0]
    asyncio.run(run())


def test_worker_and_continuation_use_frozen_selection(private_root, monkeypatch):
    async def run():
        entered, release = threading.Event(), threading.Event()
        record = LaunchAttempts.record_intent

        def blocked(store, attempt_id, **intent):
            entered.set()
            assert release.wait(10)
            record(store, attempt_id, **intent)

        monkeypatch.setattr(LaunchAttempts, "record_intent", blocked)
        decision = {
            "action": "new", "is_local": False, "machine": "original",
            "env": "POSIX", "title": "original title",
            "options": {"no_mux": True, "seed_prompt": "execution text"},
        }
        app, calls = App(), []
        async with app.run_test() as pilot:
            confirmation.confirm_before_effect(
                app, "demo", decision, surface="snapshot-test", saved=calls.append,
                prompt=" exact original text\n",
            )
            await settled(pilot, entered.is_set)
            try:
                decision["machine"] = "different"
                decision["options"]["no_mux"] = False
                decision["options"]["seed_prompt"] = "different prompt"
            finally:
                release.set()
            await settled(pilot, lambda: bool(calls))
            assert calls[0]["machine"] == "original"
            assert calls[0]["options"] == {"no_mux": True, "seed_prompt": "execution text"}
            detail = LaunchAttempts(private_root).detail(calls[0]["attempt_id"], include_input=True)
            assert detail["machine"] == "original"
            assert detail["input"] == {
                "prompt": " exact original text\n",
                "options": {"no_mux": True, "title": "original title"},
            }
    asyncio.run(run())


def test_retention_failure_keeps_draft_and_refuses_launch_with_sanitized_notice(private_root, monkeypatch):
    from worktree_manager import launch_retention

    async def run():
        monkeypatch.setattr(
            launch_retention, "maintain",
            lambda: "Private-input retention maintenance failed for prompt history.",
        )
        monkeypatch.setattr(
            LaunchAttempts, "record_intent",
            lambda *_a, **_kw: pytest.fail("retention failure must not release a launch"),
        )
        app, outcomes, notices = App(), [], []
        dialog = resume_dialog()
        monkeypatch.setattr(dialog, "notify", lambda text, **_: notices.append(text))
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            field = dialog.query_one(SeedPromptField)
            field.load_text("original private draft")
            activate(dialog)
            await settled(pilot, lambda: bool(notices))
            assert app.screen is dialog and outcomes == []
            assert field.text == "original private draft"
            assert "prompt history" in notices[0] and "original private draft" not in notices[0]
    asyncio.run(run())


def test_separate_explicit_actions_get_separate_attempts(private_root):
    async def run():
        app, calls = App(), []
        async with app.run_test() as pilot:
            for expected in (1, 2):
                assert confirmation.confirm_before_effect(
                    app, "demo", {"action": "open", "is_local": True, "worktree_id": "wt-a"},
                    surface="new-window", saved=calls.append,
                )
                await settled(pilot, lambda: len(calls) == expected)
            assert calls[0]["attempt_id"] != calls[1]["attempt_id"]
            assert len(LaunchAttempts(private_root).entries()) == 2
    asyncio.run(run())


@pytest.mark.parametrize("option", ["Anchor repo", "Bare", "AHP", "Local model"])
def test_refused_confirmation_is_durable_but_keeps_original_composer(
    private_root, monkeypatch, option,
):
    async def run():
        dialog = ScopeDlgScreen({
            "opts": [{"label": option, "on": True}], "section": 1,
        }, show_prompt=True)
        app, outcomes, notices = App(), [], []
        monkeypatch.setattr(dialog, "notify", lambda text, **_: notices.append(text))
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            dialog.query_one(SeedPromptField).load_text("retained private text")
            activate(dialog)
            await settled(pilot, lambda: bool(notices))
            assert app.screen is dialog and outcomes == []
            entry = LaunchAttempts(private_root).entries()[0]
            detail = LaunchAttempts(private_root).detail(entry["attempt_id"], include_input=True)
            assert detail["input"]["prompt"] == "retained private text"
            assert [event["phase"] for event in detail["events"]] == ["confirmed", "refused"]
    asyncio.run(run())


@pytest.mark.parametrize("scope", [False, True])
def test_cancelled_draft_does_not_create_an_attempt(private_root, scope):
    async def run():
        dialog = (
            ScopeDlgScreen({"opts": []}, show_prompt=True)
            if scope else resume_dialog()
        )
        app, outcomes = App(), []
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            dialog.query_one(SeedPromptField).load_text("unconfirmed")
            dialog.action_cancel()
            await settled(pilot, lambda: bool(outcomes))
            assert outcomes == [False if scope else None]
            assert LaunchAttempts(private_root).entries() == []
            assert not LaunchAttempts(private_root).path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("scope", [False, True])
def test_mock_composer_confirmation_never_persists(private_root, monkeypatch, scope):
    from worktree_manager.seed_history import SeedHistory

    def forbidden(*_args, **_kwargs):
        pytest.fail("Mock confirmation attempted persistence")

    monkeypatch.setattr(LaunchAttempts, "record_intent", forbidden)
    monkeypatch.setattr(SeedHistory, "remember", forbidden)

    async def run():
        dialog = (
            ScopeDlgScreen({"opts": []}, show_prompt=True, journal_enabled=False)
            if scope else resume_dialog(enabled=False)
        )
        app, outcomes = App(), []
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            dialog.query_one(SeedPromptField).load_text("synthetic mock input")
            activate(dialog)
            await settled(pilot, lambda: bool(outcomes))
            assert outcomes == accepted(dialog)
            if scope:
                assert dialog.seed_prompt == "synthetic mock input"
                assert dialog.attempt_id is None
            else:
                assert dialog.launch_decision is None
            assert not LaunchAttempts(private_root).path.exists()
    asyncio.run(run())


def test_mock_resume_entry_forwards_the_persistence_boundary(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Mock inline Resume attempted durable confirmation")

    monkeypatch.setattr(confirmation, "confirm_before_effect", forbidden)
    assert resume_dialog(enabled=False)._confirm_prompt_resume("mock input") is False


def test_refused_inline_resume_is_durable_and_keeps_the_draft(private_root, monkeypatch):
    async def run():
        app, outcomes, notices = App(), [], []
        dialog = resume_dialog()
        monkeypatch.setattr(dialog, "notify", lambda text, **_: notices.append(text))
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            dialog._ahp = True
            field = dialog.query_one(SeedPromptField)
            field.load_text("retained private prompt")
            activate(dialog)
            await settled(pilot, lambda: bool(notices))
            assert app.screen is dialog and outcomes == []
            assert field.text == "retained private prompt"
            entry = LaunchAttempts(private_root).entries()[0]
            detail = LaunchAttempts(private_root).detail(entry["attempt_id"], include_input=True)
            assert detail["input"]["prompt"] == field.text
            assert [event["phase"] for event in detail["events"]] == ["confirmed", "refused"]
    asyncio.run(run())


@pytest.mark.parametrize("action,options,expected", [
    ("new", {}, "new"),
    ("resume", {}, "resume"),
    ("resume", {"bare_resume": True}, "bare-resume"),
    ("restore", {}, "restore"),
    ("open-venue", {}, "open-venue"),
])
def test_ordinary_decision_saves_empty_prompt_before_picker_exit(
    private_root, action, options, expected,
):
    class DecisionScreen(PickerScreenMaintenanceActionsMixin, Screen):
        real_ops = True

    async def run():
        app = App()
        owner = DecisionScreen()
        async with app.run_test() as pilot:
            await app.push_screen(owner)
            owner._decide({
                "action": action, "worktree_id": "wt-a", "is_local": False,
                "machine": "remote", "env": "WSL", "options": options,
                "provider": "test-provider" if action == "open-venue" else None,
                "venue": "test-venue" if action == "open-venue" else None,
            })
            await settled(pilot, lambda: getattr(app, "result", None) is not None)
        decision = app.result
        entry = LaunchAttempts(private_root).detail(decision["attempt_id"], include_input=True)
        assert entry["action"] == expected and entry["is_local"] is False
        assert entry["input"]["prompt"] == ""
        assert all(entry["input"]["options"][key] == value for key, value in options.items())
    asyncio.run(run())


def test_removed_confirmation_owner_never_launches(private_root, monkeypatch):
    async def run():
        entered, release = threading.Event(), threading.Event()
        record = LaunchAttempts.record_intent

        def blocked(store, attempt_id, **intent):
            entered.set()
            assert release.wait(10)
            record(store, attempt_id, **intent)

        monkeypatch.setattr(LaunchAttempts, "record_intent", blocked)
        app, calls = App(), []
        async with app.run_test() as pilot:
            owner = Screen()
            await app.push_screen(owner)
            confirmation.confirm_before_effect(
                owner, "demo", {"action": "new", "is_local": True},
                surface="test", saved=calls.append,
            )
            await settled(pilot, entered.is_set)
            saving = app.screen
            try:
                await app.pop_screen()
                await app.pop_screen()
            finally:
                release.set()
            await settled(pilot, lambda: not saving._threads)
            assert calls == []
            assert len(LaunchAttempts(private_root).entries()) == 1
    asyncio.run(run())


def test_new_decision_option_fidelity():
    labels = ["Anchor repo", "Bare", "No Mux", "AHP", "Local model"]
    result = confirmation.new_decision({
        "target": ("selected", "POSIX"),
        "opts": [{"label": label, "on": True} for label in labels],
    }, "normalized execution", is_local=False)
    assert result == {
        "action": "new", "machine": "selected", "env": "POSIX", "is_local": False,
        "options": dict.fromkeys(["anchor", "bare", "no_mux", "ahp", "local_model"], True)
        | {"seed_prompt": "normalized execution"},
    }


def unresolved_locality(*_args):
    raise RuntimeError("Locality snapshot is not ready.")


def refusal_screen():
    from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
        PickerScreenWorktreeActionsMixin,
    )

    class RefusalScreen(
        PickerScreenWorktreeActionsMixin, PickerScreenMaintenanceActionsMixin, Screen,
    ):
        real_ops = True
        src = SimpleNamespace(is_local=unresolved_locality, LOCAL=("observer", "Win"))
        debug = ""

        def _src_local(self):
            return self.src.LOCAL

        def _current_tab(self):
            return {}

        def _run_bg(self, *_args, **_kwargs):
            pytest.fail("a refused action must not embody, allocate or launch")

    return RefusalScreen()


@pytest.mark.parametrize("entry", ["resume", "open", "new-window", "task-open"])
def test_unresolved_confirmed_action_retains_target_options_and_refusal(
    private_root, monkeypatch, entry,
):
    from worktree_manager.production_picker.picker_tui.headed_actions import open_worktree_cli_headed

    async def run():
        app, notices = App(), []
        owner = refusal_screen()
        monkeypatch.setattr(owner, "notify", lambda text, **_: notices.append(text))
        row = {
            "raw": {"id": "selected-worktree"}, "id4": "tree", "title": "selected title",
            "machine": "selected-host", "env": "POSIX", "mux_live": entry == "open",
        }
        async with app.run_test() as pilot:
            await app.push_screen(owner)
            if entry == "task-open":
                ok, _ = owner._embody_task_cli({
                    "task_id": "selected-task", "machine": row["machine"], "env": row["env"],
                    "title": row["title"], "source_kind": "machine-ssh",
                })
                assert ok is False
            elif entry == "new-window":
                open_worktree_cli_headed(owner, row, no_mux=True, ahp=True)
            else:
                assert owner._decide(owner._resume_decision(
                    row, no_mux=True, ahp=True,
                )) is False
            await settled(pilot, lambda: bool(notices))
            assert app.screen is owner and getattr(app, "result", None) is None
            entries = LaunchAttempts(private_root).entries()
            assert len(entries) == 1 and entries[0]["is_local"] is None
            detail = LaunchAttempts(private_root).detail(entries[0]["attempt_id"], include_input=True)
            assert detail["machine"] == row["machine"] and detail["environment"] == row["env"]
            assert detail["action"] == {
                "new-window": "resume", "task-open": "open",
            }.get(entry, entry)
            assert detail["surface"] == (
                entry if entry in {"new-window", "task-open"} else "picker-action"
            )
            assert detail["input"]["prompt"] == ""
            assert detail["input"]["options"]["title"] == row["title"]
            if entry == "task-open":
                assert detail["input"]["options"]["task_id"] == "selected-task"
            else:
                assert detail["worktree_id"] == "selected-worktree"
                assert detail["input"]["options"]["no_mux"] is True
                assert detail["input"]["options"]["ahp"] is True
            if entry == "new-window":
                assert detail["input"]["options"]["new_window"] is True
            assert [event["phase"] for event in detail["events"]] == ["confirmed", "refused"]
            assert detail["events"][-1]["details"] == {"code": "locality-unresolved"}
            assert notices == ["Locality snapshot is not ready."]
    asyncio.run(run())


@pytest.mark.parametrize("entry", ["resume", "new-window", "task-open"])
def test_mock_unresolved_action_never_creates_journal(private_root, monkeypatch, entry):
    from worktree_manager.production_picker.picker_tui.headed_actions import open_worktree_cli_headed

    async def run():
        app = App()
        owner = refusal_screen()
        owner.real_ops = False
        row = {"raw": {"id": "selected-worktree"}, "machine": "selected-host", "env": "POSIX"}
        monkeypatch.setattr(
            LaunchAttempts, "record_intent",
            lambda *_args, **_kwargs: pytest.fail("mock action attempted persistence"),
        )
        async with app.run_test():
            await app.push_screen(owner)
            if entry == "new-window":
                open_worktree_cli_headed(owner, row, no_mux=True)
            elif entry == "task-open":
                assert owner._embody_task_cli({
                    "task_id": "selected-task", "machine": row["machine"], "env": row["env"],
                })[0] is False
            else:
                assert owner._decide(owner._resume_decision(row)) is False
            assert app.screen is owner and getattr(app, "result", None) is None
            assert not LaunchAttempts(private_root).path.exists()
    asyncio.run(run())


def test_opening_unavailable_resume_prompt_is_not_a_confirmed_launch(private_root, monkeypatch):
    async def run():
        app, notices = App(), []
        dialog = resume_dialog()
        dialog._rec["is_local"] = False
        monkeypatch.setattr(dialog, "notify", lambda text, **_: notices.append(text))
        async with app.run_test():
            await app.push_screen(dialog)
            assert not dialog.query_one("#sub-prompt").display
            assert notices == [] and not LaunchAttempts(private_root).path.exists()
    asyncio.run(run())


def test_deferred_continuation_never_creates_a_replacement_intent(private_root):
    async def run():
        app = App()
        owner = refusal_screen()
        store = LaunchAttempts(private_root)
        attempt_id = "a" * 32
        store.record_intent(
            attempt_id, action="open", project="demo", surface="task-open", is_local=True,
        )
        original = store.detail(attempt_id, include_input=True)
        async with app.run_test():
            await app.push_screen(owner)
            decision = owner._resume_decision({"raw": {"id": "allocated-worktree"}})
            decision["attempt_id"] = attempt_id
            assert owner._decide(decision) is False
            assert app.screen is owner and getattr(app, "result", None) is None
            assert len(store.entries()) == 1
            assert store.detail(attempt_id, include_input=True) == original
    asyncio.run(run())


def test_unresolved_refusal_storage_error_does_not_exit_or_release_effects(
    private_root, monkeypatch,
):
    async def run():
        app, notices = App(), []
        owner = refusal_screen()
        monkeypatch.setattr(owner, "notify", lambda text, **_: notices.append(text))

        def fail(*_args, **_kwargs):
            raise LaunchAttemptError("private diagnostic must not escape")

        monkeypatch.setattr(LaunchAttempts, "record_intent", fail)
        async with app.run_test() as pilot:
            await app.push_screen(owner)
            decision = owner._resume_decision({
                "raw": {"id": "selected-worktree"}, "machine": "selected-host", "env": "POSIX",
            }, no_mux=True)
            assert owner._decide(decision) is False
            await settled(pilot, lambda: bool(notices))
            assert app.screen is owner and getattr(app, "result", None) is None
            assert notices == [
                "Could not save launch confirmation. Your draft is unchanged; no launch started.",
            ]
            assert owner._launch_confirmation.decision["options"] == {"no_mux": True}
            assert not LaunchAttempts(private_root).path.exists()
    asyncio.run(run())
