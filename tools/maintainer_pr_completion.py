"""Bounded trusted-source reconciliation; checked-in activation defaults off."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from maintainer_pr_completion_github import (
    EvidenceUnavailable, GitHubEvidence, obj, sha, text,
)
from maintainer_pr_completion_policy import CompletionPolicy, CompletionSnapshot, decide

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "maintainer-pr-completion.json"
CONFLICT_MARKER = "<!-- maintainer-pr-completion:conflict -->"
_REBASE = """
mutation($id:ID!,$head:GitObjectID!) {
  updatePullRequestBranch(input:{
    pullRequestId:$id,expectedHeadOid:$head,updateMethod:REBASE
  }) { pullRequest { id headRefOid baseRefName } }
}
"""


@dataclass(frozen=True)
class Settings:
    mode: str
    policy: CompletionPolicy
    app_slug: str | None
    app_id: int | None


class MutationUncertain(EvidenceUnavailable):
    """A write was attempted; lack of confirmation is not proof it did not apply."""


def settings(path: Path = CONFIG) -> Settings:
    data = obj(json.loads(path.read_text(encoding="utf-8")), "completion configuration")
    required = {
        "version", "mode", "repository", "base_ref", "consent_label",
        "hold_labels", "wip_title_prefixes", "app_slug", "app_id",
    }
    if set(data) != required or type(data["version"]) is not int or data["version"] != 1:
        raise EvidenceUnavailable("unsupported completion configuration")
    if data["mode"] not in ("disabled", "report-only", "enabled"):
        raise EvidenceUnavailable("invalid activation mode")
    for key in ("repository", "base_ref", "consent_label"):
        text(data[key], key)
    if data["base_ref"] != "dev":
        raise EvidenceUnavailable("this controller admits Dev only")
    for key in ("hold_labels", "wip_title_prefixes"):
        if not isinstance(data[key], list) or any(
            not isinstance(value, str) or not value.strip() for value in data[key]
        ):
            raise EvidenceUnavailable(f"invalid {key}")
    slug, app_id = data["app_slug"], data["app_id"]
    if slug is not None and (
        not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
    ):
        raise EvidenceUnavailable("invalid approved App slug")
    if app_id is not None and (type(app_id) is not int or app_id <= 0):
        raise EvidenceUnavailable("invalid approved App identity")
    if data["mode"] == "enabled" and (slug is None or app_id is None):
        raise EvidenceUnavailable("enabled completion requires an approved App binding")
    return Settings(
        mode=data["mode"], app_slug=slug, app_id=app_id,
        policy=CompletionPolicy(
            repository=data["repository"], base_ref=data["base_ref"],
            consent_label=data["consent_label"], hold_labels=tuple(data["hold_labels"]),
            wip_title_prefixes=tuple(data["wip_title_prefixes"]),
        ),
    )


def require_trusted_context(config: Settings) -> None:
    """Network observations use the workflow's repository-scoped credential."""
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REPOSITORY") != config.policy.repository
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_WORKFLOW_REF") != (
            f"{config.policy.repository}/.github/workflows/"
            "maintainer-pr-completion.yml@refs/heads/main"
        )
    ):
        raise EvidenceUnavailable("trusted Main workflow context is unavailable")
    sha(os.environ.get("GITHUB_WORKFLOW_SHA"), "trusted workflow commit")


def require_writer_context(config: Settings) -> None:
    """Only trusted Main plus an approved, action-minted App may reach a writer."""
    require_trusted_context(config)
    if (
        config.mode != "enabled" or config.app_id is None or config.app_slug is None
        or os.environ.get("COMPLETION_APP_SLUG") != config.app_slug
        or os.environ.get("COMPLETION_APP_ID") != str(config.app_id)
    ):
        raise EvidenceUnavailable("approved App/trusted Main writer binding is unavailable")
    installation = os.environ.get("COMPLETION_INSTALLATION_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", installation):
        raise EvidenceUnavailable("App installation identity is unavailable")


def invoke(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=45, check=False,
        env={**os.environ, "GH_HOST": "github.com"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def candidates(client: GitHubEvidence, number: int | None, consent_label: str) -> list[int]:
    if number is not None:
        if type(number) is not int or number <= 0:
            raise EvidenceUnavailable("invalid targeted PR")
        return [number]
    pulls = client.pages(f"repos/{client.repository}/pulls?state=open&base=dev")
    result = []
    seen: set[int] = set()
    for pull in pulls:
        candidate = pull.get("number")
        if type(candidate) is not int or candidate <= 0 or candidate in seen:
            raise EvidenceUnavailable("invalid or duplicate candidate identity")
        seen.add(candidate)
        labels = pull.get("labels")
        if not isinstance(labels, list):
            raise EvidenceUnavailable("candidate label evidence is unavailable")
        names = {text(obj(label, "candidate label").get("name"), "candidate label").casefold()
                 for label in labels}
        if consent_label.casefold() in names:
            result.append(candidate)
    if len(result) > 100:
        raise EvidenceUnavailable("catch-up exceeds 100 PRs; use targeted manual recovery")
    return result


def _same_candidate(left: CompletionSnapshot, right: CompletionSnapshot) -> bool:
    return (
        left.repository, left.number, left.node_id, left.head_sha, left.base_ref, left.base_sha,
    ) == (
        right.repository, right.number, right.node_id, right.head_sha, right.base_ref, right.base_sha,
    )


def _require_fresh_scope(
    client: GitHubEvidence, snapshot: CompletionSnapshot, policy: CompletionPolicy,
) -> None:
    current, metadata = client.scope_snapshot(snapshot.number)
    if not _same_candidate(snapshot, current) or decide(current, policy).action == "skip":
        raise EvidenceUnavailable("head/base/consent/scope changed before the write")
    client.require_live_base(current)
    author = text(obj(metadata.get("user"), "PR author").get("login"), "PR author login")
    if client.invited_collaborator(author) is not True:
        raise EvidenceUnavailable("author invitation changed before the write")


def _fenced_write(
    client: GitHubEvidence, snapshot: CompletionSnapshot, policy: CompletionPolicy,
    endpoint: str, *fields: str,
) -> object:
    _require_fresh_scope(client, snapshot, policy)
    return client.write(endpoint, *fields)


def _conflict_notice(
    client: GitHubEvidence, snapshot: CompletionSnapshot, policy: CompletionPolicy, bot: str,
) -> str:
    comments = client.pages(f"repos/{client.repository}/issues/{snapshot.number}/comments")
    owned = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str):
            raise EvidenceUnavailable("comment history is incomplete")
        if not body.startswith(CONFLICT_MARKER):
            continue
        user = obj(comment.get("user"), "conflict notice author")
        if user.get("login") != bot or user.get("type") != "Bot":
            continue
        if type(comment.get("id")) is not int or comment["id"] <= 0:
            raise EvidenceUnavailable("conflict notice identity is unavailable")
        owned.append(comment)
    if len(owned) > 1:
        raise EvidenceUnavailable("multiple owned conflict notices require reconciliation")
    body = (
        f"{CONFLICT_MARKER}\n"
        "GitHub reports a merge conflict for this opted-in PR. The original submitter "
        "must resolve it in the owning source worktree and publish the updated branch. "
        "Completion remains subject to fresh current-head Copilot approval and required CI.\n\n"
        f"Observed head: `{snapshot.head_sha}`; base: `{snapshot.base_ref}` "
        f"at `{snapshot.base_sha}`."
    )
    if owned and owned[0]["body"] == body:
        return "conflict notice already current"
    endpoint = (
        f"repos/{client.repository}/issues/comments/{owned[0]['id']}" if owned
        else f"repos/{client.repository}/issues/{snapshot.number}/comments"
    )
    response = obj(_fenced_write(
        client, snapshot, policy, endpoint, "-X", "PATCH" if owned else "POST", "-f", f"body={body}",
    ), "conflict notice response")
    user = obj(response.get("user"), "conflict notice actor")
    if (
        type(response.get("id")) is not int or response["id"] <= 0
        or response.get("body") != body or user.get("login") != bot or user.get("type") != "Bot"
    ):
        raise MutationUncertain("provider did not confirm the conflict notice and its actor")
    return "conflict notice reconciled"


def reconcile(client: GitHubEvidence, number: int, config: Settings, *, apply: bool) -> dict:
    client.write_attempted = False
    if config.mode == "disabled":
        return {"pr": number, "action": "disabled", "applied": False}
    observed = client.snapshot(number, config.policy)
    decision = decide(observed, config.policy)
    result = {
        "pr": number, "head": observed.head_sha, "base": observed.base_sha,
        "action": decision.action, "reason": decision.reason, "applied": False,
    }
    if not apply or decision.action in ("skip", "wait"):
        return result
    require_writer_context(config)
    fresh = client.snapshot(number, config.policy)
    next_decision = decide(fresh, config.policy)
    if not _same_candidate(observed, fresh) or next_decision.action != decision.action:
        return {**result, "action": "wait", "reason": "candidate changed; reconcile fresh evidence"}
    try:
        if decision.action == "conflict":
            result["reason"] = _conflict_notice(
                client, fresh, config.policy, f"{config.app_slug}[bot]",
            )
        elif decision.action == "rebase":
            response = obj(_fenced_write(
                client, fresh, config.policy, "graphql",
                "-f", f"query={_REBASE}", "-f", f"id={fresh.node_id}",
                "-f", f"head={fresh.head_sha}",
            ), "rebase response")
            data = obj(response.get("data"), "rebase data")
            update = obj(data.get("updatePullRequestBranch"), "native branch update")
            updated = obj(update.get("pullRequest"), "updated PR")
            if updated.get("id") != fresh.node_id or updated.get("baseRefName") != fresh.base_ref:
                raise MutationUncertain("branch update returned a different PR/base identity")
            new_head = sha(updated.get("headRefOid"), "updated head")
            if new_head == fresh.head_sha:
                return {**result, "action": "rebase-requested", "applied": None,
                        "reason": "native REBASE accepted; new head is not confirmed"}
            result["action"], result["head"] = "rebased", new_head
            result["reason"] = "new head confirmed; fresh review and required CI must follow"
        else:
            from agent_worktrees.providers.github_required_checks import merge_pull

            def guarded_invoke(argv: list[str]) -> subprocess.CompletedProcess[str]:
                if argv[:3] == ["gh", "pr", "merge"]:
                    _require_fresh_scope(client, fresh, config.policy)
                return client.merge_invoke(argv)

            error = merge_pull(
                client.repository, number, squash=True, admin=True,
                delete_source_branch=False, expected_head_sha=fresh.head_sha,
                host=client.host, invoke=guarded_invoke,
                hold_labels=config.policy.hold_labels,
                wip_title_prefixes=config.policy.wip_title_prefixes,
            )
            if error:
                if client.write_attempted:
                    raise MutationUncertain(error)
                raise EvidenceUnavailable(error)
            merged = client.metadata(number)
            if (
                type(merged.get("merged")) is not bool or merged.get("node_id") != fresh.node_id
                or sha(obj(merged.get("head"), "merged head").get("sha"), "merged head")
                != fresh.head_sha
                or obj(merged.get("base"), "merged base").get("ref") != fresh.base_ref
            ):
                raise MutationUncertain("provider did not confirm the expected merged candidate")
            if not merged["merged"]:
                return {**result, "action": "merge-requested", "applied": None,
                        "reason": "provider has not confirmed merge completion"}
            if merged.get("state") != "closed":
                raise MutationUncertain("merge completion and PR lifecycle state disagree")
            actor = obj(merged.get("merged_by"), "merge actor")
            if actor.get("login") != f"{config.app_slug}[bot]" or actor.get("type") != "Bot":
                raise MutationUncertain("merged PR has an unexpected actor; operator audit required")
            result["action"] = "merged"
    except (EvidenceUnavailable, OSError, subprocess.TimeoutExpired) as exc:
        if client.write_attempted:
            raise MutationUncertain(str(exc)) from exc
        raise
    result["applied"] = client.write_attempted
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, help="Bounded targeted manual recovery")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    if args.pr is not None and args.pr <= 0:
        parser.error("--pr must be a positive PR number")
    results: list[dict] = []
    current: int | None = None
    try:
        config = settings()
        if config.mode == "disabled":
            print(json.dumps({"mode": "disabled", "applied": False,
                              "reason": "implementation staged; authority/activation not accepted"}))
            return 2 if args.apply else 0
        require_trusted_context(config)
        if args.apply:
            require_writer_context(config)
        deadline = time.monotonic() + 240

        def bounded_invoke(argv: list[str]) -> subprocess.CompletedProcess[str]:
            if time.monotonic() + 45 >= deadline:
                raise EvidenceUnavailable("API budget reached; use targeted manual recovery")
            return invoke(argv)

        client = GitHubEvidence(config.policy.repository, bounded_invoke)
        numbers = candidates(client, args.pr, config.policy.consent_label)
        failed = False
        for number in numbers:
            current = number
            if time.monotonic() >= deadline:
                raise EvidenceUnavailable("catch-up deadline reached; use targeted manual recovery")
            try:
                results.append(reconcile(client, number, config, apply=args.apply))
            except MutationUncertain:
                raise
            except (EvidenceUnavailable, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                failed = True
                results.append({
                    "pr": number, "action": "unavailable", "applied": False, "error": str(exc),
                })
        print(json.dumps({"mode": config.mode, "apply_requested": args.apply,
                          "candidates": len(numbers), "results": results, "ok": not failed}))
        return 1 if failed else 0
    except (EvidenceUnavailable, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({
            "action": "unconfirmed" if isinstance(exc, MutationUncertain) else "unavailable",
            "failed_action_applied": None if isinstance(exc, MutationUncertain) else False,
            "pr": current, "completed_results": results, "error": str(exc),
        }))
        return 1


if __name__ == "__main__":
    sys.exit(main())
