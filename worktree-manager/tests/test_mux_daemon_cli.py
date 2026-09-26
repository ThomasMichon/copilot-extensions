"""CLI-level tests for `worktree-manager mux-daemon ...` (Phase 3b Sub-slice
3 Step 2). Exercises `_cmd_mux_daemon` through `main()` against a scratch
runtime root, so it never touches the real `~/.worktree-manager`."""

from __future__ import annotations

import json

import pytest

from worktree_manager import mux_daemon
from worktree_manager.__main__ import main


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path, monkeypatch):
    """Force every helper's ``root=None`` default to resolve into a scratch
    directory instead of the real ``~/.worktree-manager``."""
    monkeypatch.setattr(mux_daemon, "default_root", lambda: tmp_path)
    return tmp_path


def test_mux_daemon_no_action_prints_usage(capsys):
    assert main(["mux-daemon"]) == 2
    assert "usage" in capsys.readouterr().out


def test_mux_daemon_unknown_action(capsys):
    assert main(["mux-daemon", "bogus"]) == 2
    assert "unknown mux-daemon action" in capsys.readouterr().out


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
