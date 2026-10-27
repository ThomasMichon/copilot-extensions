"""Mux process CWD is independent from the selected pane worktree."""

from pathlib import Path
import subprocess

import pytest

from agent_worktrees import mux_creation, pane_lifecycle, sessions


@pytest.mark.parametrize("system", ["Windows", "Linux"])
@pytest.mark.parametrize("kind", ["session", "window", "pane-session", "pane-window"])
@pytest.mark.parametrize("returncode", [0, 9])
@pytest.mark.parametrize("directory", ["absolute", "relative", "empty"])
def test_mux_creation_keeps_process_home_separate_from_pane(
    monkeypatch, tmp_path, system, kind, returncode, directory,
):
    home = tmp_path / "home with spaces"
    worktree = tmp_path / "selected worktree"
    home.mkdir()
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)
    supplied = str(worktree) if directory == "absolute" else (
        worktree.name if directory == "relative" else ""
    )
    selected = supplied
    if system == "Windows":
        selected = str(worktree) if directory != "empty" else str(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(mux_creation.platform, "system", lambda: system)
    monkeypatch.setattr(mux_creation, "has_session", lambda *args: False)
    monkeypatch.setattr(
        mux_creation, "run_session", lambda argv, *args: mux_creation.run(argv),
    )
    monkeypatch.setattr(sessions.activity, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(sessions, "cleanup_mux_pane_args", lambda argv: None)
    monkeypatch.setattr(sessions, "has_mux_session", lambda wt: kind.endswith("window"))
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(sessions, "mux_focus_pane", lambda *a, **k: True)
    monkeypatch.setattr(sessions, "_initial_prompt_receipt_path",
                        lambda token: tmp_path / "receipt")
    monkeypatch.setattr(pane_lifecycle, "_wait_for_launch_receipt",
                        lambda *a, **k: (True, "launching"))
    expected = [
        "psmux", "new-window" if kind.endswith("window") else "new-session",
        "-c", selected, "child", "two words",
    ]
    directories = []

    def build(worktree_id, work_dir, *args, **kwargs):
        directories.append(work_dir)
        return list(expected)

    monkeypatch.setattr(sessions, "build_mux_new_session_argv", build)
    monkeypatch.setattr(sessions, "build_mux_new_window_argv", build)
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, returncode, stdout="%1\n", stderr="refused" if returncode else "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    operation = (
        pane_lifecycle.pane_create if kind.startswith("pane-") else
        sessions.mux_new_window if kind == "window" else sessions.mux_new_session
    )
    result = operation("fixture", supplied, ["child", "two words"], mux="psmux")
    assert result["ok"] is (returncode == 0)
    assert len(seen) == 1
    assert directories == [selected]
    assert seen[0][0] == expected
    assert seen[0][1].get("cwd") == (str(home) if system == "Windows" else None)
    assert Path.cwd() == tmp_path
