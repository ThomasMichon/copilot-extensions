"""Gitea metadata exercises the selected owner and actual curl serialization."""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agent_worktrees import config as cfg, output, pr_author_cli, pr_cli, providers
from agent_worktrees.providers import gitea, gitea_metadata

REPO = "example/project"
BASE = "https://forge.example.invalid/scm"
MARKER = "<!-- agent-worktrees:source codename=original-source root=original-root -->"
BODY = '# "quoted" \u03bb \\\\ `code`\r\n\r\n' * 40


class Backend:
    def __init__(self) -> None:
        self.pr: dict[str, Any] = {
            "number": 7, "base": {"repo": {"full_name": REPO}},
            "state": "open", "merged": False, "draft": False,
            "title": "before", "body": "before\n\n" + MARKER,
            "labels": ["held"], "reviewers": ["another"],
            "head": {"sha": "a" * 40},
        }
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.writes: list[dict[str, Any]] = []
        self.failure = ""
        self.after_effect = False
        self.readback_damage: dict[str, Any] = {}
        self.move_source = False
        self.gets = 0

    def cli(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        assert args[0] == "curl"
        method = args[args.index("-X") + 1]
        assert args[args.index("-X") + 2] == f"{BASE}/api/v1/repos/{REPO}/pulls/7"
        assert method in ("GET", "PATCH"), "no create, comment, review, merge or source push"
        assert kwargs["preserve_line_endings"] is True
        authorization = next(a for a in args if a.startswith("Authorization: "))
        assert authorization.split() == ["Authorization:", "token", "fixture-token"]
        status = 200
        if method == "PATCH":
            payload = json.loads(args[args.index("-d") + 1])
            assert len(payload) == 1 and set(payload) <= {"title", "body"}
            self.writes.append(deepcopy(payload))
            if self.failure == "reject":
                status = 422
            elif self.failure == "timeout" and not self.after_effect:
                return subprocess.CompletedProcess(args, 28, "", "timed out")
            else:
                self.pr.update(payload)
                if self.failure == "timeout":
                    return subprocess.CompletedProcess(args, 28, "", "response lost")
        else:
            self.gets += 1
            if self.failure == "readback" and self.writes:
                return subprocess.CompletedProcess(args, 28, "", "readback timed out")
            if self.move_source and self.gets == 4:
                self.pr["body"] = "<!-- agent-worktrees:source codename=another-source -->"
        observed = {**self.pr, **self.readback_damage} if method == "GET" and self.writes else self.pr
        return subprocess.CompletedProcess(args, 0, json.dumps(observed) + f"\n{status}", "")


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Backend:
    server = Backend()
    monkeypatch.setattr(gitea, "run_cli", server.cli)
    return server


def update(**kwargs: Any) -> dict[str, Any]:
    return gitea_metadata.update(
        providers.get_provider("gitea"), REPO, 7,
        api_base=BASE, token="fixture-token", **kwargs,
    )


def test_selected_fields_exact_body_and_no_replay_of_satisfied_values(backend: Backend) -> None:
    retained = {k: deepcopy(v) for k, v in backend.pr.items() if k not in ("title", "body")}
    result = update(title="After \u03bb", description=BODY)
    assert result["ok"] and result["provider"] == "gitea"
    assert backend.pr["body"] == BODY + MARKER + "\r\n"
    assert {k: v for k, v in backend.pr.items() if k not in ("title", "body")} == retained
    assert result["actions"] == {
        "title": {"status": "confirmed", "changed": True},
        "description": {"status": "confirmed", "changed": True},
    }
    assert len(backend.calls) == 7
    again = update(title="After \u03bb", description=BODY)
    assert again["ok"] and all(a["changed"] is False for a in again["actions"].values())
    assert len(backend.writes) == 2 and len(backend.calls) == 10


@pytest.mark.parametrize("body", ["", "legacy", MARKER + "\n\n" + MARKER])
def test_title_only_retains_body_including_independent_source_defects(backend: Backend, body: str) -> None:
    backend.pr["body"] = body
    assert update(title="after")["ok"]
    assert backend.pr["body"] == body and backend.writes == [{"title": "after"}]


@pytest.mark.parametrize(
    "requested", [{}, {"title": ""}, {"description": 7}, {"title": "@copilot act"},
                  {"reviewers": ("reviewer",)}, {"work_items": (42,)}, {"origin": object()}],
)
def test_invalid_or_unsupported_options_do_not_read(backend: Backend, requested: dict[str, Any]) -> None:
    assert not update(**requested)["ok"] and not backend.calls


@pytest.mark.parametrize(
    "damage", [{"number": True}, {"number": 8}, {"state": "closed"}, {"merged": True},
               {"base": {"repo": {"full_name": "other/project"}}}, {"title": None}, {"body": []}],
)
def test_wrong_malformed_or_terminal_evidence_never_writes(
    backend: Backend, damage: dict[str, Any],
) -> None:
    backend.pr.update(damage)
    assert update(title="after")["actions"]["title"]["status"] == "not_attempted"
    assert not backend.writes


@pytest.mark.parametrize(
    "current,draft,requested,prefixes,allowed",
    [
        ("before", False, "after", (), True),
        ("WIP: before", True, "WIP: after", (), True),
        ("[WIP] before", True, "[WIP] after", (), True),
        ("custom before", True, "custom after", ("custom ",), True),
        ("Draft: before", False, "Draft: after", (), True),
        ("before", False, "WIP: after", (), False),
        ("WIP: before", True, "after", (), False),
        ("WIP: before", True, "Draft: after", (), False),
        ("custom before", True, "custom after", (), False),
        ("WIP: before", False, "WIP: after", (), False),
        ("before", None, "after", (), False),
        ("before", False, "custom after", ("custom ",), False),
        ("hold before", False, "after", ("hold ",), False),
    ],
)
def test_title_preserves_native_and_configured_draft_wip_semantics(
    backend: Backend, current: str, draft: bool | None,
    requested: str, prefixes: tuple[str, ...], allowed: bool,
) -> None:
    backend.pr.update(title=current, draft=draft)
    result = update(title=requested, wip_title_prefixes=prefixes)
    assert result["ok"] is allowed and len(backend.writes) == int(allowed)
    assert backend.pr["draft"] is draft
    if not allowed:
        assert result["actions"]["title"]["status"] == "not_attempted"


def test_body_only_does_not_require_known_title_prefix_or_draft(backend: Backend) -> None:
    backend.pr.update(title="custom before", draft=None)
    assert update(description="")["ok"]
    assert backend.pr["body"] == MARKER + "\n" and backend.pr["title"] == "custom before"


@pytest.mark.parametrize(
    "marker", [MARKER, "<!-- agent-worktrees:source worktree=original-stamp -->",
               MARKER.removesuffix(" -->") + " enc="
               + base64.b64encode(b"AWI1" + b"x" * 28).decode("ascii") + " -->"],
)
def test_real_body_composition_retains_original_marker_and_literal_examples(
    backend: Backend, marker: str,
) -> None:
    backend.pr["body"] = "original\n\n" + marker
    example = "```\n<!-- agent-worktrees:source codename=example-only -->\n```\n"
    assert update(description=example)["ok"]
    assert backend.pr["body"] == example + "\n" + marker + "\n"


@pytest.mark.parametrize(
    "current,requested",
    [("", "after"), (MARKER, "<!-- agent-worktrees:source codename=different-source -->"),
     (MARKER + "\n\n" + MARKER, "after"),
     ("<!-- agent-worktrees:source codename=bad enc=invalid -->", "after"),
     (MARKER, "```\nopen example\n")],
)
def test_unrecoverable_or_conflicting_source_refuses_before_any_action(
    backend: Backend, current: str, requested: str,
) -> None:
    backend.pr["body"] = current
    result = update(title="after", description=requested)
    assert not result["ok"] and not backend.writes
    assert all(a["status"] == "not_attempted" for a in result["actions"].values())


@pytest.mark.parametrize("after_effect", [False, True])
def test_lost_response_reconciles_without_replaying(backend: Backend, after_effect: bool) -> None:
    backend.failure, backend.after_effect = "timeout", after_effect
    result = update(description=BODY)
    assert result["ok"] is after_effect and len(backend.writes) == 1
    assert result["actions"]["description"]["status"] == ("confirmed" if after_effect else "unknown")


@pytest.mark.parametrize(
    "failure,damage,status",
    [("reject", {}, "failed"), ("readback", {}, "unknown"),
     ("", {"draft": True}, "unknown"), ("", {"number": 8}, "unknown"),
     ("", {"title": "concurrent edit"}, "unknown")],
)
def test_unresolved_first_action_retains_later_action_and_selected_pr(
    backend: Backend, failure: str, damage: dict[str, Any], status: str,
) -> None:
    backend.failure, backend.readback_damage = failure, damage
    result = update(title="after", description=BODY)
    assert not result["ok"] and result["repo"] == REPO and result["number"] == 7
    assert result["actions"]["title"]["status"] == status
    assert result["actions"]["description"]["status"] == "not_attempted"
    assert len(backend.writes) == 1


def test_source_drift_keeps_confirmed_title_without_overwriting_body(backend: Backend) -> None:
    backend.move_source = True
    result = update(title="after", description=BODY)
    assert not result["ok"] and backend.writes == [{"title": "after"}]
    assert result["actions"]["title"]["status"] == "confirmed"
    assert result["actions"]["description"]["status"] == "not_attempted"


def test_native_frontend_real_owner_parser_target_policy_and_gitea_transport(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import native_author

    target = cfg.RepoConfig(
        anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        pr=cfg.PRConfig(enabled=True, provider="gitea", repository=REPO, api_base=BASE),
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux", repo_name="caller",
        explicit_pr_target=target,
        repos={"caller": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
            pr=cfg.PRConfig(enabled=True, provider="github"),
        )},
    )
    monkeypatch.setattr(pr_author_cli, "load_author_config", lambda *a, **k: config)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))
    acquired: list[tuple[str, cfg.PRConfig]] = []

    def token(repo: str, policy: cfg.PRConfig) -> str:
        acquired.append((repo, policy))
        return "fixture-token"

    monkeypatch.setattr(providers, "account_token_for_slug", token)
    parser = argparse.ArgumentParser()
    pr_author_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    owner_calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        owner_calls.append(argv)
        args = parser.parse_args(argv[1:])
        with output.capture_json_output() as capture:
            code = (pr_author_cli.cmd_capabilities(args) if args.command == "pr-author-capabilities"
                    else pr_author_cli.cmd_update(args))
        return subprocess.CompletedProcess(argv, code, capture.getvalue(), "")

    monkeypatch.setattr(native_author, "run_command", run)
    path = tmp_path / "description.md"
    path.write_bytes(BODY.encode("utf-8"))
    args = argparse.Namespace(
        command="update", repo=REPO, number=7, title=None, body=None, body_file=str(path),
        reviewer=[], work_item=[], publication_origin=None, publication_label=[], config=None,
    )
    code, result = native_author.run(args, ["selected-owner"])
    assert code == 0 and result["provider"] == "gitea" and len(owner_calls) == 2
    assert backend.pr["body"] == BODY + MARKER + "\r\n"
    assert acquired == [(REPO, target.pr)]
