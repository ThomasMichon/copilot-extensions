from __future__ import annotations

import asyncio
import os
import stat
import shutil
import subprocess
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.app import App
from agent_procutil import no_window_kwargs

from worktree_manager import seed_history
from worktree_manager.engine_client import EngineError, EngineFeatureUnavailable
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.engine_dialogs import ScopeDlgScreen
from worktree_manager.production_picker.picker_tui.engine_focus import FocusGroup
from worktree_manager.production_picker.picker_tui.engine_prompt_dialog import PromptDlgScreen
from worktree_manager.production_picker.picker_tui.seed_prompt_field import SeedPromptField


def test_history_persists_exact_multiline_text_without_launch(tmp_path):
    history = seed_history.SeedHistory("demo", tmp_path)
    assert history.entries() == []
    prompt = "  paste with 'quotes' and \"double quotes\"\nsecond line\n"
    history.remember(prompt)
    history.remember(prompt)
    history.remember(" \n ")
    assert seed_history.SeedHistory("demo", tmp_path).entries() == [prompt]
    assert seed_history.SeedHistory("other", tmp_path).entries() == []


def test_concurrent_submissions_are_not_lost(tmp_path):
    def save(n):
        seed_history.SeedHistory("demo", tmp_path).remember(f"prompt {n}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(12)))
    assert set(seed_history.SeedHistory("demo", tmp_path).entries()) == {
        f"prompt {n}" for n in range(12)
    }


def test_actual_history_connection_ddl_does_not_start_implicit_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = sqlite3.connect
    observed: list[tuple[str, bool, bool, str | None]] = []

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=(), /) -> sqlite3.Cursor:
            before = self.in_transaction
            cursor = super().execute(sql, parameters)
            observed.append((sql.split()[0], before, self.in_transaction, self.isolation_level))
            return cursor

    def observed_connect(*args, **kwargs) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=ObservedConnection)

    monkeypatch.setattr(seed_history.sqlite3, "connect", observed_connect)
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("first")
    history.remember("second")
    assert history.entries() == ["second", "first"]
    assert [row for row in observed if row[0] == "CREATE"] == [
        ("CREATE", False, False, ""), ("CREATE", False, False, ""),
    ]
    assert [row for row in observed if row[0] == "BEGIN"] == [
        ("BEGIN", False, True, ""), ("BEGIN", False, True, ""),
    ]
    assert [row for row in observed if row[0] == "INSERT"] == [
        ("INSERT", True, True, ""), ("INSERT", True, True, ""),
    ]


def test_history_storage_failure_is_explicit(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("occupied")
    with pytest.raises(seed_history.SeedHistoryError, match="not been submitted"):
        seed_history.SeedHistory("demo", root).remember("private prompt")
    assert root.read_text() == "occupied"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_history_repairs_existing_permissions(tmp_path):
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("private")
    assert stat.S_IMODE(history.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(history.path.parent.stat().st_mode) == 0o700
    history.path.chmod(0o644)
    history.path.parent.chmod(0o755)
    assert history.entries() == ["private"]
    assert stat.S_IMODE(history.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(history.path.parent.stat().st_mode) == 0o700


def _acl_powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("Neither PowerShell 7 nor Windows PowerShell is available for ACL inspection")
    return executable


@pytest.mark.parametrize("available,expected", [
    ({"pwsh": "pwsh.exe", "powershell": "powershell.exe"}, "pwsh.exe"),
    ({"powershell": "powershell.exe"}, "powershell.exe"),
    ({}, None),
])
def test_acl_shell_selection_and_explicit_missing_shell_skip(
    monkeypatch: pytest.MonkeyPatch, available: dict[str, str], expected: str | None,
) -> None:
    monkeypatch.setattr(shutil, "which", available.get)
    if expected is None:
        with pytest.raises(pytest.skip.Exception, match="ACL inspection"):
            _acl_powershell()
    else:
        assert _acl_powershell() == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
@pytest.mark.parametrize("legacy_only", [False, True])
def test_windows_history_has_protected_owner_system_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_only: bool,
) -> None:
    if legacy_only:
        which = shutil.which
        monkeypatch.setattr(shutil, "which", lambda name: which(name) if name != "pwsh" else None)
    shell = _acl_powershell()
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("private")
    for path in (history.path.parent, history.path):
        # An inherited PSModulePath can select another PowerShell edition's module.
        script = (
            "$ErrorActionPreference='Stop';"
            "Import-Module (Join-Path $PSHOME "
            "'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');"
            "$acl=Get-Acl -LiteralPath '" + str(path).replace("'", "''") + "';"
            "$acl.AreAccessRulesProtected;"
            "$acl.Access | ForEach-Object {"
            "$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value}"
        )
        result = subprocess.run(
            [shell, "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )
        if result.returncode or set(result.stdout.split()) != {"True", "S-1-3-4", "S-1-5-18"}:
            pytest.fail(f"ACL inspection with {shell} failed:\n{result.stderr}", pytrace=False)


def test_permission_failure_blocks_submission(tmp_path, monkeypatch):
    def denied(_path, **_kwargs):
        raise OSError("Denied")
    monkeypatch.setattr(seed_history, "protect", denied)
    with pytest.raises(seed_history.SeedHistoryError, match="not been submitted"):
        seed_history.SeedHistory("demo", tmp_path).remember("private")
    assert not (tmp_path / "seed-history" / "history.sqlite3").exists()


@pytest.mark.parametrize("link_kind", ["database", "directory", "ancestor"])
def test_dangling_links_refuse_reads_and_writes_without_creating_target(tmp_path, link_kind):
    history = seed_history.SeedHistory("demo", tmp_path / "manager")
    target = tmp_path / "must-not-be-created"
    link = {
        "database": history.path,
        "directory": history.path.parent,
        "ancestor": history.path.parent.parent,
    }[link_kind]
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=link_kind != "database")
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation privilege unavailable")
        raise
    with pytest.raises(seed_history.SeedHistoryError, match="read"):
        history.entries()
    with pytest.raises(seed_history.SeedHistoryError, match="not been submitted"):
        history.remember("private prompt")
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
@pytest.mark.parametrize("dangling", [False, True])
def test_windows_junction_is_rejected_before_history_write(tmp_path, dangling):
    history = seed_history.SeedHistory("demo", tmp_path / "manager")
    target = tmp_path / "target"
    if not dangling:
        target.mkdir()
    history.path.parent.parent.mkdir()
    proc = subprocess.run(
        [os.environ["COMSPEC"], "/c", "mklink", "/J", str(history.path.parent), str(target)],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert proc.returncode == 0, proc.stderr
    with pytest.raises(seed_history.SeedHistoryError):
        history.entries()
    with pytest.raises(seed_history.SeedHistoryError):
        history.remember("private")
    assert target.exists() is not dangling
    if not dangling:
        assert list(target.iterdir()) == []


def test_history_cursor_restores_draft_and_edits():
    cursor = seed_history.HistoryCursor(["newest", "older"])
    assert cursor.move("unsent draft", 1) == "newest"
    assert cursor.move("edited newest", 1) == "older"
    assert cursor.move("older", 1) is None
    assert cursor.move("older", -1) == "edited newest"
    assert cursor.move("edited newest", -1) == "unsent draft"
    assert cursor.move("unsent draft", -1) is None


@pytest.mark.parametrize("scope", [False, True])
def test_submitted_prompt_survives_failed_launch_and_new_composer(tmp_path, monkeypatch, scope):
    asyncio.run(_submitted_prompt_survives_failed_launch(tmp_path, monkeypatch, scope))


async def _submitted_prompt_survives_failed_launch(tmp_path, monkeypatch, scope):
    from worktree_manager import __main__ as entrypoint, engine_client
    from worktree_manager.picker_app import LaunchRequest

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("demo")
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("older seed")
    dialog = (
        ScopeDlgScreen({"opts": [], "section": 1}, show_prompt=True)
        if scope else PromptDlgScreen("Resume", "Prompt")
    )
    outcomes = []
    def refuse(*_args, **_kwargs):
        raise EngineError("could not resolve a launch plan: timed out")
    monkeypatch.setattr(engine_client, "resolve_launch_plan", refuse)
    monkeypatch.setattr(engine_client, "execution_leg_get", lambda *_: {"execution_leg": None})

    def failed_launch(confirmed):
        assert history.entries()[0] == "valuable\npaste"
        outcomes.append(confirmed)
        assert entrypoint._run_launch(LaunchRequest(
            project="demo", mode="new" if scope else "resume",
            worktree_id=None if scope else "wt-a", seed_prompt=dialog.seed_prompt,
        )) == 1

    app = App()
    async with app.run_test() as pilot:
        app.push_screen(dialog, failed_launch)
        await pilot.pause()
        field = dialog.query_one(SeedPromptField)
        field.load_text("valuable\npaste")
        dialog.on_focus_group_activated(
            FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm")
        )
        await pilot.pause()
        assert outcomes == [True]
        retry = PromptDlgScreen("Retry", "Prompt")
        app.push_screen(retry)
        await pilot.pause()
        await pilot.press("up")
        assert retry.query_one(SeedPromptField).text == "valuable\npaste"
        await pilot.press("up")
        assert retry.query_one(SeedPromptField).text == "older seed"
        await pilot.press("down", "down")
        assert retry.query_one(SeedPromptField).text == ""


@pytest.mark.parametrize("option", ["Anchor repo", "Bare", "AHP", "Local model", "resume-ahp"])
def test_unsupported_composer_keeps_text_without_recording(tmp_path, monkeypatch, option):
    async def run():
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
        context.set_project("demo")
        dialog = (
            PromptDlgScreen("Resume", "Prompt", seed_refusal="AHP prompt launch is unsupported")
            if option == "resume-ahp" else ScopeDlgScreen(
                {"opts": [{"label": option, "on": True}], "section": 1},
                show_prompt=True,
            )
        )
        outcomes = []
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(dialog, outcomes.append)
            await pilot.pause()
            field = dialog.query_one(SeedPromptField)
            field.load_text("unsubmitted draft")
            dialog.on_focus_group_activated(
                FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm")
            )
            await pilot.pause()
            assert app.screen is dialog
            assert field.text == "unsubmitted draft" and outcomes == []
            assert seed_history.SeedHistory("demo", tmp_path).entries() == []
    asyncio.run(run())


def test_remote_resume_refuses_before_composer_or_history_opens(monkeypatch):
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui import resume_prompt_actions

    monkeypatch.setattr(
        resume_prompt_actions, "PromptDlgScreen",
        lambda *args, **kwargs: pytest.fail("unsupported target opened prompt history"),
    )
    record = {"is_local": False, "raw": {"id": "wt-a"}}
    screen = SimpleNamespace(
        debug="", _find_internal_worktree=lambda *args: (record, None),
        _session_action_verbs=lambda _: ["Resume prompt…"],
    )
    resume_prompt_actions.open_resume_prompt(screen, record)
    assert "local native target" in screen.debug


@pytest.mark.parametrize("leg,ahp,reason", [
    ({"provider": "custom", "state": "active"}, False, "execution leg is active"),
    ({"provider": "custom", "state": "unknown"}, False, "execution leg is unknown"),
    (None, True, "unsupported with AHP"),
])
def test_resume_refusal_reports_actual_transport_without_fake_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leg: dict[str, str] | None,
    ahp: bool, reason: str,
) -> None:
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui.resume_prompt_actions import open_resume_prompt

    async def run() -> None:
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
        context.set_project("demo")
        record = {"raw": {"id": "wt-a", "execution_leg": leg}, "title": "Demo"}
        outcomes = []
        app = App()
        screen = SimpleNamespace(
            app=app, debug="",
            _find_internal_worktree=lambda *args: (record, None),
            _session_action_verbs=lambda _: ["Resume prompt…"],
            _resume_decision=lambda *args, **kwargs: outcomes.append(kwargs),
            _decide=lambda _: None,
        )
        async with app.run_test() as pilot:
            open_resume_prompt(screen, record, ahp=ahp)
            await pilot.pause()
            dialog = app.screen
            field = dialog.query_one(SeedPromptField)
            notices = []
            monkeypatch.setattr(field, "notify", lambda message, **kwargs: notices.append(message))
            field.load_text("retained draft")
            dialog.on_focus_group_activated(
                FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm")
            )
            await pilot.pause()
            assert app.screen is dialog and field.text == "retained draft"
            assert len(notices) == 1 and reason in notices[0]
            if not ahp:
                assert "AHP" not in notices[0]
            assert outcomes == []
            assert seed_history.SeedHistory("demo", tmp_path).entries() == []
            field.load_text("")
            dialog.on_focus_group_activated(
                FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm")
            )
            await pilot.pause()
            assert outcomes == [{"no_mux": False, "ahp": ahp, "seed_prompt": ""}]
    asyncio.run(run())


@pytest.mark.parametrize("options,leg", [
    ({"mode": "base"}, None),
    ({"mode": "bare-resume"}, None),
    ({"mode": "new", "ahp": True}, None),
    ({"mode": "resume", "machine": "remote"}, None),
    ({"mode": "resume", "environment": "WSL"}, None),
    ({"mode": "resume"}, {"provider": "ahp", "state": "active"}),
    ({"mode": "resume"}, {"provider": "ahp", "state": "unknown"}),
])
def test_unsupported_launch_never_requests_engine_staging(monkeypatch, options, leg):
    from worktree_manager import __main__ as entrypoint, engine_client
    from worktree_manager.picker_app import LaunchRequest

    calls = []
    monkeypatch.setattr(engine_client, "execution_leg_get", lambda *_: {"execution_leg": leg})
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: calls.append(kw))
    req = LaunchRequest(
        **{"project": "demo", "worktree_id": "wt-a", "seed_prompt": "private", **options},
    )
    assert entrypoint._run_launch(req) == 1
    assert calls == []


@pytest.mark.parametrize("target", [None, ""])
@pytest.mark.parametrize("no_mux", [False, True])
def test_prompted_resume_missing_id_refuses_before_history_or_engine_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str | None, no_mux: bool,
) -> None:
    from worktree_manager import __main__ as entrypoint, engine_client, seed_launch
    from worktree_manager.picker_app import LaunchRequest

    def forbidden(*args, **kwargs):
        pytest.fail("invalid seeded resume must refuse before history, leg lookup, or resolve")
    monkeypatch.setattr(seed_history.SeedHistory, "remember", forbidden)
    monkeypatch.setattr(engine_client, "execution_leg_get", forbidden)
    monkeypatch.setattr(engine_client, "resolve_launch_plan", forbidden)
    request = LaunchRequest(
        project="demo", worktree_id=target, mode="resume", seed_prompt="draft", no_mux=no_mux,
    )
    with pytest.raises(seed_launch.UnsupportedSeedPrompt, match="worktree id"):
        seed_launch.require_supported_seed(request)
    assert entrypoint._run_launch(request) == 1


@pytest.mark.parametrize("prompt", [None, ""])
def test_missing_resume_id_seedless_guard_retains_existing_behavior(
    monkeypatch: pytest.MonkeyPatch, prompt: str | None,
) -> None:
    from worktree_manager import engine_client, seed_launch
    from worktree_manager.picker_app import LaunchRequest

    monkeypatch.setattr(engine_client, "execution_leg_get", lambda *a: pytest.fail("unexpected probe"))
    seed_launch.require_supported_seed(LaunchRequest(
        project="demo", worktree_id=None, mode="resume", seed_prompt=prompt,
    ))


@pytest.mark.parametrize("target", [None, ""])
def test_missing_resume_id_ui_refuses_before_history_composer(
    monkeypatch: pytest.MonkeyPatch, target: str | None,
) -> None:
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
        PickerScreenWorktreeActionsMixin,
    )
    from worktree_manager.production_picker.picker_tui.resume_prompt_actions import open_resume_prompt

    def forbidden(*args, **kwargs):
        pytest.fail("missing resume target must not open or read/write a history composer")
    monkeypatch.setattr(seed_history.SeedHistory, "entries", forbidden)
    monkeypatch.setattr(seed_history.SeedHistory, "remember", forbidden)
    record = {"raw": {"id": target}}
    screen = SimpleNamespace(
        data=[record], debug="", app=SimpleNamespace(push_screen=forbidden),
        _session_action_verbs=forbidden,
    )
    screen._find_internal_worktree = (
        lambda *args: PickerScreenWorktreeActionsMixin._find_internal_worktree(screen, *args)
    )
    open_resume_prompt(screen, record)
    assert "cold-start-only" in screen.debug


def test_new_no_mux_seed_keeps_raw_whitespace_and_requires_no_resume_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import engine_client, seed_launch
    from worktree_manager.picker_app import LaunchRequest

    monkeypatch.setattr(engine_client, "execution_leg_get", lambda *a: pytest.fail("unexpected probe"))
    request = LaunchRequest(
        project="demo", worktree_id=None, mode="new", seed_prompt=" \n ", no_mux=True,
    )
    seed_launch.require_supported_seed(request)
    assert request.seed_prompt == " \n " and request.no_mux


@pytest.mark.parametrize("error_type", [EngineError, EngineFeatureUnavailable])
def test_execution_leg_probe_failure_precedes_staging(monkeypatch, error_type):
    from worktree_manager import __main__ as entrypoint, engine_client
    from worktree_manager.picker_app import LaunchRequest

    def refuse(*_args):
        raise error_type("execution leg unavailable")
    calls = []
    monkeypatch.setattr(engine_client, "execution_leg_get", refuse)
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: calls.append(kw))
    assert entrypoint._run_launch(LaunchRequest(
        project="demo", worktree_id="wt-a", mode="resume", seed_prompt="draft",
    )) == 1
    assert calls == []


def test_failed_history_save_keeps_composer_open(tmp_path, monkeypatch):
    asyncio.run(_failed_history_save_keeps_composer_open(tmp_path, monkeypatch))


async def _failed_history_save_keeps_composer_open(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("demo")
    app = App()
    dialog = PromptDlgScreen("Resume", "Prompt")
    outcomes = []
    async with app.run_test() as pilot:
        app.push_screen(dialog, outcomes.append)
        await pilot.pause()
        field = dialog.query_one(SeedPromptField)
        field.load_text("do not lose this")
        def fail(_prompt):
            raise seed_history.SeedHistoryError("Cannot save history")
        monkeypatch.setattr(field.seed_store, "remember", fail)
        dialog.on_focus_group_activated(
            FocusGroup.Activated(dialog.query_one(FocusGroup), 0, "confirm")
        )
        await pilot.pause()
        assert app.screen is dialog
        assert field.text == "do not lose this"
        assert outcomes == []


def test_cancel_does_not_record_and_multiline_cursor_still_moves(tmp_path, monkeypatch):
    asyncio.run(_cancel_and_multiline_cursor(tmp_path, monkeypatch))


async def _cancel_and_multiline_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("demo")
    history = seed_history.SeedHistory("demo", tmp_path)
    history.remember("previous")
    app = App()
    dialog = PromptDlgScreen("Resume", "Prompt")
    async with app.run_test() as pilot:
        app.push_screen(dialog)
        await pilot.pause()
        field = dialog.query_one(SeedPromptField)
        field.load_text("first\nsecond\nthird")
        field.move_cursor((1, 2))
        await pilot.press("up")
        assert field.text == "first\nsecond\nthird"
        assert field.cursor_location[0] == 0
        await pilot.press("ctrl+up")
        assert field.text == "previous"
        await pilot.press("ctrl+down")
        assert field.text == "first\nsecond\nthird"
        dialog.action_cancel()
        await pilot.pause()
        assert history.entries() == ["previous"]


def test_recalled_multiline_seed_keeps_plain_arrow_editing(tmp_path, monkeypatch):
    asyncio.run(_recalled_multiline_seed(tmp_path, monkeypatch))


async def _recalled_multiline_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("demo")
    seed_history.SeedHistory("demo", tmp_path).remember("one\ntwo\nthree")
    app = App()
    dialog = PromptDlgScreen("Resume", "Prompt")
    async with app.run_test() as pilot:
        app.push_screen(dialog)
        await pilot.pause()
        await pilot.press("up")
        field = dialog.query_one(SeedPromptField)
        field.move_cursor((1, 1))
        await pilot.press("up")
        assert field.cursor_location[0] == 0
        assert field.text == "one\ntwo\nthree"


@pytest.fixture(autouse=True)
def reset_project_context():
    yield
    context.reset()
