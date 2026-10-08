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
