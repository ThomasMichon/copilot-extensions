"""Worktree status-core write behavior."""

from __future__ import annotations

import argparse

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import tracking, worktree_identity


@pytest.mark.parametrize("raw,expected", [
    ("\n" + "x" * 512 + "\t", "x" * 512),
    ("\x07  " + "R\u00e9sum\u00e9 phase " * 12 + "\nconsumer  ",
     "R\u00e9sum\u00e9 phase " * 12 + " consumer"),
    ("1234567890" * 6, "1234567890" * 6),
    ("x" * 40 + "\u0085history-suffix", "x" * 40 + "\u0085history-suffix"),
    ("x" * 40 + "\u2028history-suffix", "x" * 40 + "\u2028history-suffix"),
    ("x" * 40 + "\u2029history-suffix", "x" * 40 + "\u2029history-suffix"),
], ids=["normalized-boundary", "unicode-controls", "numeric", "next-line", "line-separator", "paragraph-separator"])
def test_full_title_survives_ordinary_writer_history_export_and_short_display(
    status_env, capfd, raw, expected,
):
    import json
    from agent_worktrees import disposition_history, effort_focus, sessions, status_bar_cli

    record = tracking.load_record(status_env)
    record.codename = "fixture-title"
    record.active_effort = effort_focus.make_active_effort(
        "efforts/active/example/README.md", "Driver", "Declared slice",
    )
    binding = record.active_effort
    tracking.save_record(record, status_env)
    disposition_history.append(
        record.worktree_id, at="old", title="old capped\u2026", summary="old",
        follow_up=False, changed=["title"], tracking_path=status_env.parent,
    )
    assert main._cmd_status_write(
        argparse.Namespace(worktree_id=None), summary=None, title=raw,
    ) == 0
    loaded = tracking.load_record(status_env)
    assert loaded.title == expected and loaded.title_asserted
    assert tracking._load_record_uncached(status_env).title == expected
    assert loaded.active_effort == binding and loaded.status == record.status
    history = disposition_history.read(loaded.worktree_id, tracking_path=status_env.parent)
    assert [entry["title"] for entry in history] == ["old capped\u2026", expected]
    assert main._worktree_to_dict(loaded, session_ctx=sessions.SessionContext())["title"] == expected
    compact = status_bar_cli._resolve_segment_title(
        loaded, loaded.worktree_path,
        main.git_ops.WorktreeStateInfo(state=main.git_ops.WorktreeState.UNUSED),
    )
    assert len(compact) <= 48 and compact.endswith("\u2026")
    assert tracking.load_record(status_env).title == expected
    capfd.readouterr()
    assert main._cmd_status_history(
        argparse.Namespace(worktree_id=None, limit=None, json=True),
    ) == 0
    assert json.loads(capfd.readouterr().out)["history"][-1]["title"] == expected


def test_over_bound_title_rejects_before_any_status_side_effect(status_env, capfd):
    from agent_worktrees import disposition_history

    before = status_env.read_bytes()
    assert main._cmd_status_write(
        argparse.Namespace(worktree_id=None), title="x" * 513,
        summary="must not change", follow_up=True, paused=True,
    ) == 1
    assert "512" in "".join(capfd.readouterr())
    assert status_env.read_bytes() == before
    assert disposition_history.read("wt-status", tracking_path=status_env.parent) == []


def test_full_title_caller_selects_the_qualified_receiver(status_env, monkeypatch):
    from agent_worktrees import tracking_write

    seen = []
    real_dispatch = tracking_write.dispatch

    def dispatch(verb, payload, **kwargs):
        seen.append((payload.get("retain_full_title"), kwargs.get("min_version")))
        return real_dispatch(verb, payload, **kwargs)

    monkeypatch.setattr(tracking_write, "dispatch", dispatch)
    assert main._cmd_status_write(
        argparse.Namespace(worktree_id=None), summary=None, title="full " * 20,
    ) == 0
    assert seen == [(True, 3)]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("title_only", [False, True])
def test_legacy_title_entry_retains_full_current_and_new_history(
    status_env, monkeypatch, existing, title_only,
):
    from agent_worktrees import disposition_history, finalize_cli

    if not existing:
        status_env.unlink()
    monkeypatch.setattr(finalize_cli.git_ops, "get_current_branch", lambda *_: "fixture-branch")
    monkeypatch.setattr(finalize_cli.cfg, "project_name", lambda: "example")
    title = "Identify the complete title-only source flow and retained history"
    assert finalize_cli.cmd_mark_complete(argparse.Namespace(
        worktree_id=None, title=title, title_only=title_only,
    )) == 0
    record = tracking.load_record(status_env)
    assert record.title == title and record.title_asserted
    assert record.status == (
        ("finalized" if existing else "active") if title_only else "complete"
    )
    assert [entry["title"] for entry in disposition_history.read(
        record.worktree_id, tracking_path=status_env.parent,
    )] == [title]


@pytest.mark.parametrize("command", ["mark-complete", "push-changes"])
def test_title_only_over_bound_refusal_has_no_record_or_history_effect(status_env, command):
    from agent_worktrees import disposition_history, finalize_cli

    args = main.build_parser().parse_args([command, "--title-only", "--title", "x" * 513])
    before = status_env.read_bytes()
    handler = finalize_cli.cmd_mark_complete if command == "mark-complete" else finalize_cli.cmd_push_changes
    assert handler(args) == 1
    assert status_env.read_bytes() == before
    assert disposition_history.read("wt-status", tracking_path=status_env.parent) == []


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_fresh_profile_cli_retains_full_title_in_record_history_and_list(tmp_path):
    import json
    import os
    import sys
    from native_shell_support import run_headless

    home = tmp_path / "home"
    project = "title-fixture"
    root = home / f".{project}"
    records = root / "worktrees"
    records.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (root / "config.yaml").write_text(json.dumps({
        "repo_name": project, "srcroot": str(tmp_path), "machine": "fixture",
        "repos": {project: {"anchor": str(checkout), "worktree_root": str(tmp_path),
                            "remote": "", "default_branch": "main"}},
    }), encoding="utf-8")
    path = records / "fixture-title.yaml"
    record = tracking.WorktreeRecord(
        worktree_id="fixture-title", branch="worktree/fixture-title",
        worktree_path=str(checkout), repo=project, machine="fixture", platform=main.cfg.detect_platform(),
        started_at="", last_resumed_at="", resume_count=0, title="old", status="active",
        codename="fixture-title", completed_at=None,
    )
    tracking.save_record(record, path)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "COPILOT_HOME": str(home / ".copilot"), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8", "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "AGENT_WORKTREES_NO_SELFPROVISION": "1", "WORKTREE_NO_UPDATE": "1",
        **{f"XDG_{kind}_HOME": str(home / kind.lower()) for kind in ("CONFIG", "DATA", "CACHE", "STATE")},
    }
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID"):
        env.pop(key, None)

    def invoke(*args, expected=0):
        result = run_headless(
            [sys.executable, "-m", "agent_worktrees", "--project", project, *args],
            cwd=checkout, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == expected, (result.stdout, result.stderr)
        assert "Traceback" not in result.stderr and "::agent-provisioning::" not in result.stderr
        return result.stdout if expected == 0 else result.stdout + result.stderr

    title = "Full identifying title for an independent fresh-profile source consumer " * 3
    invoke("status", "--worktree-id", record.worktree_id, "--title", title)
    assert tracking.load_record(path).title == title.strip()
    history = json.loads(invoke(
        "status", "--worktree-id", record.worktree_id, "--history", "--json",
    ))
    assert [entry["title"] for entry in history["history"]] == [title.strip()]
    listing = json.loads(invoke("list", "--all", "--json"))
    assert next(row for row in listing["worktrees"] if row["id"] == record.worktree_id)["title"] == title.strip()
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    assert "512" in invoke(
        "status", "--worktree-id", record.worktree_id, "--title", "x" * 513,
        "--summary", "must not change", "--follow-up", expected=1,
    )
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert not list(home.rglob("*status-monitor*"))


@pytest.mark.parametrize("json_output", [False, True])
def test_maintenance_refusal_preserves_status_and_normal_output(
    status_env, monkeypatch, capfd, json_output,
):
    import json
    from agent_worktrees import tracking_write

    record = tracking.load_record(status_env)
    record.codename = "fixture-status"
    tracking.save_record(record, status_env)
    before = status_env.read_bytes()
    def held(*args, **kwargs):
        raise tracking_write.ResidentRecoveryHeld("active maintenance")
    monkeypatch.setattr(tracking_write, "dispatch", held)
    assert main._cmd_status_write(
        argparse.Namespace(worktree_id=None, json=json_output),
        summary="Must not persist", follow_up=True,
    ) == 1
    captured = capfd.readouterr()
    assert "maintenance" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    if json_output:
        assert "maintenance" in json.loads(captured.out)["error"]
    assert status_env.read_bytes() == before


def test_status_write_republishes_agenda_policy(status_env, monkeypatch):
    from agent_worktrees import session_context, tracking_write

    published = []
    monkeypatch.setattr(
        session_context,
        "publish_agenda_policy",
        lambda config, record: published.append(record.worktree_id),
    )
    def held(*args, **kwargs):
        raise tracking_write.ResidentRecoveryHeld("active maintenance")
    monkeypatch.setattr(tracking_write, "dispatch", held)
    main._cmd_status_write(
        argparse.Namespace(worktree_id=None, json=False), summary=None, activity="focus",
    )
    assert published == ["wt-status"]



def test_running_session_sees_enabled_agenda_default_after_status_write(
    status_env, monkeypatch, tmp_path,
):
    """An operator enabling ``agendas.default`` mid-session reaches the
    reminder hook through the next disposition write, without a restart."""
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from agent_worktrees import session_context

    spec = importlib.util.spec_from_file_location(
        "nudge_status_running", Path(__file__).resolve().parents[1] / "scripts" / "nudge_status.py",
    )
    nudge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nudge)
    record = tracking.load_record(status_env)
    record.status, record.completed_at = "active", None
    tracking.save_record(record, status_env)
    agent_home = tmp_path / "agent-home"
    runtime = agent_home / ".agent-worktrees"
    monkeypatch.setattr(session_context, "_agenda_policy_roots", lambda: [runtime])
    config = SimpleNamespace(agenda_default="off")
    monkeypatch.setattr(main.cfg, "load_config", lambda: config)
    env = {
        "AGENT_HOME": str(agent_home),
        "AGENT_WORKTREES_NUDGE_CALLS": "2", "AGENT_WORKTREES_NUDGE_MINUTES": "999",
    }

    def reminders(start):
        return [
            nudge.decide(
                {"toolName": "view", "cwd": str(tmp_path)}, env=env,
                home=tmp_path / "real-home", now=start + i,
                tracking_record=(record.worktree_id, status_env),
            )
            for i in range(2)
        ]

    session_context.publish_agenda_policy(config, record)
    assert not any("driving-effort-agendas" in (out or "") for out in reminders(1000.0))

    config.agenda_default = main.cfg.AGENDA_DEFAULT_EXPECTED
    assert main._cmd_status_write(
        argparse.Namespace(worktree_id=None), summary="Still working",
    ) == 0

    assert nudge.read_agenda_policy(runtime, record.worktree_id) == "expected"
    assert "driving-effort-agendas" in (reminders(2000.0)[-1] or "")

@pytest.fixture
def status_env(tmp_path, tmp_tracking_dir, monkeypatch):
    record = tracking.WorktreeRecord(
        worktree_id="wt-status",
        branch="worktree/wt-status",
        worktree_path=str(tmp_path),
        repo="example",
        machine="machine",
        platform="wsl",
        started_at="2026-08-28T00:00:00",
        last_resumed_at="2026-08-28T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-08-28T01:00:00",
        sessions=[],
    )
    path = tmp_tracking_dir / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    monkeypatch.setattr(main.cfg, "load_config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda: tmp_tracking_dir)
    monkeypatch.setattr(
        worktree_identity,
        "_infer_worktree_id",
        lambda _worktree_id, _config=None: record.worktree_id,
    )
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda worktree_id: worktree_id)
    return path


def test_follow_up_reactivates_finalized_worktree(status_env):
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Begin the next change",
        follow_up=True,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "active"
    assert record.completed_at is None
    assert record.follow_up is True
    assert record.summary == "Begin the next change"


def test_summary_only_preserves_finalized_status(status_env):
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Keep the existing disposition",
        follow_up=None,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "finalized"
    assert record.completed_at == "2026-08-28T01:00:00"


def test_activity_write_is_independent_of_summary_and_title(status_env):
    """#3307 worktrees-pivot-ux-overhaul follow-up: --activity writes the
    current-sub-task field without disturbing summary/title, and stamps its
    own activity_at."""
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Overall recap",
        activity=None,
        follow_up=None,
    ) == 0
    assert main._cmd_status_write(
        args,
        summary=None,
        activity="Running the retry-budget tests",
        follow_up=None,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.summary == "Overall recap"
    assert record.activity == "Running the retry-budget tests"
    assert record.activity_at is not None


def test_paused_does_not_reactivate_finalized_worktree(status_env):
    """Unlike `follow_up=True`, `paused=True` is purely informational and
    must never flip a finalized worktree back to active."""
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Leave this open on purpose",
        paused=True,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "finalized"
    assert record.completed_at == "2026-08-28T01:00:00"
    assert record.paused is True
    assert record.summary == "Leave this open on purpose"


def test_unpaused_prints_an_explicit_confirmation(status_env, capsys):
    """`--unpaused` must print an explicit confirmation that the flag was
    cleared, not just the unrelated follow-up flag (`resolved`) -- otherwise
    it is indistinguishable from a write that never touched `paused` at
    all."""
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(args, summary=None, paused=True) == 0
    capsys.readouterr()  # discard the --paused confirmation above

    assert main._cmd_status_write(args, summary=None, paused=False) == 0
    out = capsys.readouterr().out
    assert "(unpaused)" in out

    record = tracking.load_record(status_env)
    assert record.paused is False


def test_every_status_write_requires_verb_v2_even_without_paused(status_env, monkeypatch):
    """Every status write must avoid a resident v1 full-record writer, not
    only ones touching `paused`: a v1 daemon's old full-record writer
    silently drops `paused`/`paused_revision` (fields it predates), so a
    plain summary/title/follow-up write reaching that daemon during
    rolling skew would erase an already-persisted pause set moments
    earlier by a (correctly v2-gated) `--paused` write."""
    from agent_worktrees import tracking_write

    args = argparse.Namespace(worktree_id=None)
    captured_versions = []
    real_dispatch = tracking_write.dispatch

    def _spy_dispatch(verb, payload, **kwargs):
        captured_versions.append(kwargs.get("min_version"))
        return real_dispatch(verb, payload, **kwargs)

    monkeypatch.setattr(tracking_write, "dispatch", _spy_dispatch)

    assert main._cmd_status_write(args, summary="plain write", paused=None) == 0
    assert main._cmd_status_write(args, summary=None, paused=True) == 0

    assert captured_versions == [2, 2]


def test_status_history_disambiguates_pausing_from_unpausing(status_env, capsys):
    """`status --history`'s plain-text rendering must not show the same
    `(paused)` label for both a --paused and a --unpaused entry -- the
    `changed` field name alone can't distinguish set from clear."""
    args = argparse.Namespace(worktree_id=None)

    # summary=None on both so `changed` is exactly `["paused"]` on each
    # write -- isolates the assertion to the `paused` column alone.
    assert main._cmd_status_write(args, summary=None, paused=True) == 0
    assert main._cmd_status_write(args, summary=None, paused=False) == 0
    capsys.readouterr()  # discard both write confirmations above

    assert main._cmd_status_history(
        argparse.Namespace(worktree_id=None, limit=None, json=False),
    ) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    paused_glyph_lines = [line for line in lines if "(paused)" in line]
    assert len(paused_glyph_lines) == 2
    assert "\u23f8" in paused_glyph_lines[0]
    assert "\u23f8" not in paused_glyph_lines[1]


def test_first_write_in_session_emits_stage_5_status_reported(status_env, monkeypatch):
    """Stage 5 (status_reported): the first status-report write in a session
    marks "Copilot did something here"."""
    from agent_worktrees import activity

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-1")
    args = argparse.Namespace(worktree_id=None)

    main._cmd_status_write(args, summary="First summary")

    events = activity.read_events(worktree_id="wt-status", event="status_reported")
    assert len(events) == 1
    assert events[0]["stage"] == 5
    assert events[0]["stage_name"] == "status_reported"
    assert events[0]["session_id"] == "sess-1"


def test_second_write_in_same_session_does_not_duplicate_stage_5(
    status_env, monkeypatch,
):
    from agent_worktrees import activity

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-1")
    args = argparse.Namespace(worktree_id=None)

    main._cmd_status_write(args, summary="First summary")
    main._cmd_status_write(args, summary="Second summary")

    events = activity.read_events(worktree_id="wt-status", event="status_reported")
    assert len(events) == 1


def test_write_without_session_id_does_not_emit_stage_5(status_env, monkeypatch):
    from agent_worktrees import activity

    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    args = argparse.Namespace(worktree_id=None)

    main._cmd_status_write(args, summary="No session")

    assert activity.read_events(worktree_id="wt-status", event="status_reported") == []


def test_ambiguous_write_outcome_is_reported_not_swallowed(status_env, monkeypatch):
    """agent-worktrees-authoritative-daemon Phase 3: a write whose daemon
    request was sent and then failed is genuinely ambiguous (the mutation may
    already be committed server-side) -- `_cmd_status_write` must surface
    `AmbiguousWriteOutcome` as a reported command failure, never silently
    retry or swallow it (`tracking_write.dispatch`'s own contract)."""
    from agent_worktrees import tracking_write

    def _raise(*_args, **_kwargs):
        raise tracking_write.AmbiguousWriteOutcome("request sent, no response")

    monkeypatch.setattr(tracking_write, "dispatch", _raise)
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(args, summary="Will not land") == 1

    # Refused before any mutation -- the record must be untouched.
    record = tracking.load_record(status_env)
    assert record.summary != "Will not land"
