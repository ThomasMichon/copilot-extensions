"""Required-check completeness for an explicit GitHub admin merge.

This is a merge-time guard, not another watcher poll or a substitute for
server-side CI enforcement. Observed green checks cannot prove that a required
context which has not registered is satisfied.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from ..pr_review_policy import (
    KNOWN_REVIEW_STATES,
    ReviewEvidence,
    normalized_review_time,
    outstanding_change_requests,
)

_POLICY_QUERY = """
query($owner:String!,$name:String!,$ref:String!) {
  repository(owner:$owner,name:$name) {
    ref(qualifiedName:$ref) {
      branchProtectionRule { requiresStatusChecks requiredStatusCheckContexts }
    }
  }
}
"""

class RequiredChecksError(RuntimeError):
    pass


class ReviewExceptionIneligible(RequiredChecksError):
    """The PR is safe for ordinary provider enforcement, but not admin fallback."""


class ReviewSafetyError(RequiredChecksError):
    """Mutation evidence is unsafe or incomplete; no merge write is permitted."""


@dataclass(frozen=True)
class ReviewExceptionEvidence:
    head_sha: str
    base_ref: str
    rules_fingerprint: str
    classic_fingerprint: str


def _fingerprint(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _optional_api_json(
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    *, host: str, path: str, label: str,
) -> object | None:
    result = invoke(["gh", "api", "--include", path, "--hostname", host])
    text = result.stdout or ""
    status = re.search(r"(?m)^HTTP/\S+\s+(\d{3})\b", text)
    if result.returncode != 0 and status and status.group(1) == "404":
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or text.strip()
        raise RequiredChecksError(
            f"{label} verification failed (exit {result.returncode}): {detail}"
        )
    body = re.split(r"\r?\n\r?\n", text)[-1]
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RequiredChecksError(f"{label} verification returned invalid JSON") from exc


def _target_parts(repo: str, number: int) -> list[str]:
    if not isinstance(repo, str):
        raise RequiredChecksError("required-check verification needs an owner/repository slug")
    parts = repo.split("/")
    if (
        len(parts) != 2 or any(part in (".", "..") for part in parts)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
    ):
        raise RequiredChecksError("required-check verification needs an owner/repository slug")
    if type(number) is not int or number <= 0:
        raise RequiredChecksError("required-check verification needs a positive PR number")
    return parts


def _protected_merge_head(
    repo: str, number: int, *, expected_head_sha: str, host: str,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> str:
    _target_parts(repo, number)
    if expected_head_sha:
        if not isinstance(expected_head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_head_sha):
            raise RequiredChecksError("normal merge requires a full expected head commit")
        return expected_head_sha
    result = invoke(["gh", "api", f"/repos/{repo}/pulls/{number}", "--hostname", host])
    if result.returncode:
        raise ReviewSafetyError(
            "normal merge head lookup failed: " + (result.stderr.strip() or result.stdout.strip())
        )
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RequiredChecksError("normal merge head lookup returned invalid JSON") from exc
    head = metadata.get("head") if isinstance(metadata, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    if (
        not isinstance(metadata, dict) or metadata.get("state") != "open"
        or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
    ):
        raise RequiredChecksError("open PR head identity is unavailable or invalid")
    return sha


def merge_pull(
    repo: str, number: int, *, squash: bool, admin: bool,
    delete_source_branch: bool, expected_head_sha: str, host: str,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    hold_labels: tuple[str, ...] = (), wip_title_prefixes: tuple[str, ...] = (),
) -> str:
    try:
        expected_head_sha = _protected_merge_head(
            repo, number, host=host, expected_head_sha=expected_head_sha, invoke=invoke,
        )
    except RequiredChecksError as exc:
        return f"gh normal merge refused for {repo}#{number}: {exc}"
    args = ["gh", "pr", "merge", str(number), "--repo", repo]
    if squash:
        args.append("--squash")
    if delete_source_branch:
        args.append("--delete-branch")
    if expected_head_sha:
        args += ["--match-head-commit", expected_head_sha]
    result = invoke(args)
    if result.returncode == 0:
        return ""
    error = f"gh pr merge failed for {repo}#{number}: {result.stderr.strip() or result.stdout.strip()}"
    if not admin:
        return error
    logging.getLogger(__name__).info(
        "Normal GitHub merge refused for %s#%s; verifying configured admin fallback", repo, number,
    )
    try:
        review_evidence = review_exception_evidence(
            repo, number, host=host, expected_head_sha=expected_head_sha,
            hold_labels=hold_labels, wip_title_prefixes=wip_title_prefixes,
            requested_merge_method="squash" if squash else "merge",
            invoke=invoke,
        )
        verified_merge_head(
            repo, number, host=host, expected_head_sha=expected_head_sha,
            expected_base_ref=review_evidence.base_ref,
            expected_rules_fingerprint=review_evidence.rules_fingerprint,
            expected_classic_fingerprint=review_evidence.classic_fingerprint,
            hold_labels=hold_labels, wip_title_prefixes=wip_title_prefixes,
            invoke=invoke,
        )
    except RequiredChecksError as exc:
        return f"{error}; gh admin merge refused for {repo}#{number}: {exc}"
    result = invoke([*args, "--admin"])
    if result.returncode:
        return f"{error}; gh admin merge failed: {result.stderr.strip() or result.stdout.strip()}"
    return ""


def review_exception_evidence(
    repo: str, number: int, *,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    host: str, expected_head_sha: str = "",
    hold_labels: tuple[str, ...] = (), wip_title_prefixes: tuple[str, ...] = (),
    requested_merge_method: str = "squash",
) -> ReviewExceptionEvidence:
    """Prove the missing-CODEOWNER exception across actor-authorized review layers.

    The final merge still uses GitHub's expected-head CAS. GitHub exposes no
    atomic review-version CAS, so this re-read minimizes but cannot eliminate a
    provider lifecycle race after the evidence read.
    """
    _target_parts(repo, number)

    def read_json(args: list[str]) -> object:
        argv = ["gh", *args]
        if args[0] == "api":
            argv += ["--hostname", host]
        result = invoke(argv)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RequiredChecksError(
                f"review-exception verification failed (exit {result.returncode}): {detail}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ReviewSafetyError("review-exception verification returned invalid JSON") from exc

    def obj(value: object, label: str) -> dict:
        if not isinstance(value, dict):
            raise ReviewSafetyError(f"{label} is unavailable or invalid")
        return value

    def identity() -> tuple[str, str]:
        pr = obj(read_json(["api", f"/repos/{repo}/pulls/{number}"]), "PR metadata")
        head = obj(pr.get("head"), "PR head").get("sha")
        base = obj(pr.get("base"), "PR base").get("ref")
        title = pr.get("title")
        labels = pr.get("labels")
        if (
            pr.get("state") != "open" or pr.get("draft") is not False
            or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)
            or not isinstance(base, str) or not base or not isinstance(title, str)
            or not isinstance(labels, list)
        ):
            raise ReviewSafetyError("open, non-draft PR identity is unavailable or invalid")
        if expected_head_sha and head != expected_head_sha:
            raise ReviewSafetyError("PR head differs from the expected commit")
        names = {
            label.get("name", "").casefold() for label in labels
            if isinstance(label, dict) and isinstance(label.get("name"), str)
        }
        if len(names) != len(labels):
            raise ReviewSafetyError("PR labels are unavailable or invalid")
        held = names & {label.casefold() for label in hold_labels}
        if held:
            raise ReviewSafetyError("hold label present: " + ", ".join(sorted(held)))
        lowered = title.strip().casefold()
        if any(lowered.startswith(prefix.strip().casefold())
               for prefix in wip_title_prefixes if prefix.strip()):
            raise ReviewSafetyError("WIP title prefix is present")
        return head, base

    head, base = identity()
    pages = read_json([
        "api", f"/repos/{repo}/pulls/{number}/reviews", "--paginate", "--slurp",
    ])
    if not isinstance(pages, list) or not pages:
        raise ReviewSafetyError("complete review history is unavailable")
    reviews: list[ReviewEvidence] = []
    for page in pages:
        if not isinstance(page, list):
            raise ReviewSafetyError("review history page is unavailable or invalid")
        for raw in page:
            review = obj(raw, "review")
            rid = review.get("id")
            user = review.get("user")
            state = review.get("state")
            if (
                type(rid) is not int or not isinstance(user, dict)
                or not isinstance(user.get("login"), str) or not user["login"].strip()
                or not isinstance(state, str) or state.upper() not in KNOWN_REVIEW_STATES
                or (
                    state.upper() != "PENDING"
                    and normalized_review_time(review.get("submitted_at")) is None
                )
            ):
                raise ReviewSafetyError("review history is incomplete or invalid")
            reviews.append(ReviewEvidence(
                id=rid, state=state, reviewer=user["login"],
                submitted_at=str(review.get("submitted_at") or ""),
                commit_id=str(review.get("commit_id") or ""),
                dismissed=state.upper() == "DISMISSED",
            ))
    author = obj(read_json(["api", f"/repos/{repo}/pulls/{number}"]), "PR metadata")
    author_login = obj(author.get("user"), "PR author").get("login")
    if not isinstance(author_login, str):
        raise ReviewSafetyError("PR author identity is unavailable")
    asking = outstanding_change_requests(reviews, author_login)
    if asking:
        raise ReviewSafetyError("active change request by " + ", ".join(asking))

    decision = obj(read_json([
        "pr", "view", str(number), "--repo", repo, "--json", "reviewDecision",
    ]), "review decision").get("reviewDecision")
    if decision != "REVIEW_REQUIRED":
        raise ReviewExceptionIneligible(
            "GitHub does not report missing required approval as the current review block"
        )

    pages = read_json([
        "api", f"/repos/{repo}/rules/branches/{quote(base, safe='')}",
        "--paginate", "--slurp",
    ])
    if not isinstance(pages, list) or not pages:
        raise ReviewSafetyError("effective branch rules are unavailable")
    rules_fingerprint = _fingerprint(pages)
    codeowner_rulesets: set[int] = set()
    review_rulesets: set[int] = set()
    for page in pages:
        if not isinstance(page, list):
            raise ReviewSafetyError("effective branch rules page is invalid")
        for raw in page:
            rule = obj(raw, "branch rule")
            kind = rule.get("type")
            if kind in ("deletion", "non_fast_forward"):
                # A PR merge neither deletes nor force-updates its base ref.
                # Keep these rules in the fingerprint rechecked before writing.
                continue
            if kind == "required_status_checks":
                params = obj(rule.get("parameters"), "required-status-check rule")
                strict = params.get("strict_required_status_checks_policy")
                if not isinstance(strict, bool):
                    raise ReviewSafetyError("required-status-check freshness policy is incomplete")
                if strict:
                    raise ReviewExceptionIneligible(
                        "required checks demand an up-to-date branch"
                    )
                continue
            if kind != "pull_request":
                raise ReviewExceptionIneligible(
                    f"additional unverified merge requirement is active: {kind or 'unknown'}"
                )
            params = obj(rule.get("parameters"), "pull-request rule")
            allowed_methods = params.get("allowed_merge_methods")
            if (
                not isinstance(allowed_methods, list)
                or any(method not in ("merge", "squash", "rebase")
                       for method in allowed_methods)
            ):
                raise ReviewSafetyError("allowed merge methods are incomplete or invalid")
            if requested_merge_method not in allowed_methods:
                raise ReviewExceptionIneligible(
                    f"pull-request rule does not allow {requested_merge_method} merges"
                )
            codeowner = params.get("require_code_owner_review")
            count = params.get("required_approving_review_count")
            threads = params.get("required_review_thread_resolution")
            last_push = params.get("require_last_push_approval")
            if (
                not isinstance(codeowner, bool) or type(count) is not int
                or not isinstance(threads, bool) or not isinstance(last_push, bool)
            ):
                raise ReviewSafetyError("pull-request rule requirements are incomplete")
            required_reviewers = params.get("required_reviewers", [])
            if not isinstance(required_reviewers, list):
                raise ReviewSafetyError("required reviewer declarations are incomplete")
            if required_reviewers:
                raise ReviewExceptionIneligible(
                    "additional required reviewers are not a CODEOWNER-only exception"
                )
            if not codeowner:
                if count not in (0, 1) or threads or last_push:
                    raise ReviewExceptionIneligible(
                        "another pull-request review requirement is active"
                    )
                if not count:
                    continue
                # A separate one-vote layer needs its own actor-scoped authority;
                # CODEOWNER bypass alone must not waive an independent approval.
            elif count not in (0, 1) or threads or last_push:
                raise ReviewExceptionIneligible(
                    "CODEOWNER approval is not the only remaining review requirement"
                )
            ruleset_id = rule.get("ruleset_id")
            if type(ruleset_id) is not int or ruleset_id <= 0:
                requirement = "CODEOWNER" if codeowner else "approval"
                raise ReviewSafetyError(f"{requirement} review rule has no ruleset identity")
            review_rulesets.add(ruleset_id)
            if codeowner:
                codeowner_rulesets.add(ruleset_id)
    if not codeowner_rulesets:
        raise ReviewExceptionIneligible(
            "a missing CODEOWNER approval rule was not positively identified"
        )

    protection = _optional_api_json(
        invoke, host=host,
        path=f"/repos/{repo}/branches/{quote(base, safe='')}/protection",
        label="classic review-policy",
    )
    classic_fingerprint = _fingerprint(protection)
    if protection is not None:
        protection = obj(protection, "classic branch protection")
        status_policy = protection.get("required_status_checks")
        if status_policy is not None:
            status_policy = obj(status_policy, "classic required-status-check policy")
            if not isinstance(status_policy.get("strict"), bool):
                raise ReviewSafetyError("classic branch freshness policy is incomplete")
            if status_policy["strict"]:
                raise ReviewExceptionIneligible(
                    "classic required checks demand an up-to-date branch"
                )
        for field in (
            "required_conversation_resolution",
            "required_signatures",
            "required_linear_history",
            "lock_branch",
        ):
            constraint = protection.get(field)
            if constraint is not None:
                constraint = obj(constraint, f"classic {field}")
                if not isinstance(constraint.get("enabled"), bool):
                    raise ReviewSafetyError(
                        f"classic {field} requirement is incomplete"
                    )
                if constraint["enabled"]:
                    raise ReviewExceptionIneligible(
                        f"classic branch protection adds unverified requirement: {field}"
                    )
        reviews_policy = protection.get("required_pull_request_reviews")
        if reviews_policy is not None:
            reviews_policy = obj(reviews_policy, "classic review requirements")
            count = reviews_policy.get("required_approving_review_count")
            flags = (
                "require_code_owner_reviews",
                "require_last_push_approval",
            )
            if type(count) is not int or count < 0 or any(
                not isinstance(reviews_policy.get(flag), bool) for flag in flags
            ):
                raise ReviewSafetyError("classic review requirements are incomplete")
            raise ReviewExceptionIneligible(
                "classic branch protection adds a review requirement without actor-scoped evidence"
            )
    for ruleset_id in sorted(review_rulesets):
        ruleset = obj(read_json([
            "api", f"/repos/{repo}/rulesets/{ruleset_id}",
        ]), "ruleset")
        if ruleset.get("current_user_can_bypass") not in ("always", "pull_requests_only"):
            requirement = "CODEOWNER" if ruleset_id in codeowner_rulesets else "approval"
            raise ReviewExceptionIneligible(
                f"acting identity is not authorized to bypass {requirement} ruleset {ruleset_id}"
            )
    if identity() != (head, base):
        raise ReviewSafetyError("PR head/base or mutation gates changed during review verification")
    return ReviewExceptionEvidence(
        head_sha=head,
        base_ref=base,
        rules_fingerprint=rules_fingerprint,
        classic_fingerprint=classic_fingerprint,
    )


def verified_merge_head(
    repo: str, number: int, *,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    host: str, expected_head_sha: str = "", expected_base_ref: str = "",
    expected_rules_fingerprint: str = "", expected_classic_fingerprint: str = "",
    hold_labels: tuple[str, ...] = (), wip_title_prefixes: tuple[str, ...] = (),
) -> str:
    """Return a pinned head only when all configured required checks pass."""
    parts = _target_parts(repo, number)

    def read_json(args: list[str]) -> object:
        argv = ["gh", *args]
        if args[0] == "api":
            argv += ["--hostname", host]
        result = invoke(argv)
        if result.returncode != 0:
            details = "\n".join(
                stream.rstrip() for stream in (result.stdout, result.stderr) if stream.strip()
            )
            raise RequiredChecksError(
                f"required-check verification failed (exit {result.returncode}): {details}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RequiredChecksError("required-check verification returned invalid JSON") from exc

    def obj(value: object, label: str) -> dict:
        if not isinstance(value, dict):
            raise RequiredChecksError(f"{label} is unavailable or invalid")
        return value

    def identity() -> tuple[str, str]:
        pr = obj(read_json(["api", f"/repos/{repo}/pulls/{number}"]), "PR metadata")
        head = obj(pr.get("head"), "PR head").get("sha")
        base = obj(pr.get("base"), "PR base").get("ref")
        title = pr.get("title")
        labels = pr.get("labels")
        if (
            pr.get("state") != "open" or pr.get("draft") is not False
            or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)
            or not isinstance(base, str) or not base
            or not isinstance(title, str) or not isinstance(labels, list)
        ):
            raise RequiredChecksError("open, non-draft PR identity is unavailable or invalid")
        names = {
            label.get("name", "").casefold() for label in labels
            if isinstance(label, dict) and isinstance(label.get("name"), str)
        }
        if len(names) != len(labels):
            raise RequiredChecksError("PR labels are unavailable or invalid")
        held = names & {label.casefold() for label in hold_labels}
        if held:
            raise RequiredChecksError("hold label present: " + ", ".join(sorted(held)))
        lowered = title.strip().casefold()
        if any(lowered.startswith(prefix.strip().casefold())
               for prefix in wip_title_prefixes if prefix.strip()):
            raise RequiredChecksError("WIP title prefix is present")
        return head, base

    head, base = identity()
    if expected_head_sha and head != expected_head_sha:
        raise RequiredChecksError("PR head differs from the expected commit; revalidate before merging")
    if expected_base_ref and base != expected_base_ref:
        raise RequiredChecksError("PR base differs from the review-policy evidence; revalidate before merging")
    policy = obj(read_json([
        "api", "graphql", "-f", f"query={_POLICY_QUERY}",
        "-f", f"owner={parts[0]}", "-f", f"name={parts[1]}",
        "-f", f"ref=refs/heads/{base}",
    ]), "branch protection response")
    if policy.get("errors"):
        raise RequiredChecksError("branch protection could not be read completely")
    ref = obj(obj(obj(policy.get("data"), "policy data").get("repository"), "repository")
              .get("ref"), "base branch")
    expected: set[tuple[str, int | None]] = set()

    def context_names(contexts: object) -> set[str]:
        if not isinstance(contexts, list) or any(
            not isinstance(name, str) or not name.strip() for name in contexts
        ):
            raise RequiredChecksError("required context declarations are unavailable or invalid")
        return set(contexts)

    def add_checks(checks: object, app_key: str) -> set[str]:
        if not isinstance(checks, list):
            raise RequiredChecksError("required-check declarations are unavailable or invalid")
        names: set[str] = set()
        for check in checks:
            check = obj(check, "required-check declaration")
            name = check.get("context")
            context_names([name])
            app = check.get(app_key)
            if app is not None and (type(app) is not int or app != -1 and app <= 0):
                raise RequiredChecksError("required-check integration identity is invalid")
            expected.add((name, None if app == -1 else app))
            names.add(name)
        return names

    if "branchProtectionRule" not in ref:
        raise RequiredChecksError("branch protection declaration is unavailable")
    protection = ref["branchProtectionRule"]
    if protection is not None:
        protection = obj(protection, "branch protection")
        required = protection.get("requiresStatusChecks")
        if not isinstance(required, bool):
            raise RequiredChecksError("branch protection status-check policy is invalid")
        if required:
            declared = context_names(protection.get("requiredStatusCheckContexts"))
            classic = obj(read_json([
                "api", f"/repos/{repo}/branches/{quote(base, safe='')}/protection/required_status_checks",
            ]), "classic required checks")
            checks = classic.get("checks")
            if not isinstance(checks, list) or any(
                not isinstance(check, dict) or "app_id" not in check for check in checks
            ):
                raise RequiredChecksError("classic required-check integrations are unavailable")
            if (
                context_names(classic.get("contexts")) != declared
                or add_checks(checks, "app_id") != declared
            ):
                raise RequiredChecksError("classic required-check declarations changed or are incomplete")

    pages = read_json([
        "api", f"/repos/{repo}/rules/branches/{quote(base, safe='')}",
        "--paginate", "--slurp",
    ])
    if not isinstance(pages, list) or not pages:
        raise RequiredChecksError("effective branch rules are unavailable or invalid")
    if expected_rules_fingerprint and _fingerprint(pages) != expected_rules_fingerprint:
        raise RequiredChecksError("effective branch rules changed after review verification")
    for rules in pages:
        if not isinstance(rules, list):
            raise RequiredChecksError("effective branch rules page is unavailable or invalid")
        for rule in rules:
            rule = obj(rule, "branch rule")
            kind = rule.get("type")
            if not isinstance(kind, str) or not kind.strip():
                raise RequiredChecksError("branch rule type is unavailable or invalid")
            if kind != "required_status_checks":
                continue
            checks = obj(rule.get("parameters"), "required-check rule").get("required_status_checks")
            add_checks(checks, "integration_id")

    if expected_classic_fingerprint:
        protection = _optional_api_json(
            invoke, host=host,
            path=f"/repos/{repo}/branches/{quote(base, safe='')}/protection",
            label="classic branch-policy",
        )
        if _fingerprint(protection) != expected_classic_fingerprint:
            raise RequiredChecksError(
                "classic branch protection changed after review verification"
            )

    if expected:
        checks = read_json([
            "pr", "checks", str(number), "--repo", repo, "--required",
            "--json", "name,bucket",
        ])
        if not isinstance(checks, list):
            raise RequiredChecksError("required-check results are unavailable or invalid")
        passed: set[str] = set()
        unsuccessful: set[str] = set()
        for check in checks:
            check = obj(check, "required-check result")
            name = check.get("name")
            if not isinstance(name, str) or not name.strip():
                raise RequiredChecksError("required-check result has an invalid context name")
            if check.get("bucket") == "pass":
                passed.add(name)
            else:
                unsuccessful.add(name)
        missing = {name for name, _ in expected} - passed
        if missing or unsuccessful:
            raise RequiredChecksError(
                "required checks are missing, pending or unsuccessful: "
                + ", ".join(sorted(missing | unsuccessful))
            )
        bound = {(name, app) for name, app in expected if app is not None}
        if bound:
            pages = read_json([
                "api", f"/repos/{repo}/commits/{head}/check-runs",
                "--paginate", "--slurp",
            ])
            if not isinstance(pages, list) or not pages:
                raise RequiredChecksError("check-run pages are unavailable or invalid")
            observed: set[tuple[str, int]] = set()
            count = 0
            for page in pages:
                page = obj(page, "check-run page")
                runs = page.get("check_runs")
                total = page.get("total_count")
                if (
                    not isinstance(runs, list) or type(total) is not int or total < 0
                    or total != pages[0].get("total_count")
                ):
                    raise RequiredChecksError("check-run page is incomplete")
                count += len(runs)
                for run in runs:
                    run = obj(run, "check run")
                    app = obj(run.get("app"), "check-run integration").get("id")
                    if type(app) is not int or app <= 0 or run.get("head_sha") != head:
                        raise RequiredChecksError("check-run source identity is unavailable or invalid")
                    name = run.get("name")
                    context_names([name])
                    identity_key = (name, app)
                    if identity_key not in bound:
                        continue
                    if run.get("status") != "completed" or run.get("conclusion") != "success":
                        raise RequiredChecksError(f"required check from integration {app} has not passed")
                    observed.add(identity_key)
            if count != pages[0]["total_count"] or bound - observed:
                raise RequiredChecksError(
                    "required integration-bound checks are missing or incomplete: "
                    + ", ".join(f"{name} (app {app})" for name, app in sorted(bound - observed))
                )
    if identity() != (head, base):
        raise RequiredChecksError("PR head/base changed during check verification; revalidate before merging")
    return head
