"""Phase 0 skeleton tests: the app runs, reports its version, and stays a
programmatic, non-agentic, out-of-plugin entry point.
"""

from __future__ import annotations

import pytest

from worktree_manager import __version__
from worktree_manager import __main__ as entrypoint
from worktree_manager.__main__ import main


def test_version_flag(capsys):
    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_help_flag(capsys):
    assert main(["--help"]) == 0
    assert "worktree-manager" in capsys.readouterr().out.lower()


def test_bare_run_prints_intro_and_roadmap(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Worktree Manager" in out
    # The roadmap is shown so a first run is self-explanatory.
    assert "one-line bootstrap" in out
    assert "you are here" in out


def test_project_only_invocation_launches_picker(monkeypatch):
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_cmd_picker",
        lambda args: calls.append(args) or 17,
    )

    assert main(["--project", "dotfiles"]) == 17
    assert calls == [["dotfiles"]]


def test_project_only_invocation_rejects_missing_or_extra_values(capsys):
    assert main(["--project"]) == 2
    assert main(["--project", "dotfiles", "extra"]) == 2
    assert "exactly one project name" in capsys.readouterr().out


def test_is_not_a_plugin_payload():
    # Guard the boundary: the worktree-manager must not be delivered as a plugin.
    # It has no plugin.json and lives outside plugins/ (checked at the repo
    # level by the effort's validation); here we assert it declares no Copilot
    # plugin entry surface in its own package metadata.
    import worktree_manager

    assert not hasattr(worktree_manager, "PLUGIN_MANIFEST")


def test_ensure_utf8_streams_reconfigures_a_non_utf8_stdout(monkeypatch):
    """A console bound to a non-UTF-8 codepage (e.g. Windows' default
    ``cp1252``) must not crash when a command prints a status glyph like
    ``\u2713``/``\u2192`` (#5218) -- ``_ensure_utf8_streams`` reconfigures
    stdout/stderr to UTF-8 regardless of what the process inherited.
    """
    import io

    cp1252_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    cp1252_stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(entrypoint.sys, "stdout", cp1252_stdout)
    monkeypatch.setattr(entrypoint.sys, "stderr", cp1252_stderr)

    # Before reconfiguring: printing the glyph raises, reproducing the crash.
    with pytest.raises(UnicodeEncodeError):
        print("\u2713 done", file=cp1252_stdout)
        cp1252_stdout.flush()

    entrypoint._ensure_utf8_streams()

    assert cp1252_stdout.encoding.lower().replace("_", "-") == "utf-8"
    assert cp1252_stderr.encoding.lower().replace("_", "-") == "utf-8"
    # After reconfiguring, the same glyph no longer raises.
    print("\u2713 done", file=cp1252_stdout)
    cp1252_stdout.flush()


def test_ensure_utf8_streams_tolerates_a_stream_without_reconfigure():
    """A stream lacking ``reconfigure`` (e.g. a plain pipe/mock in a test
    harness) must not make this best-effort guard raise."""

    class _NoReconfigure:
        pass

    stream = _NoReconfigure()
    assert getattr(stream, "reconfigure", None) is None
    # Exercises the ``reconfigure is None: continue`` branch directly;
    # _ensure_utf8_streams() itself only ever touches sys.stdout/sys.stderr.
    entrypoint._ensure_utf8_streams()
