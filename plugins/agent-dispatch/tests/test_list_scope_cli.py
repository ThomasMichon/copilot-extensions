"""Generated worker commands must retain their explicit task-listing scope."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

import httpx
import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch import remote_dispatch, tracking
from agent_dispatch.client import DispatchClient
from agent_dispatch.embody_prompts import (
    autopilot_worker_prompt,
    fleet_autopilot_worker_prompt,
)


@dataclass
class ListingClient:
    calls: list[dict[str, object]] = field(default_factory=list)

    def list(self, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append(kwargs)
        return [{"id": "question", "repo": "example.com/owner/project"}]

    def __enter__(self) -> ListingClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch) -> ListingClient:
    client = ListingClient()
    monkeypatch.setattr(cli, "_client", lambda _args: client)
    monkeypatch.setattr(cli, "_enrich", lambda tasks: tasks)
    monkeypatch.setattr(tracking, "enrich_tasks", lambda tasks: tasks)
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _machine: False)
    return client


@pytest.mark.parametrize("kind", ("local", "fleet"))
@pytest.mark.parametrize("route", ("", " --shared"))
def test_generated_all_repo_list_command_executes(
    kind: str,
    route: str,
    listing: ListingClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "_scope_repo",
        lambda _args: pytest.fail("explicit all-repo listing must not inspect CWD"),
    )
    if kind == "local":
        prompt = autopilot_worker_prompt(
            "question", worker_id="worker", all_repos=True, route=route,
        )
    else:
        prompt = fleet_autopilot_worker_prompt(
            "question",
            origin="origin",
            owner="pool/worker",
            worker_id="worker",
            all_repos=True,
            route=route,
        )
    commands = [
        shlex.split(command)
        for command in re.findall(r"`([^`]+)`", prompt)
        if " list --all-repos" in command
    ]
    assert len(commands) == 1
    command = commands[0]
    arguments = command[command.index("agent-dispatch") + 1:]
    args = cli.build_parser().parse_args(arguments)

    assert args.func(args) == 0
    assert listing.calls == [{
        "repo": None,
        "status": None,
        "target_machine": None,
        "target_repo": None,
        "label": None,
        "evaluator_ref": None,
        "category": "work",
        "limit": 200,
    }]
    assert "question" in capsys.readouterr().out


@pytest.mark.parametrize("selector", ([], ["--repo", "project"]))
def test_list_keeps_repository_scoped_defaults(
    selector: list[str],
    listing: ListingClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selectors: list[str | None] = []

    def scope(args: object) -> str:
        selectors.append(getattr(args, "repo"))
        return "example.com/owner/project"

    monkeypatch.setattr(cli, "_scope_repo", scope)
    args = cli.build_parser().parse_args(["list", *selector])

    assert args.func(args) == 0
    assert selectors == ["project" if selector else None]
    assert listing.calls[0]["repo"] == "example.com/owner/project"
    capsys.readouterr()


def test_list_without_repo_still_refuses_unresolved_scope(
    listing: ListingClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_scope_repo", lambda _args: None)
    args = cli.build_parser().parse_args(["list"])

    assert args.func(args) == 2
    assert listing.calls == []
    assert "could not resolve the calling repo" in capsys.readouterr().err


def test_list_scope_flags_are_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["list", "--repo", "project", "--all-repos"])

    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_list_still_rejects_unknown_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["list", "--all-repoz"])

    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_list_registration_preserves_cli_callback_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_cmd_list", lambda _args: 42)
    args = cli.build_parser().parse_args(["list", "--all-repos"])

    assert args.func(args) == 42


def test_all_repo_list_uses_existing_http_query_contract(
    listing: ListingClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.QueryParams] = []
    tasks = [
        {"id": "first", "repo": "example.com/owner/first"},
        {"id": "second", "repo": "example.com/owner/second"},
    ]

    def exchange(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"task_categories": 1})
        assert request.url.path == "/tasks"
        seen.append(request.url.params)
        return httpx.Response(200, json=tasks)

    client = DispatchClient("http://coordinator", transport=httpx.MockTransport(exchange))
    monkeypatch.setattr(cli, "_client", lambda _args: client)
    monkeypatch.setattr(
        cli, "_scope_repo", lambda _args: pytest.fail("must not resolve local repo"),
    )
    args = cli.build_parser().parse_args([
        "list", "--all-repos", "--status", "queued", "--label", "questions",
        "--target-machine", "worker", "--target-repo", "library",
        "--evaluator-ref", "verification", "--limit", "3",
    ])

    assert args.func(args) == 0
    assert len(seen) == 1
    assert dict(seen[0]) == {
        "status": "queued",
        "target_machine": "worker",
        "target_repo": "library",
        "label": "questions",
        "evaluator_ref": "verification",
        "category": "work",
        "limit": "3",
    }
    assert listing.calls == []
    output = capsys.readouterr().out
    assert '"first"' in output and '"second"' in output


def test_peer_list_retains_explicit_all_repo_scope(
    listing: ListingClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli, "_scope_repo", lambda _args: pytest.fail("must not resolve local repo"),
    )
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _machine: True)
    calls: list[tuple[str, str | None, bool]] = []

    def browse(args: object, command: str, *, repo: str | None = None) -> int:
        calls.append((command, repo, bool(getattr(args, "all_repos"))))
        return 0

    monkeypatch.setattr(cli, "_browse_peer", browse)
    args = cli.build_parser().parse_args([
        "list", "--all-repos", "--machine", "peer",
        "--status", "queued", "--label", "questions",
        "--target-machine", "worker", "--target-repo", "library", "--limit", "3",
    ])

    assert args.func(args) == 0
    assert listing.calls == []
    assert calls == [("list", None, True)]
    forwarded = remote_dispatch.build_remote_browse_argv("list", args)
    remote = cli.build_parser().parse_args(forwarded[1:])
    assert remote.all_repos is True
    assert remote.repo is None
    assert remote.machine is None
    assert remote.status == "queued"
    assert remote.label == "questions"
    assert remote.target_machine == "worker"
    assert remote.target_repo == "library"
    assert remote.limit == 3
    capsys.readouterr()
