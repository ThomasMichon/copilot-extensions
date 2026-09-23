"""CLI entry point for agent-pull-requests."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from . import __version__

_STATUS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { "
    "repository(owner: $owner, name: $name) { "
    "pullRequest(number: $number) { "
    "number title url state isDraft mergeable reviewDecision "
    "} } }"
)

_PR_URL_NUMBER_RE = re.compile(r"/pull/(\d+)\s*$")

_WAIT_TERMINAL_STATES = frozenset({"MERGED", "CLOSED"})


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


def _run_agent_worktrees_gh_raw(repo: str, gh_args: list[str]) -> subprocess.CompletedProcess[str]:
    command = [
        _agent_worktrees_command(),
        "repos",
        "gh",
        repo,
        "--",
        *gh_args,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
        check=False,
    )


def _run_agent_worktrees_gh(repo: str, gh_args: list[str]) -> dict[str, Any]:
    proc = _run_agent_worktrees_gh_raw(repo, gh_args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    return _parse_json_tail(proc.stdout)


def _strip_leading_diagnostic(stdout: str) -> str:
    """Drop a leading 'ambient auth' warning line 'repos gh' may emit on stdout.

    Mirrors the tolerance already applied to JSON output in
    ``_parse_json_tail``, but for plain-text gh output (e.g. the PR URL
    printed by ``gh pr create``, or ``gh pr merge``'s status lines).
    """
    lines = stdout.strip("\n").splitlines()
    while lines and "using ambient auth" in lines[0]:
        lines = lines[1:]
    return "\n".join(lines).strip()


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


def _github_create(
    repo: str, head: str, base: str | None, title: str, body: str, draft: bool
) -> dict[str, Any]:
    gh_args = ["pr", "create", "--repo", repo, "--head", head, "--title", title, "--body", body]
    if base:
        gh_args += ["--base", base]
    if draft:
        gh_args.append("--draft")
    proc = _run_agent_worktrees_gh_raw(repo, gh_args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    url = _strip_leading_diagnostic(proc.stdout)
    match = _PR_URL_NUMBER_RE.search(url)
    if not match:
        raise RuntimeError(f"could not parse a PR number from gh output: {url!r}")
    return {
        "repo": repo,
        "number": int(match.group(1)),
        "title": title,
        "url": url,
        "head": head,
        "base": base,
        "isDraft": draft,
    }


def _cmd_create(args: argparse.Namespace) -> int:
    try:
        result = _github_create(args.repo, args.head, args.base, args.title, args.body, args.draft)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "repo": args.repo}))
        else:
            print(f"agent-pull-requests: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"created:  {result['url']}")
        print(f"repo:     {result['repo']}")
        print(f"number:   #{result['number']}")
        print(f"head/base: {result['head']} -> {result['base'] or '(default)'}")
    return 0


_MERGE_METHOD_FLAGS = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}


def _github_merge(
    repo: str, number: int, method: str, auto: bool, delete_branch: bool
) -> dict[str, Any]:
    gh_args = ["pr", "merge", str(number), "--repo", repo, _MERGE_METHOD_FLAGS[method]]
    if auto:
        gh_args.append("--auto")
    if delete_branch:
        gh_args.append("--delete-branch")
    proc = _run_agent_worktrees_gh_raw(repo, gh_args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "unknown gh failure"
        raise RuntimeError(detail)
    return {
        "repo": repo,
        "number": number,
        "method": method,
        "auto": auto,
        "message": _strip_leading_diagnostic(proc.stdout) or _strip_leading_diagnostic(proc.stderr),
    }


def _cmd_merge(args: argparse.Namespace) -> int:
    try:
        result = _github_merge(args.repo, args.number, args.method, args.auto, args.delete_branch)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "repo": args.repo, "number": args.number}))
        else:
            print(f"agent-pull-requests: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["message"])
    return 0


def _cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    status: dict[str, Any] | None = None
    timed_out = False
    while True:
        try:
            status = _github_status(args.repo, args.number)
        except RuntimeError as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "repo": args.repo, "number": args.number}))
            else:
                print(f"agent-pull-requests: {exc}", file=sys.stderr)
            return 1
        if status["state"] in _WAIT_TERMINAL_STATES:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(args.interval)
    assert status is not None
    if args.json:
        print(json.dumps({**status, "timedOut": timed_out}, indent=2, sort_keys=True))
    else:
        print(_render_status(status))
        print(f"timed out:       {'yes' if timed_out else 'no'}")
    if timed_out:
        return 3
    return 0 if status["state"] == "MERGED" else 1


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

    create = subparsers.add_parser("create", help="open a pull request on owner/repo")
    create.add_argument("--repo", required=True, type=_validate_repo_slug, help="owner/name")
    create.add_argument("--head", required=True, help="head branch (must already be pushed)")
    create.add_argument("--base", default=None, help="base branch (defaults to the repo default)")
    create.add_argument("--title", required=True, help="pull request title")
    create.add_argument("--body", default="", help="pull request body (default: empty)")
    create.add_argument("--draft", action="store_true", help="open as a draft pull request")
    create.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    create.set_defaults(handler=_cmd_create)

    merge = subparsers.add_parser("merge", help="merge an existing pull request")
    merge.add_argument("--repo", required=True, type=_validate_repo_slug, help="owner/name")
    merge.add_argument("--number", required=True, type=int, help="pull request number")
    method_group = merge.add_mutually_exclusive_group()
    method_group.add_argument(
        "--squash", dest="method", action="store_const", const="squash",
        help="squash-merge (default)",
    )
    method_group.add_argument(
        "--merge", dest="method", action="store_const", const="merge", help="ordinary merge commit"
    )
    method_group.add_argument(
        "--rebase", dest="method", action="store_const", const="rebase", help="rebase-merge"
    )
    merge.set_defaults(method="squash")
    merge.add_argument(
        "--auto", action="store_true", help="enable auto-merge instead of merging immediately"
    )
    merge.add_argument(
        "--delete-branch", action="store_true", help="delete the head branch after merging"
    )
    merge.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    merge.set_defaults(handler=_cmd_merge)

    wait = subparsers.add_parser("wait", help="poll a pull request until it merges or closes")
    wait.add_argument("--repo", required=True, type=_validate_repo_slug, help="owner/name")
    wait.add_argument("--number", required=True, type=int, help="pull request number")
    wait.add_argument("--interval", type=float, default=15.0, help="poll interval in seconds")
    wait.add_argument("--timeout", type=float, default=600.0, help="give up after this many seconds")
    wait.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    wait.set_defaults(handler=_cmd_wait)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
