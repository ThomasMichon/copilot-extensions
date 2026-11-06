"""Contributor regressions at actual in-process runtime decision boundaries."""

from __future__ import annotations

import copy
import importlib.util
import os
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
    forms = load_source(
        "tool_call_forms", "plugins/agent-worktrees/scripts/tool_call_forms.py"
    )
    guard = load_source(
        "cohort_anchor_write_guard",
        "plugins/agent-worktrees/scripts/anchor_write_guard.py",
    )
finally:
    sys.path[:] = _original_path


def test_native_patch_targets_exclude_content_and_keep_moves():
    patch = (
        "*** Begin Patch\n*** Update File: source.py\n"
        "+mentions other/repo.py\n*** Move to: moved.py\n"
        "*** Add File: added.py\n+content\n*** Delete File: deleted.py\n*** End Patch\n"
    )
    assert forms.write_targets("apply_patch", patch) == [
        "source.py", "moved.py", "added.py", "deleted.py",
    ]
    assert forms.write_targets("multi_edit", {
        "edits": [{"file_path": "source.py", "new_string": "other/repo.py"}],
    }) == ["source.py"]


@pytest.mark.parametrize("command,mutates", [
    pytest.param("git --no-pager status --short", False, id="read"),
    pytest.param("git -c core.quotepath=false commit -m test", True, id="global-options"),
    pytest.param("git pull --ff-only", True, id="pull"),
    pytest.param("git branch new-branch", True, id="branch"),
    pytest.param("git branch --show-current", False, id="read-branch"),
    pytest.param("Write-Output $(git fetch origin)", True, id="substitution"),
    pytest.param("git status > report.txt", True, id="redirect"),
    pytest.param("Write-Output 'git commit; Set-Content x value'", False, id="quoted"),
    pytest.param(";".join(["git status"] * 128), False, id="segment-limit"),
    pytest.param(";".join(["git status"] * 129), True, id="segment-overflow"),
    pytest.param("Write-Output $(" * 9 + "git status" + ")" * 9, True, id="depth-overflow"),
    pytest.param("sed -n '1,100p' file.py", False, id="read-sed"),
    pytest.param("sed -n '1,100p' file.py > report.txt", True, id="read-sed-redirect"),
    pytest.param("sed -i.bak 's/old/new/' file.py", True, id="inplace-sed"),
    pytest.param("$result = git commit -m test", True, id="assignment"),
    pytest.param("$null = Set-Content file value", True, id="assignment-write"),
    pytest.param("$result = 'git commit -m test'", False, id="literal-assignment"),
    pytest.param('Write-Output "' + "(" * 20000 + '"', False, id="quoted-parentheses"),
    pytest.param("$(" * 1000, True, id="scan-overflow"),
    pytest.param("& { git commit -m test }", True, id="invoked-block"),
    pytest.param("if ($true) { Set-Content file value }", True, id="conditional"),
    pytest.param("Write-Output '& { git commit -m test }'", False, id="literal-block"),
    pytest.param("$result.Value = git commit -m test", True, id="member-assignment"),
    pytest.param("$result[0] = Set-Content file value", True, id="indexed-assignment"),
    pytest.param("[string]$result = git commit -m test", True, id="typed-assignment"),
    pytest.param("[string]$result = Set-Content file value", True, id="typed-write"),
    pytest.param("1 | % { Set-Content file value }", True, id="pipeline-block"),
    pytest.param("Invoke-Command -ScriptBlock { Set-Content file value }", True, id="invocation-block"),
    pytest.param("Write-Output 'Invoke-Command -ScriptBlock { Set-Content file value }'", False, id="quoted-scriptblock"),
    pytest.param("Write-Output '[string]$result = git commit -m test'", False, id="quoted-type"),
    pytest.param("sed -sni s/a/b/ file", True, id="clustered-sed"),
    pytest.param("sed -bnEi s/a/b/ file", True, id="binary-clustered-sed"),
    pytest.param("sed -neprint file", False, id="attached-sed-expression"),
    pytest.param("sed -nf -i file", False, id="sed-option-as-file"),
    pytest.param("Get-Content -LiteralPath file[ab]", False, id="read-bracket-path"),
    pytest.param("[IO.File]::ReadAllText('file')", False, id="read-type-call"),
    pytest.param("[string[]]$result = git commit -m test", True, id="typed-array-assignment"),
    pytest.param("git diff --output=report.txt", True, id="git-output"),
    pytest.param("git log --output report.txt", True, id="git-output-argument"),
    pytest.param("git show --output=report.txt HEAD", True, id="git-show-output"),
    pytest.param("git diff -- --output=literal-path", False, id="git-output-after-delimiter"),
    pytest.param("git log --format=--output=literal", False, id="git-output-format-data"),
    pytest.param(". git commit -m test", True, id="dot-git-write"),
    pytest.param(". Set-Content file value", True, id="dot-file-write"),
    pytest.param(". git status --short", False, id="dot-read"),
    pytest.param("$result = . git commit -m test", True, id="assigned-dot-write"),
])
def test_shell_invalidation_uses_original_parser_not_permission_exemptions(command, mutates):
    assert guard.shell_may_mutate(command, "powershell") is mutates


@pytest.mark.parametrize("tool", ["bash", "sh"])
@pytest.mark.parametrize("command,mutates", [
    pytest.param('result="$(git commit -m test)"', True, id="quoted"),
    pytest.param("result=$(git commit -m test)", True, id="unquoted"),
    pytest.param("result='$(git commit -m test)'", False, id="literal"),
])
def test_assignment_substitution_precedes_environment_normalization(tool, command, mutates):
    assert guard.shell_may_mutate(command, tool) is mutates


def test_native_batch_retains_session_scope_and_valid_calls():
    events = forms.tool_events({
        "sessionId": "synthetic-session", "workingDirectory": "/synthetic/project",
        "toolCalls": [None, {"id": "write", "name": "apply_patch", "args": "patch"}],
    })
    assert events == [{
        "sessionId": "synthetic-session", "workingDirectory": "/synthetic/project",
        "cwd": "/synthetic/project", "toolCallId": "write",
        "toolName": "apply_patch", "toolArgs": "patch",
    }]

def test_strict_native_batch_keeps_canonical_scope_and_arguments():
    payload = {
        "sessionId": "synthetic-session", "cwd": "/synthetic/cwd",
        "workingDirectory": "/synthetic/workspace", "tool_input": {"path": "outer"},
        "toolCalls": [{"id": "write", "name": "edit", "args": '{"path":"target"}'}],
    }
    assert forms.tool_events(payload, strict=True) == [{
        "sessionId": "synthetic-session", "cwd": "/synthetic/cwd",
        "workingDirectory": "/synthetic/workspace", "tool_input": {"path": "outer"},
        "toolCallId": "write", "toolName": "edit", "toolArgs": '{"path":"target"}',
    }]
    assert forms.native_mutation_targets(
        payload, write_tools=frozenset({"edit"}), cwd=payload["workingDirectory"],
    ) == [os.path.join(payload["workingDirectory"], "target")]


def test_strict_native_batch_rejects_incomplete_inputs_instead_of_skipping():
    valid = {"name": "edit", "args": {"path": "one"}}
    for calls in (
        None, {}, [], [valid, None], [valid, {"name": []}],
        [valid, {"name": ""}], [valid, {"name": "edit", "cwd": "/other"}],
        [valid] * 129,
    ):
        with pytest.raises(ValueError):
            forms.native_mutation_targets(
                {"toolCalls": calls}, write_tools=frozenset({"edit"}), cwd="/synthetic/workspace",
            )


def test_native_batch_target_limit_is_shared_and_unknowns_keep_workspace():
    workspace = "/synthetic/workspace"
    writes = frozenset({"edit", "multi_edit"})
    assert forms.native_mutation_targets(
        {"toolCalls": [{"name": "view"}, {"name": "edit", "args": {"path": []}}]},
        write_tools=writes, cwd=workspace,
    ) == [workspace]
    assert forms.native_mutation_targets(
        {"toolCalls": [{"name": "view"}]}, write_tools=writes, cwd=workspace,
    ) == []
    assert forms.native_mutation_targets(
        {"toolCalls": [
            {"name": "multi_edit", "args": {"files": [{"path": f"file-{i}"} for i in range(128)]}},
            {"name": "edit", "args": {"path": "overflow"}},
        ]}, write_tools=writes, cwd=workspace,
    ) is None


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
        (["update", "--recover-for-repair"], True),
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
    assert mux.parse_session_counts(text, strict=True) == counts
    assert mux.parse_monitor_sessions(text) == monitor


def test_authoritative_mux_parser_rejects_partial_and_duplicate_evidence():
    for text in ("unparsed", "work:-1", "work:bad", "work:0\nwork:1"):
        with pytest.raises(ValueError, match="absence is unconfirmed"):
            mux.parse_session_counts(text, strict=True)


def test_authoritative_presence_requires_success_or_confirmed_absence():
    assert mux.confirmed_has_session("wt-one", CompletedProcess([], 0, b"", b"")) is True
    for message in (
        b"can't find session: wt-one", b"session not found: wt-one",
        "no server running", "no server running on /synthetic/mux",
        "error connecting to /synthetic/mux (No such file or directory)",
        b"error connecting to /synthetic/mux (No such file or directory)",
    ):
        assert mux.confirmed_has_session("wt-one", CompletedProcess([], 1, "", message)) is False
    for binary in ("psmux", r"C:\Tools\PSMUX.EXE"):
        assert mux.confirmed_has_session(
            "wt-one", CompletedProcess([], 1, b"", b""), binary=binary,
        ) is False
    for result in (
        CompletedProcess([], 1, "", "permission denied"),
        CompletedProcess([], 1, "", "can't find session: wt-other"),
        CompletedProcess([], 2, "", "no server running"),
    ):
        with pytest.raises(RuntimeError, match="absence is unconfirmed"):
            mux.confirmed_has_session("wt-one", result)
    for binary, result in (
        ("tmux", CompletedProcess([], 1, b"", b"")),
        ("psmux", CompletedProcess([], 2, b"", b"")),
        ("psmux", CompletedProcess([], 1, b"", b"permission denied")),
        ("psmux", CompletedProcess([], 1, b"partial output", b"")),
    ):
        with pytest.raises(RuntimeError, match="absence is unconfirmed"):
            mux.confirmed_has_session("wt-one", result, binary=binary)


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
