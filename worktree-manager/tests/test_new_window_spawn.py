"""Platform-dispatch tests for `new_window_spawn` -- the mechanics behind
the Picker's "Launch in new window" action: a deliberate, narrow exception
that opens a VISIBLE new terminal window, unlike every other process-
spawning helper in this codebase which exists to suppress one.
"""

from __future__ import annotations

import pytest

from worktree_manager import new_window_spawn as nws


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid


def test_windows_prefers_wt_exe(monkeypatch):
    monkeypatch.setattr(nws.platform, "system", lambda: "Windows")
    monkeypatch.setattr(nws.shutil, "which", lambda name: r"C:\wt.exe" if "wt" in name else None)
    calls = []
    monkeypatch.setattr(
        nws.subprocess, "Popen",
        lambda argv, **kw: calls.append((argv, kw)) or _FakeProc(111),
    )

    result = nws.spawn_detached_new_window(["psmux", "attach-session", "-t", "wt-abc"], title="abc")

    assert result == {"spawner": "wt.exe", "pid": 111}
    argv, kwargs = calls[0]
    assert argv[0] == r"C:\wt.exe"
    assert "-w" in argv and "-1" in argv
    assert argv[-4:] == ["psmux", "attach-session", "-t", "wt-abc"]
    assert kwargs.get("env") is None


def test_windows_falls_back_to_new_console_without_wt(monkeypatch):
    monkeypatch.setattr(nws.platform, "system", lambda: "Windows")
    monkeypatch.setattr(nws.shutil, "which", lambda name: None)
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeProc(222)

    monkeypatch.setattr(nws.subprocess, "Popen", fake_popen)

    result = nws.spawn_detached_new_window(["psmux", "attach-session", "-t", "wt-abc"], title="x")

    assert result["pid"] == 222
    assert "conhost" in result["spawner"]
    argv, kwargs = calls[0]
    assert argv == ["psmux", "attach-session", "-t", "wt-abc"]
    assert kwargs.get("creationflags") == nws._CREATE_NEW_CONSOLE


def test_windows_passes_an_explicit_env_through_to_popen(monkeypatch):
    """The `env` kwarg (Phase 9's no-mux-composed-with-new-window fix) must
    reach whichever spawner is actually used -- never silently dropped."""
    monkeypatch.setattr(nws.platform, "system", lambda: "Windows")
    monkeypatch.setattr(nws.shutil, "which", lambda name: r"C:\wt.exe" if "wt" in name else None)
    calls = []
    monkeypatch.setattr(
        nws.subprocess, "Popen",
        lambda argv, **kw: calls.append(kw) or _FakeProc(1),
    )
    env = {"WORKTREE_NO_MUX": "1"}

    nws.spawn_detached_new_window(["cmd"], title="x", env=env)

    assert calls[0].get("env") is env


def test_posix_probes_terminals_in_order(monkeypatch):
    monkeypatch.setattr(nws.platform, "system", lambda: "Linux")
    # Only the third-preferred terminal ("konsole") is "installed".
    monkeypatch.setattr(nws.shutil, "which", lambda name: "/usr/bin/konsole" if name == "konsole" else None)
    calls = []
    monkeypatch.setattr(
        nws.subprocess, "Popen",
        lambda argv, **kw: calls.append((argv, kw)) or _FakeProc(333),
    )

    result = nws.spawn_detached_new_window(["tmux", "attach-session", "-t", "wt-abc"], title="my-title")

    assert result == {"spawner": "konsole", "pid": 333}
    argv, kwargs = calls[0]
    assert argv[0] == "/usr/bin/konsole"
    assert argv[-4:] == ["tmux", "attach-session", "-t", "wt-abc"]
    assert kwargs.get("env") is None


def test_posix_fails_closed_when_nothing_is_found(monkeypatch):
    monkeypatch.setattr(nws.platform, "system", lambda: "Linux")
    monkeypatch.setattr(nws.shutil, "which", lambda name: None)

    def boom(*a, **kw):
        raise AssertionError("must not spawn anything when no spawner was found")

    monkeypatch.setattr(nws.subprocess, "Popen", boom)

    with pytest.raises(nws.NewWindowSpawnError, match="no visible terminal spawner"):
        nws.spawn_detached_new_window(["tmux", "attach-session", "-t", "wt-abc"], title="x")


def test_macos_uses_osascript(monkeypatch):
    monkeypatch.setattr(nws.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(
        nws.subprocess, "Popen",
        lambda argv, **kw: calls.append((argv, kw)) or _FakeProc(444),
    )

    result = nws.spawn_detached_new_window(["tmux", "attach-session", "-t", "wt-abc"], title="x")

    assert result == {"spawner": "osascript (Terminal.app)", "pid": 444}
    argv, kwargs = calls[0]
    assert argv[0] == "osascript"
    assert "tmux attach-session -t wt-abc" in argv[-1]
    assert kwargs.get("env") is None


def test_macos_escapes_double_quotes_and_backslashes_for_applescript(monkeypatch):
    """High-severity review finding: `shlex.quote()` only protects the shell
    layer. A raw `"` or `\\` surviving into the AppleScript string literal
    can terminate it early or inject AppleScript source (e.g. an argv
    element naming a path with a quote in it). Both characters must be
    AppleScript-escaped (backslash-doubled, then quote-escaped) before
    interpolation."""
    import shlex

    monkeypatch.setattr(nws.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(
        nws.subprocess, "Popen",
        lambda argv, **kw: calls.append(argv) or _FakeProc(555),
    )

    argv = ["echo", 'a path with a " quote and a \\ backslash']
    nws.spawn_detached_new_window(argv, title="x")

    script = calls[0][-1]
    prefix = 'tell application "Terminal" to do script "'
    assert script.startswith(prefix) and script.endswith('"')
    inner = script[len(prefix):-1]
    # The dynamic portion must be EXACTLY the AppleScript-escaped form of
    # the shell-quoted command -- i.e. the escape is invertible with no
    # leftover unescaped `"`/`\` that could terminate the literal early or
    # inject AppleScript source.
    shell_quoted_cmd = " ".join(shlex.quote(a) for a in argv)
    assert inner == nws._applescript_quote(shell_quoted_cmd)
    assert '\\"' in inner  # the quote survived, but escaped for AppleScript
    assert "\\\\" in inner  # the backslash survived, but doubled
    # Undoing the AppleScript escape recovers the original shell-quoted
    # command exactly -- the one-way assertion above plus this round-trip
    # together prove the escape neither drops nor corrupts a byte.
    recovered = inner.replace('\\"', '"').replace("\\\\", "\\")
    assert recovered == shell_quoted_cmd


def test_applescript_quote_helper_is_order_sensitive():
    """Backslashes must be doubled BEFORE quotes are escaped, or a `\\"`
    input would double-escape into `\\\\"` instead of the correct `\\\\\\"`."""
    assert nws._applescript_quote('a"b') == 'a\\"b'
    assert nws._applescript_quote("a\\b") == "a\\\\b"
    assert nws._applescript_quote('\\"') == '\\\\\\"'
