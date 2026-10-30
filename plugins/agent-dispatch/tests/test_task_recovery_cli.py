"""Real CLI parsing with synthetic HTTP, never a live coordinator."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_dispatch import __main__ as core
from agent_dispatch.client import DispatchClient

HEALTH = {
    "task_recovery": {
        "protocol": "task_recovery/v1",
        "operations": ["detach_agent", "reset_proposed", "reset_queued", "reconcile_wake"],
    }
}
STRICT_FLAGS = [
    "--expected-revision",
    "revision-a",
    "--request-id",
    "request-a",
    "--actor",
    "test-operator",
    "--reason",
    "synthetic recovery",
]


@pytest.fixture(autouse=True)
def isolate_task_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AGENT_DISPATCH_TASK_TOKEN",
        "AGENT_DISPATCH_TASK_ID",
        "AGENT_DISPATCH_TASK_URL",
        "AGENT_DISPATCH_TASK_WORKER_ID",
        "AGENT_DISPATCH_TASK_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    recorded: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=HEALTH)
        return httpx.Response(200, json={"fixture": True})

    monkeypatch.setattr(
        core,
        "_client",
        lambda args, **_kwargs: DispatchClient(
            "https://dispatch.invalid",
            transport=httpx.MockTransport(handle),
        ),
    )
    return recorded


@pytest.mark.parametrize(
    "command,route",
    [
        (["detach-agent", "task-a", *STRICT_FLAGS], "/tasks/task-a/recovery/detach_agent"),
        (
            ["reset", "task-a", "--to", "queued", *STRICT_FLAGS],
            "/tasks/task-a/recovery/reset_queued",
        ),
        (
            ["reset", "task-a", "--to", "proposed", *STRICT_FLAGS],
            "/tasks/task-a/recovery/reset_proposed",
        ),
    ],
)
def test_strict_commands_use_exact_recovery_body(
    requests: list[httpx.Request],
    command: list[str],
    route: str,
    capsys: pytest.CaptureFixture,
) -> None:
    assert core.main(command) == 0
    assert json.loads(capsys.readouterr().out) == {"fixture": True}
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/health"), ("POST", route)]
    assert json.loads(requests[-1].content) == {
        "expected_revision": "revision-a",
        "request_id": "request-a",
        "actor": "test-operator",
        "reason": "synthetic recovery",
    }


@pytest.mark.parametrize("flags", [[], ["--to", "proposed"], ["--reason", "legacy note"]])
def test_default_proposed_reset_remains_legacy(
    requests: list[httpx.Request],
    flags: list[str],
) -> None:
    assert core.main(["reset", "task-a", *flags]) == 0
    assert [(r.method, r.url.path) for r in requests] == [("POST", "/tasks/task-a/reset")]
    body = json.loads(requests[-1].content)
    assert "expected_revision" not in body
    assert body["reason"] == ("legacy note" if flags == ["--reason", "legacy note"] else None)


def test_target_detach_is_not_agent_detach(requests: list[httpx.Request]) -> None:
    assert core.main(["detach", "task-a"]) == 0
    assert [(r.method, r.url.path) for r in requests] == [("POST", "/tasks/task-a/detach")]


def test_diagnostics_and_wake_reconciliation(requests: list[httpx.Request]) -> None:
    assert core.main(["task-diagnostics", "task-a"]) == 0
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/health"),
        ("GET", "/tasks/task-a/diagnostics"),
    ]
    requests.clear()
    assert (
        core.main(
            [
                "reconcile-wake",
                "task-a",
                "--outbox",
                "run_waiter_wakes",
                "--wake-id",
                "wake-a",
                "--expected-revision",
                "revision-a",
                "--actor",
                "test-operator",
                "--reason",
                "observe",
            ]
        )
        == 0
    )
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/health"),
        ("POST", "/tasks/task-a/wakes/reconcile"),
    ]
    assert json.loads(requests[-1].content) == {
        "outbox": "run_waiter_wakes",
        "wake_id": "wake-a",
        "expected_revision": "revision-a",
        "actor": "test-operator",
        "reason": "observe",
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["reset", "task-a", "--to", "started"],
        ["reset", "task-a", "--to", "queued"],
        ["reset", "task-a", "--actor", "operator"],
        ["reset", "task-a", "--to", "queued", *STRICT_FLAGS, "--expected-status", "queued"],
        ["reset", "task-a", "--to", "queued", *STRICT_FLAGS, "--actor", " "],
        ["detach-agent", "task-a", *STRICT_FLAGS, "--reason", ""],
    ],
)
def test_missing_or_conflicting_strict_fields_fail_before_client_resolution(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    capsys: pytest.CaptureFixture,
) -> None:
    def unexpected(args: object) -> DispatchClient:
        pytest.fail("invalid strict recovery must not resolve or start a coordinator")

    monkeypatch.setattr(core, "_client", unexpected)
    assert core.main(argv) == 2
    assert "agent-dispatch:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["reset", "task-a", "--outcome", "sent"],
        ["reset", "task-a", "--act", "operator"],
        ["detach-agent", "task-a"],
        ["detach-agent", "task-a", *STRICT_FLAGS, "--unknown", "value"],
        ["task-diagnostics", "task-a", "--repair"],
        ["reconcile-wake", "task-a", "--outbox", "other"],
        ["reconcile-wake", "task-a", "--outbox", "wake_outbox", "--outcome", "not_sent"],
    ],
)
def test_invalid_or_unknown_arguments_are_parser_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        core.build_parser().parse_args(argv)
    assert caught.value.code == 2


def test_unsupported_provider_is_reported_without_post(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    recorded = []

    def handle(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        core,
        "_client",
        lambda args, **_kwargs: DispatchClient(
            "https://dispatch.invalid",
            transport=httpx.MockTransport(handle),
        ),
    )
    assert core.main(["reset", "task-a", "--to", "queued", *STRICT_FLAGS]) == 1
    assert "task_recovery/v1" in capsys.readouterr().err
    assert [r.method for r in recorded] == ["GET"]


@pytest.mark.parametrize(
    "argv",
    [
        ["task-diagnostics", "task-a"],
        ["detach-agent", "task-a", *STRICT_FLAGS],
        ["reset", "task-a", "--to", "queued", *STRICT_FLAGS],
        [
            "reconcile-wake",
            "task-a",
            "--outbox",
            "wake_outbox",
            "--wake-id",
            "wake-a",
            "--expected-revision",
            "revision-a",
            "--actor",
            "operator",
            "--reason",
            "observe",
        ],
    ],
)
def test_task_only_cli_authority_cannot_use_recovery(
    requests: list[httpx.Request],
    argv: list[str],
    capsys: pytest.CaptureFixture,
) -> None:
    projection = [
        "--task-token",
        "task-fixture",
        "--task-scope",
        "task-a",
        "--task-url",
        "https://dispatch.invalid",
        "--task-worker",
        "host-a/work-a",
    ]
    assert core.main([*projection, *argv]) == 1
    assert "outside the projected task authority" in capsys.readouterr().err
    assert requests == []
