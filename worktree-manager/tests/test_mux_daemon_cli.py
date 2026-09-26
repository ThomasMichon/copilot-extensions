"""CLI-level tests for `worktree-manager mux-daemon ...` (Phase 3b Sub-slice
3 Step 2). Exercises `_cmd_mux_daemon` through `main()` against a scratch
runtime root, so it never touches the real `~/.worktree-manager`."""

from __future__ import annotations

import json

import pytest

from worktree_manager import managed_mux_link, managed_mux_session, mux_daemon
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


def test_mux_daemon_activate_deactivate_roundtrip(capsys, monkeypatch, tmp_path):
    from work_coalescing_singleton.server import CoalescingServer

    observed: list[dict] = []

    def _compute(kind: str, payload: dict) -> dict:
        observed.append(dict(payload))
        return {"applied": True, "revision": payload["mapping_revision"]}

    server = CoalescingServer(_compute)
    server.start()
    try:
        lock = tmp_path / "status-monitor.lock"
        rv = server.rendezvous()
        lock.write_text(
            json.dumps(
                {
                    "managed_mux_endpoint": rv["endpoint"],
                    "managed_mux_token": rv["token"],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(managed_mux_link, "lock_path", lambda root=None: lock)
        monkeypatch.setattr(managed_mux_link, "ensure_status_monitor_running", lambda: True)
        monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda root=None: True)
        monkeypatch.setattr(
            managed_mux_session,
            "_session_metadata",
            lambda mux_bin, mux_session: {
                "attached_clients": 1,
                "session_incarnation": "session-1:100",
                "panes": [{"pane_id": "%1", "role": "head", "live": True}],
            },
        )

        rc = main(
            [
                "mux-daemon",
                "activate",
                "--project=proj",
                "--worktree-id=wt-1",
                f"--worktree-path={tmp_path / 'worktree'}",
                "--mux-session=wt-1",
                "--mux-bin=tmux",
            ]
        )
        assert rc == 0
        activated = json.loads(capsys.readouterr().out)
        assert activated["mapping"]["applied"] is True
        assert activated["mapping_revision"] == 1

        rc = main(
            [
                "mux-daemon",
                "deactivate",
                "--project=proj",
                "--worktree-id=wt-1",
                "--mux-session=wt-1",
            ]
        )
        assert rc == 0
        deactivated = json.loads(capsys.readouterr().out)
        assert deactivated["mapping"]["applied"] is True
        assert deactivated["mapping_revision"] == 2
        assert [item["live"] for item in observed] == [True, False]
    finally:
        server.close()


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
