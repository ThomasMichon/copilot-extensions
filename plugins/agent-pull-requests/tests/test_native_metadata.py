"""Metadata forwarding preserves selected-provider uncertainty and skew."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agent_pull_requests import native_author

REPO = "example/project"
PROVIDER = "github"


@pytest.fixture(autouse=True, params=["github", "gitea"])
def provider(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules[__name__], "PROVIDER", request.param)


def arguments(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(
        command="update",
        repo=REPO,
        number=7,
        title="after",
        body=None,
        body_file=None,
        reviewer=[],
        work_item=[],
        publication_origin=None,
        publication_label=[],
        config=None,
        **kwargs,
    )


def support() -> dict[str, Any]:
    return {
        "schema": "agent-worktrees.pr-author-capabilities",
        "version": 1,
        "provider": PROVIDER,
        "repo": REPO,
        "operations": {"metadata-update": True, "future": True},
    }


def result(status: str = "confirmed") -> dict[str, Any]:
    return {
        "schema": "agent-worktrees.pr-metadata",
        "version": 1,
        "provider": PROVIDER,
        "repo": REPO,
        "number": 7,
        "publication_retained": True,
        "ok": status == "confirmed",
        "actions": {
            "title": {"status": status, **({"changed": True} if status == "confirmed" else {})}
        },
        **({"error": "Reconcile this PR before another attempt"} if status != "confirmed" else {}),
    }


@pytest.mark.parametrize("status", ["confirmed", "not_attempted", "failed", "unknown"])
def test_native_metadata_keeps_every_owner_outcome(
    monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        value = support() if len(calls) == 1 else result(status)
        return subprocess.CompletedProcess(
            argv, 0 if len(calls) == 1 or value["ok"] else 1, json.dumps(value), ""
        )

    monkeypatch.setattr(native_author, "run_command", run)
    code, value = native_author.run(arguments(), ["selected-owner"])
    assert code == (0 if status == "confirmed" else 1) and value == result(status)
    assert len(calls) == 2 and calls[1][1] == "pr-update"


@pytest.mark.parametrize(
    "damage",
    [
        {"schema": "other"},
        {"version": True},
        {"provider": "other"},
        {"ok": False},
        {"actions": {}},
        {"actions": {"title": {"status": "confirmed"}}},
        {"actions": {"title": {"status": "unknown"}}},
        {"error": "contradictory failure"},
        {"number": True},
        {"repo": "other/project"},
        {"publication_retained": False},
    ],
)
def test_malformed_owner_result_is_unconfirmed_and_never_replayed(
    monkeypatch: pytest.MonkeyPatch, damage: dict[str, Any],
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        value = support() if len(calls) == 1 else {**result(), **damage}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(native_author, "run_command", run)
    code, value = native_author.run(arguments(), ["selected-owner"])
    assert code == 1 and value["repo"] == REPO and value["number"] == 7
    assert value["actions"]["title"]["status"] == "unknown"
    assert "unconfirmed" in value["error"] or "selected" in value["error"]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "damage",
    [
        {"operations": {}},
        {"operations": {"metadata-update": "true"}},
        {"provider": None},
        {"version": 2},
        {"repo": "other/project"},
    ],
)
def test_old_or_rolled_back_owner_refuses_before_update(
    monkeypatch: pytest.MonkeyPatch, damage: dict[str, Any],
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({**support(), **damage}), "")

    monkeypatch.setattr(native_author, "run_command", run)
    code, value = native_author.run(arguments(), ["selected-owner"])
    assert code == 1 and value["repo"] == REPO and value["number"] == 7
    assert "does not advertise" in value["error"]
    assert len(calls) == 1


def test_response_lost_after_dispatch_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 0, json.dumps(support()), "")
        raise subprocess.TimeoutExpired(argv, 90)

    monkeypatch.setattr(native_author, "run_command", run)
    code, value = native_author.run(arguments(), ["selected-owner"])
    assert code == 1 and value["number"] == 7
    assert value["actions"]["title"]["status"] == "unknown"
    assert "unconfirmed" in value["error"]
    assert len(calls) == 2


def test_actual_process_boundary_keeps_body_file_as_a_file(tmp_path: Path) -> None:
    body = tmp_path / "description.md"
    text = '# "quoted" \u03bb\r\n' * 200
    body.write_bytes(text.encode("utf-8"))
    value = result()
    value["actions"] = {"description": {"status": "confirmed", "changed": True}}
    peer = tmp_path / "peer.py"
    peer.write_text(
        "import json, pathlib, sys\n"
        f"support = {support()!r}\n"
        f"value = {value!r}\n"
        "if sys.argv[1] == 'pr-author-capabilities':\n"
        "    print(json.dumps(support))\n"
        "else:\n"
        "    assert sys.argv[1] == 'pr-update'\n"
        "    arg = next(a for a in sys.argv if a.startswith('--body-file='))\n"
        "    data = pathlib.Path(arg.split('=', 1)[1]).read_bytes().decode('utf-8')\n"
        f"    assert data == {text!r}\n"
        "    assert not any(a.startswith('--body=') for a in sys.argv)\n"
        "    print(json.dumps(value))\n",
        encoding="utf-8",
    )
    args = arguments()
    args.title, args.body_file = None, str(body)
    original = deepcopy(args)
    code, payload = native_author.run(args, [sys.executable, str(peer)])
    assert code == 0 and payload == value and args == original
