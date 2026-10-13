"""Required-check completeness for an explicit GitHub admin merge.

This is a merge-time guard, not another watcher poll or a substitute for
server-side CI enforcement. Observed green checks cannot prove that a required
context which has not registered is satisfied.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from urllib.parse import quote

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


def merge_pull(
    repo: str, number: int, *, squash: bool, admin: bool,
    delete_source_branch: bool, expected_head_sha: str, host: str,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> str:
    if admin:
        try:
            expected_head_sha = verified_merge_head(
                repo, number, host=host, expected_head_sha=expected_head_sha, invoke=invoke,
            )
        except RequiredChecksError as exc:
            return f"gh admin merge refused for {repo}#{number}: {exc}"
    args = ["gh", "pr", "merge", str(number), "--repo", repo]
    if squash:
        args.append("--squash")
    if admin:
        args.append("--admin")
    if delete_source_branch:
        args.append("--delete-branch")
    if expected_head_sha:
        args += ["--match-head-commit", expected_head_sha]
    result = invoke(args)
    if result.returncode != 0:
        return f"gh pr merge failed for {repo}#{number}: {result.stderr.strip() or result.stdout.strip()}"
    return ""


def verified_merge_head(
    repo: str, number: int, *,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    host: str, expected_head_sha: str = "",
) -> str:
    """Return a pinned head only when all configured required checks pass."""
    parts = repo.split("/")
    if (
        len(parts) != 2 or any(part in (".", "..") for part in parts)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
    ):
        raise RequiredChecksError("required-check verification needs an owner/repository slug")
    if type(number) is not int or number <= 0:
        raise RequiredChecksError("required-check verification needs a positive PR number")

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
        if (
            pr.get("state") != "open"
            or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)
            or not isinstance(base, str) or not base
        ):
            raise RequiredChecksError("open PR head/base identity is unavailable or invalid")
        return head, base

    head, base = identity()
    if expected_head_sha and head != expected_head_sha:
        raise RequiredChecksError("PR head differs from the expected commit; revalidate before merging")
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
    for rules in pages:
        if not isinstance(rules, list):
            raise RequiredChecksError("effective branch rules page is unavailable or invalid")
        for rule in rules:
            rule = obj(rule, "branch rule")
            if rule.get("type") != "required_status_checks":
                continue
            checks = obj(rule.get("parameters"), "required-check rule").get("required_status_checks")
            add_checks(checks, "integration_id")

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
