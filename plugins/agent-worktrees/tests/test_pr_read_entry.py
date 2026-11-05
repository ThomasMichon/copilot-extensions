"""One-shot read startup preserves its handler and declines project/lifecycle routing."""

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import current_roster_entry, pr_read_entry


@pytest.mark.parametrize("argv", [
    [], ["pr-watch", "snapshot", "7"], ["pr-watch", "wait", "owner/repo", "7"],
    ["pr-watch", "cursor", "owner/repo", "7"],
    ["pr-watch", "reply", "owner/repo", "7"],
    ["pr-watch", "subscribe", "owner/repo", "7"],
    ["pr-watch", "snapshot", "--config", "7"],
    ["pr-watch", "snapshot", "/repo", "7"],
    ["pr-watch", "snapshot", "owner/repo", "bad"],
    ["--project", "p", "pr-watch", "snapshot", "owner/repo", "7"],
    ["pr-watch", "snapshot", "owner/repo", "7", "--project", "p"],
    ["pr-watch", "snapshot", "owner/repo", "7", "--project=p"],
    ["pr-watch", "snapshot", "owner/repo", "7", "-p", "p"],
])
def test_selector_declines_without_imports_or_filesystem_effects(monkeypatch, argv):
    def forbidden(*args, **kwargs):
        pytest.fail("read selection performed an effect")

    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "open", forbidden)
        guarded.setattr(Path, "cwd", forbidden)
        guarded.setattr(builtins, "__import__", forbidden)
        result = pr_read_entry.select_read(argv)
    assert result is None


@pytest.mark.parametrize("verb", ["snapshot", "status", "threads"])
def test_selected_read_retains_exact_arguments_and_existing_handler(monkeypatch, verb):
    from agent_worktrees import pr_cli

    arguments = ["pr-watch", verb, "owner/repo", "7", "--json",
                 "--host", "https://forge.example", "--config", "selected.yaml"]
    seen = []
    monkeypatch.setattr(pr_cli, "cmd_pr_watch_dispatch",
                        lambda argv: seen.append(argv) or 19)
    selected = pr_read_entry.select_read(arguments)
    assert selected is not None and not seen
    arguments[-1] = "changed.yaml"
    assert selected() == 19
    assert seen == [[verb, "owner/repo", "7", "--json",
                     "--host", "https://forge.example", "--config", "selected.yaml"]]


@pytest.mark.parametrize("kind", ["module", "console"])
def test_read_entry_uses_existing_exit_policy_without_importing_root(monkeypatch, kind):
    from agent_worktrees import _shutdown_exit

    calls = []
    monkeypatch.setattr(sys, "argv", ["agent-worktrees", "pr-watch", "snapshot", "owner/repo", "7"])
    monkeypatch.setitem(sys.modules, "agent_worktrees.__main__",
                        SimpleNamespace(console_entry=lambda: pytest.fail("read imported root entry")))
    monkeypatch.setattr(pr_read_entry, "_run_read", lambda args: calls.append(args) or 17)
    monkeypatch.setattr(_shutdown_exit, "run_and_exit", lambda selected: selected())
    if kind == "module":
        assert current_roster_entry.run_module_if_selected() is None
    else:
        assert current_roster_entry.console_entry() == 17
    assert calls == [("snapshot", "owner/repo", "7")]


def test_read_entry_preserves_interrupt_exit(monkeypatch, capsys):
    from agent_worktrees import pr_cli

    def interrupt(argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(pr_cli, "cmd_pr_watch_dispatch", interrupt)
    assert pr_read_entry.select_read(["pr-watch", "snapshot", "owner/repo", "7"])() == 130
    assert "Cancelled." in capsys.readouterr().out
