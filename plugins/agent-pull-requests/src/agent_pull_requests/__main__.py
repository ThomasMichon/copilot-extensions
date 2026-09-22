"""CLI entry point for agent-pull-requests."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any

from . import __version__

_STATUS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { "
    "repository(owner: $owner, name: $name) { "
    "pullRequest(number: $number) { "
    "number title url state isDraft mergeable reviewDecision "
    "} } }"
)


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _parse_repo_slug(value: str) -> tuple[str, str]:
    owner, sep, name = value.partition("/")
    if not sep or not owner or not name:
        raise argparse.ArgumentTypeError("repo must be 'owner/name'")
    return owner, name


def _validate_repo_slug(value: str) -> str:
    _parse_repo_slug(value)
    return value


def _agent_worktrees_command() -> str:
    command = shutil.which("agent-worktrees")
    if command:
        return command
    raise RuntimeError(
        "agent-worktrees command not found on PATH; this initial scaffold "
        "depends on 'agent-worktrees repos gh <repo> -- ...' for GitHub auth"
    )


def _run_agent_worktrees_gh(repo: str, gh_args: list[str]) -> dict[str, Any]:
    command = [
        _agent_worktrees_command(),
        "repos",
        "gh",
        repo,
        "--",
        *gh_args,
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    return _parse_json_tail(proc.stdout)


def _parse_json_tail(stdout: str) -> dict[str, Any]:
    """Parse the JSON payload from gh output that may be preceded by warnings.

    'agent-worktrees repos gh' can write a diagnostic (e.g. an ambient-auth
    token-fallback warning) to stdout ahead of the actual JSON response. Fall
    back to locating the last top-level JSON object/array in the output
    rather than assuming the whole stream is JSON.
    """
    stripped = stdout.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for marker in ("{", "["):
        idx = stripped.rfind(marker)
        while idx != -1:
            candidate = stripped[idx:]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                idx = stripped.rfind(marker, 0, idx)
    raise RuntimeError(
        "agent-worktrees repos gh returned non-JSON output: " + stripped[:200]
    )


def _github_status(repo: str, number: int) -> dict[str, Any]:
    owner, name = _parse_repo_slug(repo)
    payload = _run_agent_worktrees_gh(
        repo,
        [
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
            "-f",
            f"query={_STATUS_QUERY}",
        ],
    )
    repository = payload.get("data", {}).get("repository")
    pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
    if not isinstance(pull_request, dict):
        raise RuntimeError(f"GitHub did not return pull request #{number} for {repo}")
    return {
        "repo": repo,
        "number": int(pull_request.get("number", number)),
        "title": str(pull_request.get("title") or ""),
        "url": str(pull_request.get("url") or ""),
        "state": str(pull_request.get("state") or ""),
        "isDraft": bool(pull_request.get("isDraft", False)),
        "mergeable": str(pull_request.get("mergeable") or ""),
        "reviewDecision": str(pull_request.get("reviewDecision") or ""),
        "transport": "agent-worktrees repos gh",
    }


def _render_status(status: dict[str, Any]) -> str:
    review = status["reviewDecision"] or "(none)"
    mergeable = status["mergeable"] or "(unknown)"
    draft = "yes" if status["isDraft"] else "no"
    return "\n".join(
        [
            f"repo:            {status['repo']}",
            f"number:          #{status['number']}",
            f"state:           {status['state']}",
            f"mergeable:       {mergeable}",
            f"review decision: {review}",
            f"draft:           {draft}",
            f"title:           {status['title']}",
            f"url:             {status['url']}",
        ]
    )


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = _github_status(args.repo, args.number)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "repo": args.repo, "number": args.number}))
        else:
            print(f"agent-pull-requests: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(_render_status(status))
    return 0


def _cmd_not_implemented(args: argparse.Namespace) -> int:
    print(
        f"agent-pull-requests: '{args.command}' is not implemented yet in this "
        "scaffold slice; use agent-worktrees for existing worktree-bound PR flows.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-pull-requests",
        description="Cross-repository pull-request commands for owner/repo targets.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read one pull request by owner/repo + number")
    status.add_argument(
        "--repo",
        required=True,
        type=_validate_repo_slug,
        help="target GitHub repo as owner/name",
    )
    status.add_argument("--number", required=True, type=int, help="pull request number")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    status.set_defaults(handler=_cmd_status)

    for name, help_text in (
        ("create", "planned cross-repo PR creation surface (not implemented yet)"),
        ("merge", "planned cross-repo PR merge surface (not implemented yet)"),
        ("wait", "planned cross-repo PR wait/watch surface (not implemented yet)"),
    ):
        planned = subparsers.add_parser(name, help=help_text)
        planned.set_defaults(handler=_cmd_not_implemented)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
