"""Related-repository plans retain deliberate profile-based Bridge startup."""

from __future__ import annotations

import re
import shlex

import pytest

from agent_worktrees.related import Locus, RelatedEntry, build_resolution


@pytest.mark.parametrize("venue,expected", [
    ("codespace", [
        ["codespace:<name>", "<task>"],
        ["container:<name>", "<task>"],
        ["codespace:<name>", "<read-only task>"],
    ]),
    ("container-local", [["container:<name>", "<task>"]]),
    ("container-remote", [
        ["<machine>", "<task>"],
        ["codespace:<name>", "<task>"],
        ["<machine>", "<read task>"],
    ]),
    ("machine", [["host-b", "<task>"], ["host-b", "<read task>"]]),
    ("local-remote", [["<machine>", "<task>"], ["<machine>", "<read task>"]]),
])
def test_related_plan_bridge_hints_select_legacy_startup_explicitly(
    venue: str, expected: list[list[str]],
) -> None:
    codespace = {"repo": "example/project"}
    local_container = {"repo": "example/project", "machines": ["host-a"]}
    remote_container = {"repo": "example/project", "machines": ["host-b"]}
    locus = {
        "codespace": Locus(
            preferred="codespace", codespace=codespace, container=local_container,
        ),
        "container-local": Locus(preferred="container", container=local_container),
        "container-remote": Locus(
            preferred="container", container=remote_container, codespace=codespace,
        ),
        "machine": Locus(preferred="machine:host-b"),
        "local-remote": Locus(preferred="local", machines=["host-b"]),
    }[venue]
    plan = build_resolution(
        RelatedEntry(name="project", locus=locus, delegate="agent-bridge"),
        current_machine="host-a", repo_class="worktree", repo_path=None, adopted=True,
    )
    commands = [
        shlex.split(command)
        for line in (*plan.steps, *plan.notes, *plan.explore)
        for command in re.findall(r"`(agent-bridge send[^`]*)`", line)
    ]
    assert commands == [
        ["agent-bridge", "send", target, "--legacy-selector", prompt]
        for target, prompt in expected
    ]
