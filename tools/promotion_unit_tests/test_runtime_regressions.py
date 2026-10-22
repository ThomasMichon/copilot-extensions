"""Contributor regressions at actual in-process runtime decision boundaries."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKTREES = ROOT / "plugins/agent-worktrees"


def load_source(name: str, relative: str):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


routing = load_source(
    "cohort_project_routing",
    "plugins/agent-worktrees/src/agent_worktrees/project_routing.py",
)
mux = load_source(
    "cohort_mux_list_result",
    "plugins/agent-worktrees/src/agent_worktrees/mux_list_result.py",
)
turns = load_source(
    "cohort_live_turn_state",
    "plugins/agent-bridge/src/agent_bridge/live_turn_state.py",
)
_original_path = sys.path.copy()
try:
    load_source("registry_root", "plugins/agent-worktrees/scripts/registry_root.py")
    load_source(
        "anchor_shell_parser", "plugins/agent-worktrees/scripts/anchor_shell_parser.py"
    )
    guard = load_source(
        "cohort_anchor_write_guard",
        "plugins/agent-worktrees/scripts/anchor_write_guard.py",
    )
finally:
    sys.path[:] = _original_path


@pytest.mark.parametrize(
    "selector",
    [
        ["--repo", "/synthetic/project"],
        ["--repo=/synthetic/project"],
    ],
)
@pytest.mark.parametrize("operation", ["--peek", "--apply"])
def test_explicit_reconcile_repo_does_not_require_cwd_project(selector, operation):
    argv = ["reconcile-plugins", *selector, operation]
    before = argv.copy()
    assert routing._is_no_project_invocation(argv) is True
    assert argv == before


def test_native_execution_check_is_project_independent():
    argv = ["reconcile-plugins", "--check-update"]
    assert routing._is_no_project_invocation(argv) is True
    assert argv == ["reconcile-plugins", "--check-update"]


@pytest.mark.parametrize(
    "argv,allowed",
    [
        ([], False),
        (["reconcile-plugins", "--peek"], False),
        (["reconcile-plugins", "--apply"], False),
        (["list"], False),
        (["create"], False),
        (["get", "machine"], False),
        (["doctor", "--json"], True),
        (["repos", "list"], True),
        (["--version"], True),
        (["pr-status", "--repo=example/project", "--pr=123"], True),
        (["pr-status", "--repo=example/project"], False),
        (["get", "owner-ref", "--session-id=synthetic-session"], True),
        (["update", "--auth-report"], False),
        (["update", "--auth-report", "--auth-source=example/project"], True),
        (["claims", "ownership", "--json"], True),
        (["update", "--request-status", "synthetic-request"], True),
        (["update", "--request-status=synthetic-request", "--json"], True),
        (["update", "--recovery-status", "--json"], True),
        (["update", "--recover-after-restart"], True),
        (["update"], False),
        (["update", "--detach"], False),
        (["update", "--supervised-debug"], False),
        (["update", "--not-recovery-status"], False),
    ],
)
def test_other_project_routing_contracts_are_preserved(argv, allowed):
    assert routing._is_no_project_invocation(argv) is allowed


@pytest.mark.parametrize("binary", ["tmux", "/usr/bin/tmux", r"C:\Tools\tmux.exe"])
@pytest.mark.parametrize(
    "message",
    [
        "no server running on /synthetic/mux/default",
        "error connecting to /synthetic/mux/default (No such file or directory)",
    ],
)
def test_confirmed_no_server_is_empty_not_unavailable(binary, message):
    result = CompletedProcess([binary, "list-sessions"], 1, "", message + "\n")
    assert mux.is_empty_tmux_listing(binary, result) is True


@pytest.mark.parametrize(
    "binary,code,stdout,stderr",
    [
        ("tmux", 1, "", "error connecting to /synthetic/mux (Permission denied)"),
        ("tmux", 1, "", "error connecting to /synthetic/mux (Connection refused)"),
        ("tmux", 1, "", "server exited unexpectedly"),
        ("tmux", 127, "", "no server running on /synthetic/mux"),
        ("tmux", 1, "session:0\n", "no server running on /synthetic/mux"),
        ("tmux", 1, "", "invalid option\nno server running on /synthetic/mux"),
        ("tmux", 1, "", ""),
        ("psmux", 1, "", "no server running on /synthetic/mux"),
        ("other-program", 1, "", "no server running on /synthetic/mux"),
        ("tmux", 0, "", "no server running on /synthetic/mux"),
    ],
)
def test_unconfirmed_mux_failure_cannot_be_a_healthy_empty_listing(
    binary,
    code,
    stdout,
    stderr,
):
    result = CompletedProcess([binary, "list-sessions"], code, stdout, stderr)
    assert mux.is_empty_tmux_listing(binary, result) is False


@pytest.mark.parametrize(
    "text,counts,monitor",
    [
        ("", {}, {}),
        ("work:0\n", {"work": 0}, {}),
        ("team:work:2:$1:123\n", {"team:work:2:$1": 123}, {"team:work": (2, "$1:123")}),
        ("work:bad:$2:456\n", {"work:bad:$2": 456}, {"work": (0, "$2:456")}),
    ],
)
def test_mux_parsers_keep_their_distinct_listing_formats(text, counts, monitor):
    assert mux.parse_session_counts(text) == counts
    assert mux.parse_monitor_sessions(text) == monitor


@pytest.fixture
def anchor_evidence(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor"
    anchor.mkdir()

    def repository_root(path):
        candidate = Path(path)
        return anchor if candidate == anchor or anchor in candidate.parents else None

    monkeypatch.setattr(guard, "find_repo_root", repository_root)
    monkeypatch.setattr(guard, "is_linked_worktree", lambda _root: False)
    return anchor, [{"name": "example", "path": str(anchor)}]


@pytest.mark.parametrize(
    "tool,command",
    [
        ("bash", "git status --short; git diff --stat; git log -5 --oneline"),
        ("bash", "git merge-base HEAD origin/dev && git diff --name-only"),
        ("bash", 'git -C "{anchor}" merge-base HEAD origin/dev'),
        ("powershell", "$base = git merge-base HEAD origin/dev; git diff $base --stat"),
        ("powershell", "git fetch origin; $base = git merge-base HEAD origin/dev"),
        ("bash", "git branch --show-current"),
    ],
)
def test_read_only_git_batches_are_not_anchor_writes(
    anchor_evidence, tmp_path, tool, command
):
    anchor, anchors = anchor_evidence
    payload = {
        "toolName": tool,
        "cwd": str(anchor),
        "toolArgs": {"command": command.format(anchor=anchor)},
    }
    assert guard.decide(payload, env={}, home=tmp_path, anchors=anchors) is None


@pytest.mark.parametrize(
    "command",
    [
        "git add file.py",
        "git commit -m change",
        "git reset --hard",
        "git checkout main",
        "git merge topic",
        "git rebase topic",
        'git -C "{anchor}" reset --hard',
    ],
)
def test_mutation_controls_still_deny_confirmed_anchor_writes(
    anchor_evidence,
    tmp_path,
    command,
):
    anchor, anchors = anchor_evidence
    decision = guard.decide(
        {
            "toolName": "bash",
            "cwd": str(anchor),
            "toolArgs": {"command": command.format(anchor=anchor)},
        },
        env={},
        home=tmp_path,
        anchors=anchors,
    )
    assert decision and decision["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "marker",
    [
        {"agentId": "child"},
        {"data": {"agentId": "child"}},
        {"data": {"parentToolCallId": "child-tool"}},
    ],
)
@pytest.mark.parametrize("prior", [None, "idle", "running"])
@pytest.mark.parametrize(
    "kind",
    [
        "assistant.message",
        "assistant.turn_end",
        "tool.execution_start",
    ],
)
def test_background_child_events_cannot_change_parent_state_or_activity(
    marker, prior, kind
):
    events = [{"type": kind, **copy.deepcopy(marker)}]
    before = copy.deepcopy(events)
    assert turns.is_subagent_event(events[0]) is True
    assert turns.derive_turn_state(events, prior_state=prior) == (prior, False)
    assert events == before


@pytest.mark.parametrize(
    "events,prior,expected",
    [
        ([], None, (None, False)),
        ([{"type": "session.created"}], None, (None, False)),
        ([{"type": "assistant.turn_end"}], "running", ("idle", False)),
        ([{"type": "user.message"}], "idle", ("running", True)),
        (
            [{"type": "user.message"}, {"type": "assistant.turn_end"}],
            None,
            ("idle", True),
        ),
        (
            [{"type": "assistant.turn_end"}, {"type": "assistant.message"}],
            None,
            ("running", True),
        ),
        (
            [
                {"type": "assistant.message"},
                {"type": "assistant.turn_end", "agentId": "child"},
            ],
            None,
            ("running", True),
        ),
    ],
)
def test_parent_turn_order_and_unknown_startup_state_are_preserved(
    events, prior, expected
):
    assert turns.derive_turn_state(events, prior_state=prior) == expected
