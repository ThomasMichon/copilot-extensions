"""CLI-level tests for `worktree-manager mux-daemon ...` (Phase 3b Sub-slice
3 Step 2). Exercises `_cmd_mux_daemon` through `main()` against a scratch
runtime root, so it never touches the real `~/.worktree-manager`."""

from __future__ import annotations

import json

import pytest

from worktree_manager import mux_daemon
from worktree_manager import mux_mapping_registry
from worktree_manager.__main__ import main


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path, monkeypatch):
    """Force every helper's ``root=None`` default to resolve into a scratch
    directory instead of the real ``~/.worktree-manager``. Patched in BOTH
    modules -- ``mux_daemon.default_root`` (its own lock-path resolution)
    and ``mux_mapping_registry.default_root`` (the registry lives in its
    own module since the Copilot-review-driven split, with its own
    imported reference to the same function)."""
    monkeypatch.setattr(mux_daemon, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_mapping_registry, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *a, **k: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *a, **k: {"applied": True})
    return tmp_path


def test_mux_daemon_no_action_prints_usage(capsys):
    assert main(["mux-daemon"]) == 2
    assert "usage" in capsys.readouterr().out


def test_mux_daemon_unknown_action(capsys):
    assert main(["mux-daemon", "bogus"]) == 2
    assert "unknown mux-daemon action" in capsys.readouterr().out


def test_mux_daemon_status_delegates_to_daemons_cli(monkeypatch, capsys):
    """``mux-daemon status`` (and its ``daemons status`` alias dispatched
    from ``__main__``) both route into the shared Phase 1 observability
    report (copilot-extensions#5001)."""
    monkeypatch.setattr("worktree_manager.daemons_status.daemon_statuses", lambda: [])

    assert main(["mux-daemon", "status"]) == 0
    assert "no resident mux-daemons" in capsys.readouterr().out

    assert main(["daemons", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_mux_daemon_register_show_remove_roundtrip(capsys):
    rc = main(
        [
            "mux-daemon",
            "register",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=wt-1",
            "--mux-bin=psmux",
            "--mapping-revision=1",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"applied": True, "revision": 1}

    rc = main(["mux-daemon", "show", "--project=proj", "--worktree-id=wt-1"])
    assert rc == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["mux_session"] == "wt-1"
    assert shown["live"] is True

    rc = main(["mux-daemon", "remove", "--project=proj", "--worktree-id=wt-1"])
    assert rc == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed == {"applied": True}

    # remove() tombstones (live: false) rather than deleting -- show still
    # finds the entry, just no longer live.
    rc = main(["mux-daemon", "show", "--project=proj", "--worktree-id=wt-1"])
    assert rc == 0
    tombstoned = json.loads(capsys.readouterr().out)
    assert tombstoned["live"] is False


def test_mux_daemon_register_auto_assigns_revision_when_omitted(capsys):
    rc = main(
        [
            "mux-daemon",
            "register",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=wt-1",
            "--mux-bin=psmux",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"applied": True, "revision": 1}

    rc = main(
        [
            "mux-daemon",
            "register",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=wt-1",
            "--mux-bin=psmux",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"applied": True, "revision": 2}


def test_mux_daemon_remove_rejects_non_integer_revision(capsys):
    rc = main(
        [
            "mux-daemon",
            "remove",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mapping-revision=not-a-number",
        ]
    )
    assert rc == 2
    assert "must be an integer" in capsys.readouterr().out


def test_mux_daemon_remove_with_mux_session_rejects_a_stale_teardown(capsys):
    """#3838, exercised through the real CLI
    surface: an old session's unversioned ``remove --mux-session=<old>``
    must not tombstone a newer session's live mapping for the same
    worktree."""
    rc = main(
        [
            "mux-daemon",
            "register",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=session-a",
            "--mux-bin=psmux",
            "--mapping-revision=1",
        ]
    )
    assert rc == 0
    capsys.readouterr()

    rc = main(
        [
            "mux-daemon",
            "register",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=session-b",
            "--mux-bin=psmux",
            "--mapping-revision=2",
        ]
    )
    assert rc == 0
    capsys.readouterr()

    rc = main(
        [
            "mux-daemon",
            "remove",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=session-a",
        ]
    )
    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is False
    assert result["reason"] == "session_mismatch"

    rc = main(["mux-daemon", "show", "--project=proj", "--worktree-id=wt-1"])
    assert rc == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["live"] is True
    assert shown["mux_session"] == "session-b"


def test_mux_daemon_remove_with_reused_display_name_uses_session_incarnation(capsys):
    """Copilot review finding on PR #3906: the display session name alone
    is reused deterministically across launch incarnations for the same
    worktree (both launchers derive ``wt-<worktree_id>``), so an
    old-vs-new pair can compare EQUAL by name. ``--session-incarnation=``
    (a live tmux/psmux session_id:created probe in production) is the
    authoritative identity check that still rejects the stale teardown even
    when the reused name would have let it through."""
    same_name = "wt-1"
    rc = main(
        [
            "mux-daemon", "register",
            "--project=proj", "--worktree-id=wt-1",
            f"--mux-session={same_name}", "--mux-bin=tmux",
            "--session-incarnation=tmux-session-id-1:100", "--mapping-revision=1",
        ]
    )
    assert rc == 0
    capsys.readouterr()

    rc = main(
        [
            "mux-daemon", "register",
            "--project=proj", "--worktree-id=wt-1",
            f"--mux-session={same_name}", "--mux-bin=tmux",
            "--session-incarnation=tmux-session-id-2:200", "--mapping-revision=2",
        ]
    )
    assert rc == 0
    capsys.readouterr()

    rc = main(
        [
            "mux-daemon", "remove",
            "--project=proj", "--worktree-id=wt-1",
            f"--mux-session={same_name}", "--session-incarnation=tmux-session-id-1:100",
        ]
    )
    assert rc == 1
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is False
    assert result["reason"] == "session_mismatch"

    rc = main(["mux-daemon", "show", "--project=proj", "--worktree-id=wt-1"])
    assert rc == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["live"] is True
    assert shown["session_incarnation"] == "tmux-session-id-2:200"


def test_mux_daemon_remove_rejects_negative_revision_for_never_registered_key(capsys):
    """Copilot review finding: a negative revision parses successfully, but
    when the key is absent remove_mapping() builds a tombstone and
    _normalize_mapping_entry() raises ValueError -- this must surface as
    the same controlled error/exit code register() already uses, not an
    uncaught traceback."""
    rc = main(
        [
            "mux-daemon",
            "remove",
            "--project=proj",
            "--worktree-id=never-registered",
            "--mapping-revision=-1",
        ]
    )
    assert rc == 2
    assert "error:" in capsys.readouterr().out


def test_mux_daemon_register_rejects_missing_required_field(capsys):
    rc = main(["mux-daemon", "register", "--project=proj"])
    assert rc == 2
    assert "error:" in capsys.readouterr().out


def test_mux_daemon_register_rejects_non_integer_revision(capsys):
    rc = main(
        [
            "mux-daemon",
            "register",
            "--project=proj",
            "--worktree-id=wt-1",
            "--mux-session=wt-1",
            "--mux-bin=psmux",
            "--mapping-revision=not-a-number",
        ]
    )
    assert rc == 2
    assert "must be an integer" in capsys.readouterr().out


def test_mux_daemon_remove_requires_project_and_worktree_id(capsys):
    rc = main(["mux-daemon", "remove", "--project=proj"])
    assert rc == 2
    assert "error:" in capsys.readouterr().out


def test_mux_daemon_show_requires_project_and_worktree_id(capsys):
    rc = main(["mux-daemon", "show", "--worktree-id=wt-1"])
    assert rc == 2
    assert "error:" in capsys.readouterr().out


def test_help_lists_mux_daemon(capsys):
    assert main(["--help"]) == 0
    assert "mux-daemon" in capsys.readouterr().out
