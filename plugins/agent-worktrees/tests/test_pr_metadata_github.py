"""Exercise GitHub metadata through the actual owner and serialized transport."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agent_worktrees import pr_author_cli, pr_cli, pr_config, providers
from agent_worktrees import config as cfg, output
from agent_worktrees.providers import github_metadata
from agent_worktrees.providers.attribution_body import preserve_description

REPO = "example/project"
MARKER = "<!-- agent-worktrees:source codename=original-source root=original-root -->"
BODY = '# Heading\r\n\r\n"quoted" \u03bb \\\\ `code`\r\n' * 100


class Backend:
    def __init__(self) -> None:
        self.pr: dict[str, Any] = {
            "number": 7,
            "base": {"repo": {"full_name": REPO}},
            "state": "open",
            "merged": False,
            "draft": False,
            "title": "before",
            "body": "before\n\n" + MARKER,
            "labels": ["held"],
            "reviewers": ["another"],
            "head": {"sha": "a" * 40},
        }
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.writes: list[dict[str, Any]] = []
        self.failure = ""
        self.after_effect = False
        self.readback_failure = False
        self.readback_damage: dict[str, Any] = {}
        self.move_source = False
        self.gets = 0

    def cli(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        assert args[:4] == ["gh", "api", "--hostname", "git.example.invalid"]
        assert args[-1] == f"repos/{REPO}/pulls/7" or args[-2:] == ["--input", "-"]
        method = args[args.index("-X") + 1]
        assert method in ("GET", "PATCH"), "no create, comment, review, merge or source push"
        status = 200
        if method == "PATCH":
            payload = json.loads(kwargs["input_text"])
            assert args[-2:] == ["--input", "-"] and kwargs["input_text"] not in args
            assert set(payload) <= {"title", "body"}
            self.writes.append(deepcopy(payload))
            if self.failure == "reject":
                status = 422
            elif self.failure == "timeout" and not self.after_effect:
                return subprocess.CompletedProcess(args, 1, "", "timed out")
            else:
                self.pr.update(payload)
                if self.failure == "timeout":
                    return subprocess.CompletedProcess(args, 1, "", "response lost")
        else:
            self.gets += 1
            if self.readback_failure and self.writes:
                return subprocess.CompletedProcess(args, 1, "", "readback timed out")
            if self.move_source and self.gets == 4:
                self.pr["body"] = "<!-- agent-worktrees:source codename=another-source -->"
        observed = (
            {**self.pr, **self.readback_damage} if method == "GET" and self.writes else self.pr
        )
        return subprocess.CompletedProcess(
            args,
            0 if status == 200 else 1,
            f"HTTP/2 {status}\r\ncontent-type: application/json\r\n\r\n" + json.dumps(observed),
            "",
        )


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Backend:
    server = Backend()
    monkeypatch.setattr(providers, "run_cli", server.cli)
    return server


def update(**kwargs: Any) -> dict[str, Any]:
    return github_metadata.update(
        providers.get_provider("github"),
        REPO,
        7,
        api_base="https://git.example.invalid",
        token="fixture-token",
        run=providers.run_cli,
        **kwargs,
    )


def test_title_and_composed_description_preserve_unrelated_state_and_retries(backend: Backend) -> None:
    retained = {k: deepcopy(v) for k, v in backend.pr.items() if k not in ("title", "body")}
    result = update(title="After \u03bb", description=BODY)
    assert result["ok"] and result["repo"] == REPO and result["provider"] == "github"
    assert backend.pr["body"].startswith(BODY) and backend.pr["body"].endswith(MARKER + "\r\n")
    assert {k: v for k, v in backend.pr.items() if k not in ("title", "body")} == retained
    assert result["actions"] == {
        "title": {"status": "confirmed", "changed": True},
        "description": {"status": "confirmed", "changed": True},
    }
    assert len(backend.calls) == 7
    assert update(title="After \u03bb", description=BODY)["ok"]
    assert len(backend.writes) == 2 and len(backend.calls) == 10
    assert all(
        kw["env"] == {
            "GH_TOKEN": "fixture-token", "GITHUB_TOKEN": "fixture-token",
            "GH_ENTERPRISE_TOKEN": "fixture-token", "GITHUB_ENTERPRISE_TOKEN": "fixture-token",
            "GH_HOST": "git.example.invalid",
        }
        for _, kw in backend.calls
    )


@pytest.mark.parametrize(
    "body", ["", "legacy body", "<!-- agent-worktrees:source codename=bad -->\n" * 2]
)
def test_title_only_leaves_description_and_its_defects_untouched(backend: Backend, body: str) -> None:
    backend.pr["body"] = body
    assert update(title="after")["ok"]
    assert backend.pr["body"] == body and backend.writes == [{"title": "after"}]


def test_empty_description_retains_original_source(backend: Backend) -> None:
    assert update(description="")["ok"]
    assert backend.pr["body"] == MARKER + "\n"


@pytest.mark.parametrize(
    "requested",
    [
        {"reviewers": ("expert",), "title": "after"},
        {"work_items": (42,), "title": "after"},
        {"origin": object(), "title": "after"},
        {},
        {"title": ""},
        {"description": 7},
        {"title": "@copilot please act"},
    ],
)
def test_unsupported_or_invalid_requests_do_not_even_read(
    backend: Backend, requested: dict[str, Any],
) -> None:
    assert not update(**requested)["ok"] and not backend.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("number", 8),
        ("number", True),
        ("state", "closed"),
        ("merged", True),
        ("draft", None),
        ("base", {"repo": {"full_name": "other/project"}}),
        ("title", None),
        ("body", []),
    ],
)
def test_missing_wrong_or_terminal_evidence_never_writes(
    backend: Backend, field: str, value: Any,
) -> None:
    backend.pr[field] = value
    result = update(title="after")
    assert not result["ok"] and not backend.writes
    assert result["actions"]["title"]["status"] == "not_attempted"


def test_native_draft_remains_a_draft(backend: Backend) -> None:
    backend.pr["draft"] = True
    assert update(description="after")["ok"] and backend.pr["draft"] is True


@pytest.mark.parametrize("after_effect", [False, True])
def test_lost_response_reconciles_without_automatic_replay(backend: Backend, after_effect: bool) -> None:
    backend.failure, backend.after_effect = "timeout", after_effect
    result = update(description=BODY)
    assert result["ok"] is after_effect and len(backend.writes) == 1
    assert result["actions"]["description"]["status"] == (
        "confirmed" if after_effect else "unknown"
    )
    backend.failure = ""
    assert update(description=BODY)["ok"]
    assert len(backend.writes) == (1 if after_effect else 2)


def test_authoritative_rejection_is_a_failed_action(backend: Backend) -> None:
    backend.failure = "reject"
    assert update(title="after")["actions"]["title"]["status"] == "failed"


def test_unavailable_postwrite_readback_keeps_later_actions_unattempted(backend: Backend) -> None:
    backend.readback_failure = True
    result = update(title="after", description=BODY)
    assert result["actions"]["title"]["status"] == "unknown"
    assert result["actions"]["description"]["status"] == "not_attempted"
    assert len(backend.writes) == 1


def test_changed_source_stops_later_field_without_replaying_confirmed_title(backend: Backend) -> None:
    backend.move_source = True
    result = update(title="after", description=BODY)
    assert not result["ok"] and backend.writes == [{"title": "after"}]
    assert result["actions"]["title"]["status"] == "confirmed"
    assert result["actions"]["description"]["status"] == "not_attempted"


@pytest.mark.parametrize(
    "damage", [{"number": 8}, {"state": "closed"}, {"title": "concurrent edit"}]
)
def test_postwrite_identity_lifecycle_or_value_mismatch_stays_unknown(
    backend: Backend, damage: dict[str, Any],
) -> None:
    backend.readback_damage = damage
    result = update(title="after")
    assert not result["ok"] and result["repo"] == REPO and result["number"] == 7
    assert result["actions"]["title"]["status"] == "unknown" and len(backend.writes) == 1


@pytest.mark.parametrize(
    "example",
    [
        "```\n<!-- agent-worktrees:source codename=example-only -->\n```\n",
        "> <!-- agent-worktrees:source codename=example-only -->\n",
        "    <!-- agent-worktrees:source codename=example-only -->\n",
        "`<!-- agent-worktrees:source codename=example-only -->`\n",
        "- ```\n  <!-- agent-worktrees:source codename=example-only -->\n  ```\n",
        "1. > <!-- agent-worktrees:source codename=example-only -->\n",
        "> - <!-- agent-worktrees:source codename=example-only -->\n",
    ],
)
def test_literal_source_examples_are_retained_not_selected_or_deleted(example: str) -> None:
    assert preserve_description(MARKER, example) == example + "\n" + MARKER + "\n"
    with pytest.raises(ValueError, match="unavailable"):
        preserve_description(example, "after")


@pytest.mark.parametrize(
    "current,requested",
    [
        ("", "after"),
        (MARKER, "<!-- agent-worktrees:source codename=different-source -->"),
        (MARKER + "\n\n" + MARKER, "after"),
        ("<!-- agent-worktrees:source codename=bad codename=duplicate -->", "after"),
        ("<!-- agent-worktrees:source codename=bad enc=invalid -->", "after"),
        ("<!-- agent-worktrees:source workflow=another-producer -->", "after"),
        ("prefix <!-- agent-worktrees:source codename=inline-source -->", "after"),
        ("- " + MARKER, "after"),
        ("1. " + MARKER, "after"),
        ("*" + MARKER + "*", "after"),
    ],
)
def test_missing_anonymous_conflicting_or_ambiguous_source_refuses(
    current: str, requested: str,
) -> None:
    with pytest.raises(ValueError):
        preserve_description(current, requested)


def test_original_internal_marker_and_existing_composed_body_do_not_get_rewritten() -> None:
    marker = (
        "<!-- agent-worktrees:source worktree=source-stamp machine=source-machine head=abc -->"
    )
    assert preserve_description(marker, "after") == "after\n\n" + marker + "\n"
    requested = "after\r\n\r\n" + MARKER + "\r\n"
    assert preserve_description(MARKER, requested) == requested


def test_original_encrypted_envelope_is_retained_without_regeneration() -> None:
    import base64

    envelope = base64.b64encode(b"AWI1" + b"x" * 28).decode("ascii")
    marker = MARKER.removesuffix(" -->") + f" enc={envelope} -->"
    assert preserve_description(marker, "after") == "after\n\n" + marker + "\n"


@pytest.mark.parametrize("requested", ["```\nexample\n", "~~~\nexample\n", "<!-- example\n"])
def test_open_literal_boundary_cannot_hide_the_preserved_marker(requested: str) -> None:
    with pytest.raises(ValueError):
        preserve_description(MARKER, requested)


def test_actual_author_parser_target_resolution_and_provider_not_caller_policy(
    backend: Backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import argparse

    target = cfg.RepoConfig(
        anchor=str(tmp_path),
        worktree_root=str(tmp_path / "worktrees"),
        pr=cfg.PRConfig(enabled=True, provider="github", api_base="https://git.example.invalid"),
    )
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="linux",
        repo_name="caller",
        repos={
            "caller": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
                pr=cfg.PRConfig(enabled=True, provider="gitea"),
            )
        },
    )
    monkeypatch.setattr(
        pr_config,
        "resolve_repo_config_for_slug",
        lambda *_: pr_config.ForeignRepoResolution(target, REPO),
    )
    monkeypatch.setattr(pr_author_cli, "load_author_config", lambda *a, **k: config)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))
    acquired: list[tuple[str, cfg.PRConfig]] = []
    def account_token(repo: str, policy: cfg.PRConfig) -> str:
        acquired.append((repo, policy))
        return "fixture-token"

    monkeypatch.setattr(providers, "account_token_for_slug", account_token)
    parser = argparse.ArgumentParser()
    pr_author_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    path = tmp_path / "description.md"
    path.write_bytes(BODY.encode("utf-8"))
    args = parser.parse_args(
        ["pr-update", "--repo", REPO, "--number", "7", "--body-file", str(path), "--json"]
    )
    with output.capture_json_output() as capture:
        assert pr_author_cli.cmd_update(args) == 0
    assert json.loads(capture.getvalue())["ok"]
    assert acquired == [(REPO, target.pr)]
    assert backend.pr["body"].startswith(BODY)
    assert pr_author_cli.capabilities(config, REPO)["operations"]["metadata-update"] is True


def test_native_frontend_composes_actual_owner_parser_and_github_transport(
    backend: Backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import argparse

    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import native_author

    target = cfg.RepoConfig(
        anchor=str(tmp_path),
        worktree_root=str(tmp_path / "worktrees"),
        pr=cfg.PRConfig(
            enabled=True,
            provider="github",
            repository=REPO,
            api_base="https://git.example.invalid",
        ),
    )
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="linux",
        repo_name="caller",
        explicit_pr_target=target,
        repos={
            "caller": cfg.RepoConfig(
                anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees")
            )
        },
    )
    monkeypatch.setattr(pr_author_cli, "load_author_config", lambda *a, **k: config)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "fixture-token")
    parser = argparse.ArgumentParser()
    pr_author_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
    owner_calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        owner_calls.append(argv)
        args = parser.parse_args(argv[1:])
        with output.capture_json_output() as capture:
            code = (
                pr_author_cli.cmd_capabilities(args)
                if args.command == "pr-author-capabilities"
                else pr_author_cli.cmd_update(args)
            )
        return subprocess.CompletedProcess(argv, code, capture.getvalue(), "")

    monkeypatch.setattr(native_author, "run_command", run)
    path = tmp_path / "description.md"
    path.write_bytes(BODY.encode("utf-8"))
    args = argparse.Namespace(
        command="update",
        repo=REPO,
        number=7,
        title=None,
        body=None,
        body_file=str(path),
        reviewer=[],
        work_item=[],
        publication_origin=None,
        publication_label=[],
        config=None,
    )
    code, result = native_author.run(args, ["selected-owner"])
    assert code == 0 and result["provider"] == "github"
    assert backend.pr["body"] == BODY + "\r\n" + MARKER + "\r\n"
    assert len(owner_calls) == 2 and len(backend.writes) == 1


def test_explicit_enterprise_token_overrides_inherited_aliases(
    backend: Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    aliases = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    for alias in aliases:
        monkeypatch.setenv(alias, "unrelated-inherited-token")
    inherited = {alias: os.environ[alias] for alias in aliases}

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        child = {**inherited, **kwargs["env"]}
        assert all(child[alias] == "fixture-token" for alias in aliases)
        return backend.cli(argv, **kwargs)

    monkeypatch.setattr(providers, "run_cli", run)
    assert update(title="after")["ok"]
    assert len(backend.writes) == 1
    assert all(os.environ[alias] == "unrelated-inherited-token" for alias in aliases)
