"""Repository-owned GitHub review producer for the raw self-hosted loop."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

Runner = Callable[..., subprocess.CompletedProcess[str]]

_QUERY = """
query($owner:String!, $name:String!, $after:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(first:100, after:$after, states:OPEN, orderBy:{field:UPDATED_AT,direction:DESC}) {
      nodes {
        number title url updatedAt isDraft isCrossRepository headRefOid baseRefName
        authorAssociation author { login }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_ONE_QUERY = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      number title url updatedAt state isDraft isCrossRepository headRefOid baseRefName
      authorAssociation author { login }
    }
  }
}
"""


class ReviewerSourceError(RuntimeError):
    """The repository review source could not produce a trustworthy result."""


def _run_json(
    argv: list[str],
    *,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
) -> Any:
    command = argv
    if runner is subprocess.run:
        executable = shutil.which(argv[0])
        if executable is None:
            raise ReviewerSourceError(f"required command is not on PATH: {argv[0]}")
        if os.name == "nt" and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
            command = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                executable,
                *argv[1:],
            ]
        else:
            command = [executable, *argv[1:]]
    completed = runner(
        command, check=False, capture_output=True, text=True, env=env
    )
    if completed.returncode != 0:
        raise ReviewerSourceError(
            f"{' '.join(argv[:3])} failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise ReviewerSourceError(
            f"{' '.join(argv[:3])} returned invalid JSON: {exc}"
        ) from exc


def load_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path).resolve()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    required = {
        "repository",
        "lane",
        "acting_identity",
        "landing",
        "task_label",
        "guidance",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise ReviewerSourceError(
            f"policy is missing required field(s): {', '.join(missing)}"
        )
    if policy["landing"] not in {"self", "author"}:
        raise ReviewerSourceError("policy landing must be 'self' or 'author'")
    policy["_root"] = str(policy_path.parent.parent)
    return policy


def _gh_environment(
    policy: dict[str, Any], *, runner: Runner
) -> dict[str, str] | None:
    if runner is not subprocess.run:
        return None
    cached = policy.get("_gh_env")
    if isinstance(cached, dict):
        return cached
    gh = shutil.which("gh")
    if gh is None:
        raise ReviewerSourceError("required command is not on PATH: gh")
    token = subprocess.run(
        [gh, "auth", "token", "--user", str(policy["acting_identity"])],
        check=False,
        capture_output=True,
        text=True,
    )
    if token.returncode != 0 or not token.stdout.strip():
        raise ReviewerSourceError(
            f"could not resolve GitHub token for acting identity "
            f"{policy['acting_identity']!r}: {token.stderr.strip()}"
        )
    env = {**os.environ, "GH_TOKEN": token.stdout.strip()}
    identity = _run_json(["gh", "api", "user"], env=env)
    if str(identity.get("login") or "").casefold() != str(
        policy["acting_identity"]
    ).casefold():
        raise ReviewerSourceError(
            f"resolved GitHub identity {identity.get('login')!r} does not match "
            f"acting identity {policy['acting_identity']!r}"
        )
    policy["_gh_env"] = env
    return env


def _pull_requests(policy: dict[str, Any], *, runner: Runner) -> list[dict[str, Any]]:
    owner, name = policy["repository"].split("/", 1)
    rows = []
    after = ""
    while True:
        data = _run_json(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_QUERY}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-f",
                f"after={after}",
            ],
            runner=runner,
            env=_gh_environment(policy, runner=runner),
        )
        connection = data["data"]["repository"]["pullRequests"]
        rows.extend(connection["nodes"])
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            return rows
        after = str(page["endCursor"] or "")


def _pull_request(
    number: int,
    policy: dict[str, Any],
    *,
    runner: Runner,
) -> dict[str, Any] | None:
    owner, name = policy["repository"].split("/", 1)
    data = _run_json(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_ONE_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ],
        runner=runner,
        env=_gh_environment(policy, runner=runner),
    )
    return data["data"]["repository"]["pullRequest"]


def _completed_payload_refs(
    policy: dict[str, Any], *, runner: Runner
) -> set[str]:
    rows = _run_json(
        [
            "agent-dispatch",
            "list",
            "--repo",
            policy["lane"],
            "--status",
            "completed,abandoned,dead_letter",
            "--evaluator-ref",
            "copilot-extensions-review-lifecycle",
            "--limit",
            "10000",
        ],
        runner=runner,
    )
    return {
        row["payload_ref"]
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("payload_ref"), str)
        and row["payload_ref"].startswith("github-pr:")
    }


def eligibility(pr: dict[str, Any], policy: dict[str, Any]) -> tuple[bool, str]:
    author = str((pr.get("author") or {}).get("login") or "")
    if not author:
        return False, "pull request has no author identity"
    if pr.get("isDraft"):
        return False, "draft pull request"
    if author.casefold() == str(policy["acting_identity"]).casefold():
        return False, "authored by the acting identity"
    excluded_authors = {
        str(value).casefold() for value in policy.get("excluded_authors", [])
    }
    if author.casefold() in excluded_authors:
        return False, "author is excluded by policy"
    excluded_associations = {
        str(value).upper()
        for value in policy.get("excluded_author_associations", [])
    }
    if str(pr.get("authorAssociation") or "").upper() in excluded_associations:
        return False, "author association is excluded by policy"
    if pr.get("isCrossRepository") and not policy.get("allow_forks", False):
        return False, "fork pull requests are excluded by policy"
    return True, "eligible"


def _payload_ref(policy: dict[str, Any], pr: dict[str, Any]) -> str:
    return (
        f"github-pr:{policy['repository']}#{pr['number']}@{pr['headRefOid']}"
        f":base={pr['baseRefName']}"
    )


def _updated_after_watermark(
    pr: dict[str, Any], policy: dict[str, Any]
) -> bool:
    watermark = policy.get("discover_updated_since")
    if not watermark:
        return True
    updated = pr.get("updatedAt")
    if not isinstance(updated, str):
        return False
    threshold = datetime.fromisoformat(str(watermark).replace("Z", "+00:00"))
    observed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    if threshold.tzinfo is None:
        threshold = threshold.replace(tzinfo=timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed >= threshold


def render_task(
    pr: dict[str, Any],
    policy: dict[str, Any],
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    rendered = _run_json(
        [
            "agent-dispatch",
            "recipes",
            "render",
            "reviewer",
            "--param",
            f"repo={policy['repository']}",
            "--param",
            f"pr={pr['number']}",
            "--param",
            f"base={pr['baseRefName']}",
            "--param",
            f"land={policy['landing']}",
        ],
        runner=runner,
    )
    guidance = (
        Path(policy["_root"]) / policy["guidance"]
    ).read_text(encoding="utf-8")
    labels = list(
        dict.fromkeys([*rendered["labels"], str(policy["task_label"])])
    )
    return {
        "title": rendered["title"],
        "repo": policy["lane"],
        "prompt": (
            f"Acting GitHub identity: {policy['acting_identity']}\n\n"
            f"Reviewed head generation: {pr['headRefOid']}\n"
            "Before suspending for an author update, run:\n"
            "`uv run --no-project python "
            "plugins/copilot-extensions-harness/scripts/reviewer_source.py wait "
            f"{policy['repository']}#{pr['number']} --head {pr['headRefOid']} "
            f"--base {pr['baseRefName']} "
            "--policy .agent-dispatch/reviewer-loop.json`\n\n"
            f"{rendered['prompt']}\n\n{guidance}"
        ),
        "requires": rendered["requires"],
        "labels": labels,
        "dedup_key": (
            f"recipe:reviewer:target=github.com/"
            f"{policy['repository']}#{pr['number']}"
        ),
        "goal": rendered["goal"],
        "done_criteria": rendered["done_criteria"],
        "payload_ref": _payload_ref(policy, pr),
        "target_repo": policy["repository"],
    }


def discover(
    policy: dict[str, Any], *, runner: Runner = subprocess.run
) -> list[dict[str, Any]]:
    completed = _completed_payload_refs(policy, runner=runner)
    tasks = []
    for pr in _pull_requests(policy, runner=runner):
        allowed, reason = eligibility(pr, policy)
        if not allowed:
            print(f"skip #{pr.get('number')}: {reason}", file=sys.stderr)
            continue
        if not _updated_after_watermark(pr, policy):
            print(
                f"skip #{pr['number']}: missing/older than discovery watermark",
                file=sys.stderr,
            )
            continue
        if _payload_ref(policy, pr) in completed:
            print(
                f"skip #{pr['number']}: head generation already reviewed",
                file=sys.stderr,
            )
            continue
        tasks.append(render_task(pr, policy, runner=runner))
    return tasks


def _parse_change_ref(value: str, repository: str) -> int:
    raw = value.strip()
    if "#" in raw:
        repo, raw = raw.rsplit("#", 1)
        if repo and repo.casefold() != repository.casefold():
            raise ReviewerSourceError(
                f"change reference targets {repo!r}, expected {repository!r}"
            )
    try:
        number = int(raw)
    except ValueError as exc:
        raise ReviewerSourceError(
            f"change reference must be NUMBER or owner/repo#NUMBER: {value!r}"
        ) from exc
    if number <= 0:
        raise ReviewerSourceError("pull-request number must be positive")
    return number


def side_load(
    change_ref: str,
    policy: dict[str, Any],
    *,
    runner: Runner = subprocess.run,
) -> list[dict[str, Any]]:
    number = _parse_change_ref(change_ref, policy["repository"])
    pr = _pull_request(number, policy, runner=runner)
    if not pr or pr.get("state") != "OPEN":
        raise ReviewerSourceError(
            f"open pull request {policy['repository']}#{number} was not found"
        )
    allowed, reason = eligibility(pr, policy)
    if not allowed:
        print(f"skip #{number}: {reason}", file=sys.stderr)
        return []
    return [render_task(pr, policy, runner=runner)]


def wait_for_change(
    change_ref: str,
    reviewed_head: str,
    reviewed_base: str,
    policy: dict[str, Any],
    *,
    interval: float = 60.0,
    timeout: float = 7 * 24 * 60 * 60,
    runner: Runner = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait on trusted metadata until the reviewed head changes or closes."""
    number = _parse_change_ref(change_ref, policy["repository"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pr = _pull_request(number, policy, runner=runner)
        if not pr or pr.get("state") != "OPEN":
            return {"event": "closed", "number": number}
        current = str(pr.get("headRefOid") or "")
        current_base = str(pr.get("baseRefName") or "")
        if current and (
            current != reviewed_head or current_base != reviewed_base
        ):
            return {
                "event": "generation-changed",
                "number": number,
                "previous_head": reviewed_head,
                "previous_base": reviewed_base,
                "head": current,
                "base": current_base,
            }
        sleep(interval)
    raise ReviewerSourceError(
        f"timed out waiting for {policy['repository']}#{number} to change"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    discover_parser = sub.add_parser("discover")
    discover_parser.add_argument("--policy", required=True)
    side_parser = sub.add_parser("side-load")
    side_parser.add_argument("change_ref")
    side_parser.add_argument("--policy", required=True)
    wait_parser = sub.add_parser("wait")
    wait_parser.add_argument("change_ref")
    wait_parser.add_argument("--head", required=True)
    wait_parser.add_argument("--base", required=True)
    wait_parser.add_argument("--policy", required=True)
    wait_parser.add_argument("--interval", type=float, default=60.0)
    wait_parser.add_argument("--timeout", type=float, default=7 * 24 * 60 * 60)
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.command == "discover":
            result = discover(policy)
        elif args.command == "side-load":
            result = side_load(args.change_ref, policy)
        else:
            result = wait_for_change(
                args.change_ref,
                args.head,
                args.base,
                policy,
                interval=args.interval,
                timeout=args.timeout,
            )
    except (OSError, ValueError, KeyError, ReviewerSourceError) as exc:
        print(f"reviewer-source: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
