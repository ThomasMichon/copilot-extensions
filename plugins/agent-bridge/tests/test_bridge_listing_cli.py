"""The unified list keeps project scope and never ensures the Bridge daemon."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import __main__ as cli


@pytest.mark.parametrize(
    "argv",
    [
        ["--project", "demo", "--json", "list"],
        ["--project", "demo", "list", "--json"],
    ],
)
def test_list_json_keeps_explicit_project_and_does_not_ensure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    calls: list[dict[str, Any]] = []
    result = {"version": 1, "bridges": [], "sources": {}, "complete": False}

    def get_client(**kwargs: Any) -> Any:
        assert kwargs == {"ensure": False}
        return SimpleNamespace(list_bridges=lambda **scope: calls.append(scope) or result)

    monkeypatch.setattr(cli, "_get_client", get_client)
    cli.main(argv)
    assert calls == [
        {
            "project": "demo",
            "machine": None,
            "namespace": None,
            "status": None,
            "live": False,
            "all_projects": False,
        }
    ]
    assert json.loads(capsys.readouterr().out) == result


def test_json_list_infers_cwd_project_instead_of_defaulting_fleet_wide(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(cli, "_sender_repo", lambda: "cwd-project")
    monkeypatch.setattr(
        cli,
        "_get_client",
        lambda **_kwargs: SimpleNamespace(
            list_bridges=lambda **scope: (
                calls.append(scope)
                or {
                    "version": 1,
                    "bridges": [],
                    "sources": {},
                    "complete": True,
                }
            ),
        ),
    )
    cli.main(["--json", "list", "--machine", "host-a", "--namespace", "container", "--live"])
    assert calls[0]["project"] == "cwd-project"
    assert calls[0]["machine"] == "host-a"
    assert calls[0]["namespace"] == "container"
    assert calls[0]["live"] is True
    assert json.loads(capsys.readouterr().out)["complete"] is True


def test_unscoped_list_requires_explicit_all_projects_before_contacting_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_sender_repo", lambda: None)
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: pytest.fail("unscoped request"))
    with pytest.raises(SystemExit, match="project"):
        cli.main(["list"])


def test_explicit_all_projects_is_not_an_implicit_cwd_filter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(cli, "_sender_repo", lambda: "cwd-project")
    monkeypatch.setattr(
        cli,
        "_get_client",
        lambda **_kwargs: SimpleNamespace(
            list_bridges=lambda **scope: (
                calls.append(scope)
                or {
                    "version": 1,
                    "bridges": [],
                    "sources": {},
                    "complete": True,
                }
            ),
        ),
    )
    cli.main(["--json", "list", "--all-projects", "--status", "dormant"])
    assert calls[0]["project"] is None
    assert calls[0]["all_projects"] is True
    assert calls[0]["status"] == "dormant"
    capsys.readouterr()


def test_human_list_displays_unavailable_control_and_partial_sources(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_sender_repo", lambda: "demo")
    monkeypatch.setattr(
        cli,
        "_get_client",
        lambda **_kwargs: SimpleNamespace(
            list_bridges=lambda **_scope: {
                "version": 1,
                "complete": False,
                "sources": {"worktrees": {"completeness": "incomplete"}},
                "bridges": [
                    {
                        "handle": "workspace-a",
                        "project": "demo",
                        "machine": "host-a",
                        "namespace": "local",
                        "status": "running",
                        "live": True,
                        "hosting": "mux",
                        "transport": "local",
                        "directory": "/work/demo",
                        "location_kind": "worktree",
                        "control": "unavailable",
                        "control_reason": "registration_missing",
                        "title": "Finish the assigned goal",
                        "creator_worktree": None,
                        "caller_worktree": None,
                        "controller": None,
                        "disposition": {"activity": "Waiting for review", "summary": None},
                    }
                ],
            },
        ),
    )
    cli.main(["list"])
    output = capsys.readouterr()
    assert "workspace-a" in output.out
    assert "running" in output.out
    assert "unavailable" in output.out
    assert "Waiting for review" in output.out
    assert "incomplete" in output.err.lower()


def test_live_sessions_fleet_selection_retains_the_original_host_routes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(
        cli,
        "_get_client",
        lambda **kwargs: SimpleNamespace(
            list_bridges=lambda **scope: (
                calls.append((kwargs, scope))
                or {
                    "version": 1,
                    "bridges": [],
                    "sources": {},
                    "complete": False,
                }
            )
        ),
    )
    cli.main(["live-sessions", "list", "--host", "host-a", "--host", "host-b", "--json"])
    assert calls[0][0] == {"ensure": False}
    assert calls[0][1]["hosts"] == ["host-a", "host-b"]
    assert calls[0][1]["live"] is True
    assert calls[0][1]["all_projects"] is True
    assert json.loads(capsys.readouterr().out)["complete"] is False
