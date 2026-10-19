"""Read-only GitHub evidence for the trusted-source PR completion steward."""

from __future__ import annotations

import base64
import binascii
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from maintainer_pr_completion_policy import (
    CompletionPolicy, CompletionSnapshot, ReviewSummary, decide,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}")
LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?")
BOT = "copilot-pull-request-reviewer[bot]"
MAX_PAGES = 10
GhInvoke = Callable[[list[str]], subprocess.CompletedProcess[str]]
_EPOCH_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      timelineItems(first:100,after:$cursor,
        itemTypes:[REOPENED_EVENT,BASE_REF_CHANGED_EVENT]) {
        nodes {
          __typename
          ... on ReopenedEvent { createdAt }
          ... on BaseRefChangedEvent { createdAt }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
_STRICT_QUERY = """
query($owner:String!,$name:String!,$ref:String!) {
  repository(owner:$owner,name:$name) {
    ref(qualifiedName:$ref) {
      branchProtectionRule { requiresStatusChecks requiresStrictStatusChecks }
    }
  }
}
"""


class EvidenceUnavailable(RuntimeError):
    """Missing or malformed evidence never becomes an affirmative gate."""


def obj(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise EvidenceUnavailable(f"{label} is not an object")
    return value


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceUnavailable(f"{label} is missing")
    return value


def sha(value: object, label: str) -> str:
    value = text(value, label)
    if not SHA.fullmatch(value):
        raise EvidenceUnavailable(f"{label} is not a full commit identity")
    return value


def timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(text(value, "timestamp").replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceUnavailable("invalid provider timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceUnavailable("provider timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _engine() -> None:
    source = ROOT / "plugins" / "agent-worktrees" / "src"
    expected = (source / "agent_worktrees").resolve()
    for name, module in list(sys.modules.items()):
        if name == "agent_worktrees" or name.startswith("agent_worktrees."):
            filename = getattr(module, "__file__", None)
            if not filename or not Path(filename).resolve().is_relative_to(expected):
                raise EvidenceUnavailable("an imported PR helper is not from the trusted checkout")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


class GitHubEvidence:
    def __init__(self, repository: str, invoke: GhInvoke, *, host: str = "github.com") -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise EvidenceUnavailable("invalid repository slug")
        self.repository, self.invoke, self.host = repository, invoke, host
        self.owner, self.name = repository.split("/")
        self.write_attempted = False

    def read(self, endpoint: str, *fields: str) -> object:
        args = ["gh", "api", endpoint, "--hostname", self.host, *fields]
        result = self.invoke(args)
        if result.returncode:
            raise EvidenceUnavailable(
                f"GitHub evidence request failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise EvidenceUnavailable("GitHub returned invalid evidence JSON") from exc
        if isinstance(payload, dict) and payload.get("errors"):
            raise EvidenceUnavailable(f"GitHub GraphQL evidence errors: {payload['errors']}")
        return payload

    def write(self, endpoint: str, *fields: str) -> object:
        self.write_attempted = True
        return self.read(endpoint, *fields)

    def merge_invoke(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["gh", "pr", "merge"]:
            self.write_attempted = True
        return self.invoke(argv)

    def pages(self, endpoint: str) -> list[dict]:
        result: list[dict] = []
        separator = "&" if "?" in endpoint else "?"
        for page in range(1, MAX_PAGES + 1):
            payload = self.read(f"{endpoint}{separator}per_page=100&page={page}")
            if not isinstance(payload, list) or len(payload) > 100:
                raise EvidenceUnavailable("invalid evidence page")
            result.extend(obj(item, "evidence entry") for item in payload)
            if len(payload) < 100:
                return result
        raise EvidenceUnavailable("evidence pagination limit exceeded")

    def metadata(self, number: int) -> dict:
        if type(number) is not int or number <= 0:
            raise EvidenceUnavailable("invalid PR number")
        pr = obj(self.read(f"repos/{self.repository}/pulls/{number}"), "PR")
        if type(pr.get("number")) is not int or pr["number"] != number:
            raise EvidenceUnavailable("PR response identity does not match the requested candidate")
        return pr

    def epoch(self, number: int, created_at: object) -> datetime:
        latest = timestamp(created_at)
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            fields = [
                "-f", f"query={_EPOCH_QUERY}", "-f", f"owner={self.owner}",
                "-f", f"name={self.name}", "-F", f"number={number}",
            ]
            if cursor is not None:
                fields += ["-f", f"cursor={cursor}"]
            payload = obj(self.read("graphql", *fields), "epoch response")
            data = obj(payload.get("data"), "epoch data")
            repo = obj(data.get("repository"), "epoch repository")
            pr = obj(repo.get("pullRequest"), "epoch PR")
            connection = obj(pr.get("timelineItems"), "epoch connection")
            nodes = connection.get("nodes")
            info = obj(connection.get("pageInfo"), "epoch pagination")
            if (
                not isinstance(nodes, list) or len(nodes) > 100
                or type(info.get("hasNextPage")) is not bool
            ):
                raise EvidenceUnavailable("incomplete epoch history")
            for node in nodes:
                event = obj(node, "epoch event")
                if event.get("__typename") not in ("ReopenedEvent", "BaseRefChangedEvent"):
                    raise EvidenceUnavailable("unexpected epoch event")
                latest = max(latest, timestamp(event.get("createdAt")))
            if not info["hasNextPage"]:
                return latest
            cursor = text(info.get("endCursor"), "epoch cursor")
            if cursor in seen:
                raise EvidenceUnavailable("repeated epoch cursor")
            seen.add(cursor)
        raise EvidenceUnavailable("epoch pagination limit exceeded")

    def reviews(self, number: int, author: str, epoch: datetime) -> ReviewSummary:
        evidence = []
        bot_reviews = []
        ids: set[int] = set()
        for review in self.pages(f"repos/{self.repository}/pulls/{number}/reviews"):
            user = obj(review.get("user"), "reviewer")
            login = text(user.get("login"), "reviewer login")
            rid, state = review.get("id"), review.get("state")
            if (
                not LOGIN.fullmatch(login) or type(rid) is not int or rid <= 0 or rid in ids
                or state not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING")
            ):
                raise EvidenceUnavailable("invalid or duplicate submitted review")
            ids.add(rid)
            if state == "PENDING":
                continue
            submitted = timestamp(review.get("submitted_at"))
            item = {
                "id": rid, "state": state, "reviewer": login,
                "submitted_at": submitted.isoformat(),
                "commit_id": sha(review.get("commit_id"), "review head"),
                "dismissed": state == "DISMISSED",
            }
            evidence.append(item)
            if login.casefold() == BOT:
                if user.get("type") != "Bot":
                    raise EvidenceUnavailable("Copilot reviewer is not a provider-confirmed bot")
                bot_reviews.append((submitted, rid, item))
        latest = max(bot_reviews, key=lambda item: (item[0], item[1])) if bot_reviews else None
        _engine()
        try:
            from agent_worktrees.pr_review_policy import outstanding_change_requests
        except ImportError as exc:
            raise EvidenceUnavailable("trusted source lacks the reviewed lifecycle policy") from exc
        return ReviewSummary(
            complete=True, change_requests=outstanding_change_requests(evidence, author),
            copilot_state=latest[2]["state"] if latest else "",
            copilot_head=latest[2]["commit_id"] if latest else "",
            copilot_submitted_at=latest[0] if latest else None, epoch=epoch,
        )

    def invited_collaborator(self, author: str) -> bool:
        if not LOGIN.fullmatch(author):
            raise EvidenceUnavailable("invalid PR author identity")
        result = self.invoke([
            "gh", "api", f"repos/{self.repository}/collaborators/{author}",
            "--hostname", self.host, "--silent",
        ])
        if result.returncode:
            if re.search(r"\bHTTP 404\b", result.stderr):
                return False
            raise EvidenceUnavailable(f"collaborator lookup failed: {result.stderr.strip()}")
        return True

    def membership(self, author: str, base_sha: str) -> tuple[bool, bool]:
        if not self.invited_collaborator(author):
            return False, False
        payload = obj(self.read(
            f"repos/{self.repository}/contents/.github/CODEOWNERS?ref={base_sha}",
        ), "root CODEOWNERS")
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise EvidenceUnavailable("root CODEOWNERS is not a readable file")
        try:
            content = base64.b64decode(
                "".join(text(payload.get("content"), "CODEOWNERS content").split()),
                validate=True,
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise EvidenceUnavailable("invalid CODEOWNERS encoding") from exc
        roots = [
            parts[1:] for line in content.splitlines()
            if (parts := line.partition("#")[0].split()) and parts[0] == "*"
        ]
        if not roots or not roots[-1] or any(
            not token.startswith("@") or not LOGIN.fullmatch(token[1:]) for token in roots[-1]
        ):
            raise EvidenceUnavailable("root CODEOWNERS needs an explicit user roster")
        return True, author.casefold() in {token[1:].casefold() for token in roots[-1]}

    def update_required(self, base_ref: str, base_sha: str, head_sha: str) -> bool:
        response = obj(self.read(
            "graphql", "-f", f"query={_STRICT_QUERY}", "-f", f"owner={self.owner}",
            "-f", f"name={self.name}", "-f", f"ref=refs/heads/{base_ref}",
        ), "strict policy response")
        data = obj(response.get("data"), "strict policy data")
        repo = obj(data.get("repository"), "strict policy repository")
        branch = obj(repo.get("ref"), "strict policy branch")
        if "branchProtectionRule" not in branch:
            raise EvidenceUnavailable("classic branch policy is incomplete")
        strict = False
        protection = branch["branchProtectionRule"]
        if protection is not None:
            protection = obj(protection, "classic branch policy")
            requires = protection.get("requiresStatusChecks")
            strict_value = protection.get("requiresStrictStatusChecks")
            if type(requires) is not bool or type(strict_value) is not bool:
                raise EvidenceUnavailable("classic strict-check policy is incomplete")
            strict = requires and strict_value
        for rule in self.pages(f"repos/{self.repository}/rules/branches/{quote(base_ref, safe='')}"):
            if not isinstance(rule.get("type"), str):
                raise EvidenceUnavailable("branch rule type is unavailable")
            if rule["type"] != "required_status_checks":
                continue
            parameters = obj(rule.get("parameters"), "required-check rule")
            value = parameters.get("strict_required_status_checks_policy")
            if type(value) is not bool:
                raise EvidenceUnavailable("ruleset strict-check policy is incomplete")
            strict = strict or value
        if not strict:
            return False
        comparison = obj(self.read(
            f"repos/{self.repository}/compare/{base_sha}...{head_sha}",
        ), "base comparison")
        behind = comparison.get("behind_by")
        if type(behind) is not int or behind < 0:
            raise EvidenceUnavailable("base comparison is incomplete")
        return behind > 0

    def scope_snapshot(self, number: int) -> tuple[CompletionSnapshot, dict]:
        pr = self.metadata(number)
        base, head = obj(pr.get("base"), "PR base"), obj(pr.get("head"), "PR head")
        base_repo, head_repo = obj(base.get("repo"), "base repository"), obj(head.get("repo"), "head repository")
        base_sha, head_sha = sha(base.get("sha"), "base head"), sha(head.get("sha"), "PR head")
        text(obj(pr.get("user"), "PR author").get("login"), "PR author login")
        labels = pr.get("labels")
        if (
            type(pr.get("draft")) is not bool or not isinstance(pr.get("title"), str)
            or not isinstance(labels, list) or type(base_repo.get("id")) is not int
            or base_repo["id"] <= 0 or type(head_repo.get("id")) is not int
            or head_repo["id"] <= 0
        ):
            raise EvidenceUnavailable("PR scope, draft or label evidence is incomplete")
        names = tuple(text(obj(label, "label").get("name"), "label name") for label in labels)
        created_at = timestamp(pr.get("created_at"))
        base_ref = text(base.get("ref"), "base ref")
        return CompletionSnapshot(
            repository=text(base_repo.get("full_name"), "repository name"),
            number=number, node_id=text(pr.get("node_id"), "PR node identity"),
            state=text(pr.get("state"), "PR state"), head_sha=head_sha,
            head_ref=text(head.get("ref"), "head ref"), base_ref=base_ref, base_sha=base_sha,
            same_repository=head_repo["id"] == base_repo["id"], draft=pr["draft"],
            title=pr["title"], labels=names, invited_collaborator=None, root_codeowner=None,
            reviews=ReviewSummary(False, (), "", "", None, created_at),
            required_checks_verified=None, mergeable=None, update_required=None,
            rebase_supported=None,
        ), pr

    def require_live_base(self, snapshot: CompletionSnapshot) -> None:
        base_tip = obj(self.read(
            f"repos/{self.repository}/git/ref/heads/{quote(snapshot.base_ref, safe='')}",
        ), "live base ref")
        target = obj(base_tip.get("object"), "live base target")
        if (
            target.get("type") != "commit"
            or sha(target.get("sha"), "live base head") != snapshot.base_sha
        ):
            raise EvidenceUnavailable("PR base identity has not caught up with its live branch")

    def snapshot(self, number: int, policy: CompletionPolicy) -> CompletionSnapshot:
        initial, pr = self.scope_snapshot(number)
        if decide(initial, policy).action == "skip":
            return initial
        self.require_live_base(initial)
        base_ref, base_sha, head_sha = initial.base_ref, initial.base_sha, initial.head_sha
        base_repo = obj(obj(pr.get("base"), "PR base").get("repo"), "base repository")
        author = text(obj(pr.get("user"), "PR author").get("login"), "PR author login")
        invited, root_owner = self.membership(author, base_sha)
        initial = replace(initial, invited_collaborator=invited, root_codeowner=root_owner)
        if decide(initial, policy).action == "skip":
            return initial
        epoch = self.epoch(number, pr.get("created_at"))
        reviews = self.reviews(number, author, epoch)
        _engine()
        from agent_worktrees.providers.github_required_checks import (
            RequiredChecksError, verified_merge_head,
        )

        check_error = ""
        try:
            verified_merge_head(
                self.repository, number, invoke=self.invoke, host=self.host,
                expected_head_sha=head_sha, expected_base_ref=base_ref,
            )
            verified = True
        except RequiredChecksError as exc:
            verified, check_error = False, str(exc)
        repository = obj(self.read(f"repos/{self.repository}"), "repository")
        if (
            type(repository.get("id")) is not int or repository["id"] != base_repo["id"]
            or text(repository.get("full_name"), "repository name").casefold()
            != self.repository.casefold()
        ):
            raise EvidenceUnavailable("repository identity does not match the admitted candidate")
        allowed = repository.get("allow_rebase_merge")
        if type(allowed) is not bool:
            raise EvidenceUnavailable("native rebase capability is unavailable")
        rebase = (
            pr["rebaseable"] if type(pr.get("rebaseable")) is bool else None
        ) if allowed else False
        return replace(
            initial, invited_collaborator=invited,
            root_codeowner=root_owner, reviews=reviews, required_checks_verified=verified,
            mergeable=pr.get("mergeable") if type(pr.get("mergeable")) is bool else None,
            update_required=self.update_required(base_ref, base_sha, head_sha),
            rebase_supported=rebase, required_checks_error=check_error,
        )
