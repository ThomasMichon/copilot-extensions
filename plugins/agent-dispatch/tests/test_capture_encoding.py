"""Explicit command codecs without changing other capture callers' defaults."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

from agent_dispatch import procutil, repository_github_auth
from agent_dispatch.repository_issue_loops import GitHubProvider


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
def test_capture_uses_the_requested_encoding_for_both_pipes(
    bounded: bool, encoding: str,
) -> None:
    text = "caf\u00e9" if encoding == "cp1252" else "\u270f\ufe0f caf\u00e9 \u2713 \U0001f680"
    payload = (text + "\r\nend\r").encode(encoding)
    expected = text + "\nend\n"
    done = procutil.run_background_capture(
        [sys.executable, "-c",
         f"import sys; sys.stdout.buffer.write({payload!r}); sys.stderr.buffer.write({payload!r})"],
        timeout=20, max_output=2 * len(expected) if bounded else None, encoding=encoding,
    )
    assert done is not None and done.returncode == 0
    assert done.stdout == done.stderr == expected


@pytest.mark.parametrize("encoding,text", [
    ("cp1252", "caf\u00e9"),
    ("utf-8", "\u270f\ufe0f \U0001f680"),
])
def test_capture_stdin_uses_the_requested_encoding(encoding: str, text: str) -> None:
    done = procutil.run_background_capture(
        [sys.executable, "-c",
         "import sys; data=sys.stdin.buffer.read(); "
         "sys.stdout.buffer.write(data); sys.stderr.buffer.write(data)"],
        timeout=20, input_text=text, encoding=encoding,
    )
    assert done is not None and done.returncode == 0
    assert done.stdout == done.stderr == text


@pytest.mark.parametrize("auth", ["ambient", "repository"])
def test_github_graphql_encoding_reaches_real_subprocess(
    monkeypatch: pytest.MonkeyPatch, auth: str,
) -> None:
    title = "\u270f\ufe0f caf\u00e9 \u2713 \U0001f680"
    payload = json.dumps({
        "data": {"repository": {"issues": {
            "nodes": [{
                "number": 7, "title": title, "url": "https://example.com/issues/7",
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "labels": {"nodes": []}, "comments": {"nodes": []},
            }],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}},
    }, ensure_ascii=False).encode("utf-8")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        # A deterministic legacy Windows default, even on a UTF-8 test host.
        kwargs.setdefault("encoding", "cp1252")
        kwargs.update(procutil.no_window_kwargs())
        run = subprocess.run if auth == "ambient" else repository_github_auth.run_repository_command
        return run(
            [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({payload!r})"],
            **kwargs,
        )

    monkeypatch.setattr(
        "agent_dispatch.repository_issue_loops.repository_environment",
        lambda *_args, **_kwargs: dict(os.environ),
    )
    provider = GitHubProvider(
        "issue-bot", runner=runner, repository="example/project", auth=auth,
    )
    provider._verified_repos.add("example/project")
    (issue,) = provider.list_open_issues("example/project")
    assert issue.title == title
    assert calls[0][0][:3] == ["gh", "api", "graphql"]
    assert calls[0][1]["encoding"] == "utf-8"


@pytest.mark.parametrize("encoding", [None, "utf-8", "cp1252"])
def test_repository_command_forwards_encoding(
    monkeypatch: pytest.MonkeyPatch, encoding: str | None,
) -> None:
    calls: list[dict[str, Any]] = []

    def capture(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(procutil, "run_background_capture", capture)
    repository_github_auth.run_repository_command(["gh", "api", "graphql"], encoding=encoding)
    assert calls[0]["encoding"] == encoding


def test_github_non_graphql_commands_keep_the_encoding_default() -> None:
    calls: list[dict[str, Any]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    provider = GitHubProvider("issue-bot", runner=runner)
    provider._gh("issue", "edit", "7")
    assert "encoding" not in calls[0]


@pytest.mark.parametrize("encoding", [None, "utf-8", "cp1252"])
def test_encoding_reaches_popen_without_changing_the_default_keyword_set(
    monkeypatch: pytest.MonkeyPatch, encoding: str | None,
) -> None:
    captured: dict[str, Any] = {}

    class Process:
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "", ""

    def popen(argv: list[str], **kwargs: Any) -> Process:
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)
    procutil.run_background_capture(["probe"], timeout=5, encoding=encoding)
    assert captured.get("encoding") == encoding
    if encoding is None:
        assert "encoding" not in captured
