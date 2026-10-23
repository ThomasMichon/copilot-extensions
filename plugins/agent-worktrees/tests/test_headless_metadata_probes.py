"""Captured metadata probes must not create visible Windows consoles."""

from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main


@pytest.mark.parametrize("probe", ["registration", "launcher-enumeration"])
def test_metadata_probe_uses_shared_no_window_options(tmp_path, monkeypatch, probe):
    calls = []
    monkeypatch.setattr(main, "no_window_kwargs", lambda: {"creationflags": 12345})

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", run)
    if probe == "registration":
        assert main._registration_nudge_context(str(tmp_path)) == ""
        expected = "git"
    else:
        assert main._enumerate_launcher_shells_windows() == []
        expected = "pwsh"
    assert len(calls) == 1
    assert calls[0][0][0] == expected
    assert calls[0][1]["creationflags"] == 12345
    assert calls[0][1]["capture_output"] is True


@pytest.mark.parametrize("probe", ["common-dir", "toplevel"])
def test_state_root_probe_uses_shared_no_window_options(tmp_path, monkeypatch, probe):
    from agent_worktrees import state_root

    calls = []
    monkeypatch.setattr(state_root, "no_window_kwargs", lambda: {"creationflags": 12345})

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(tmp_path), stderr="")

    monkeypatch.setattr(state_root.subprocess, "run", run)
    if probe == "common-dir":
        assert state_root._git_common_dir(tmp_path) == tmp_path.resolve()
    else:
        assert state_root._git_toplevel(str(tmp_path)) == str(tmp_path)
    assert len(calls) == 1
    assert calls[0][0][:3] == ["git", "-C", str(tmp_path)]
    assert calls[0][1]["creationflags"] == 12345
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["timeout"] == 10
    assert calls[0][1]["stdin"] == state_root.subprocess.DEVNULL
