"""Read-only admission for the single active Dev-to-Main promotion."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_promotion_workflows import VerificationError, _api  # noqa: E402
from promotion_dispatch import PromotionDispatchError, pending_source, require_automatic_source  # noqa: E402

SCHEMA = "copilot-extensions.promotion-admission"
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+")
_BRANCH = re.compile(r"release/promote-([1-9][0-9]{0,19})")
_SHA = re.compile(r"[0-9a-f]{40}")


class PromotionAdmissionError(RuntimeError):
    """The complete promotion inventory or requested admission is unavailable."""


@dataclass(frozen=True)
class ActivePromotion:
    number: int
    branch: str
    head_sha: str
    run_id: int


def active_promotions(
    repository: str, *, api: Callable[..., object] | None = None,
    wait_seconds: float = 120,
) -> tuple[ActivePromotion, ...]:
    """Read every open Main PR; never infer idle from a partial response."""
    if (
        not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or type(wait_seconds) not in (int, float) or not 0 < wait_seconds <= 120
    ):
        raise PromotionAdmissionError("invalid promotion inventory request")
    request = _api if api is None else api
    owner = repository.split("/", 1)[0].casefold()
    deadline = time.monotonic() + wait_seconds
    seen: set[int] = set()
    promotions: list[ActivePromotion] = []
    for page in range(1, 11):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PromotionAdmissionError("promotion inventory deadline expired")
        rows = request(
            f"repos/{repository}/pulls?state=open&base=main&sort=created&direction=asc"
            f"&per_page=100&page={page}",
            timeout=min(30, remaining),
        )
        if not isinstance(rows, list) or len(rows) > 100:
            raise PromotionAdmissionError("open Main PR inventory is incomplete or invalid")
        for row in rows:
            if not isinstance(row, dict):
                raise PromotionAdmissionError("open Main PR inventory contains an invalid row")
            number, head, base, user = (
                row.get("number"), row.get("head"), row.get("base"), row.get("user"),
            )
            if (
                type(number) is not int or number <= 0 or number in seen
                or row.get("state") != "open"
                or not isinstance(base, dict) or base.get("ref") != "main"
                or not isinstance(head, dict) or not isinstance(head.get("ref"), str)
                or not isinstance(user, dict) or not isinstance(user.get("login"), str)
            ):
                raise PromotionAdmissionError("open Main PR inventory is ambiguous or invalid")
            seen.add(number)
            branch = head["ref"]
            if not branch.startswith("release/promote-"):
                continue
            source = head.get("repo")
            if not isinstance(source, dict) or not isinstance(source.get("full_name"), str):
                raise PromotionAdmissionError("promotion PR source repository is unavailable")
            if source["full_name"].casefold() != repository.casefold():
                continue
            if user["login"].casefold() != owner:
                raise PromotionAdmissionError("an active same-repository promotion has an unexpected publisher")
            match, sha = _BRANCH.fullmatch(branch), head.get("sha")
            if match is None or not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise PromotionAdmissionError("an active promotion PR has an invalid identity")
            promotions.append(ActivePromotion(number, branch, sha, int(match[1])))
        if len(rows) < 100:
            return tuple(sorted(promotions, key=lambda promotion: promotion.number))
    raise PromotionAdmissionError("open Main PR inventory exceeds the bounded pagination limit")


def require_idle(
    promotions: tuple[ActivePromotion, ...], *,
    candidate_branch: str | None = None, candidate_sha: str | None = None,
) -> None:
    """Only an idempotent, already-qualified PR-opening step may reuse its PR."""
    if candidate_branch is not None or candidate_sha is not None:
        if (
            not isinstance(candidate_branch, str) or not _BRANCH.fullmatch(candidate_branch)
            or not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha)
        ):
            raise PromotionAdmissionError("invalid existing candidate identity")
        if len(promotions) == 1 and (
            promotions[0].branch, promotions[0].head_sha,
        ) == (candidate_branch, candidate_sha):
            return
    if promotions:
        identifiers = ", ".join(f"#{promotion.number}" for promotion in promotions)
        raise PromotionAdmissionError(
            f"promotion PR(s) {identifiers} remain active; resume their existing publication/"
            "merge job or explicitly retire them before starting another generation"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--require-idle", action="store_true")
    parser.add_argument("--candidate-branch")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--automatic-source")
    events = parser.add_mutually_exclusive_group()
    events.add_argument("--source-ci-run", type=int)
    events.add_argument("--completed-promotion-run", type=int)
    events.add_argument("--closed-main-pr", type=int)
    args = parser.parse_args(argv)
    try:
        event_mode = any(value is not None for value in (
            args.source_ci_run, args.completed_promotion_run, args.closed_main_pr,
        ))
        if any(value is not None and not 0 < value < 10 ** 20 for value in (
            args.source_ci_run, args.completed_promotion_run, args.closed_main_pr,
        )):
            raise PromotionAdmissionError("invalid promotion scheduling event identity")
        if event_mode and (
            args.require_idle or args.candidate_branch is not None or args.candidate_sha is not None
            or args.automatic_source is not None
        ):
            raise PromotionAdmissionError("dispatch scheduling cannot reuse an existing candidate")
        if (args.candidate_branch is not None or args.candidate_sha is not None) and not args.require_idle:
            raise PromotionAdmissionError("existing-candidate admission requires --require-idle")
        if args.automatic_source is not None and (
            not args.require_idle or args.candidate_branch is not None or args.candidate_sha is not None
        ):
            raise PromotionAdmissionError("automatic-source admission requires a fresh idle generation")
        require_idle((), candidate_branch=args.candidate_branch, candidate_sha=args.candidate_sha)
        started = time.monotonic()
        promotions = active_promotions(args.repository)
        if args.require_idle:
            require_idle(
                promotions, candidate_branch=args.candidate_branch, candidate_sha=args.candidate_sha,
            )
        if args.automatic_source is not None:
            remaining = 120 - (time.monotonic() - started)
            if remaining <= 0:
                raise PromotionAdmissionError("promotion scheduling deadline expired")
            require_automatic_source(
                args.repository, args.automatic_source, api=_api, wait_seconds=remaining,
            )
        idle = not promotions
        decision = None
        if event_mode and idle:
            remaining = 120 - (time.monotonic() - started)
            if remaining <= 0:
                raise PromotionAdmissionError("promotion scheduling deadline expired")
            decision = pending_source(
                args.repository, source_ci_run=args.source_ci_run,
                completed_promotion_run=args.completed_promotion_run,
                closed_main_pr=args.closed_main_pr, api=_api, wait_seconds=remaining,
            )
        dispatch_sha = decision.source_sha if decision is not None else None
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"promotion_idle={str(idle).lower()}\n")
                if dispatch_sha is not None:
                    output.write(f"dispatch_sha={dispatch_sha}\n")
        result: dict[str, object] = {
            "schema": SCHEMA, "version": 1, "idle": idle,
            "active_promotions": [asdict(promotion) for promotion in promotions],
        }
        if event_mode:
            result.update(
                dispatch=dispatch_sha is not None, source_sha=dispatch_sha,
                reason=decision.reason if decision is not None else "active-promotion",
            )
        print(json.dumps(result, sort_keys=True))
        if not args.require_idle and promotions:
            print(
                "::notice::An existing promotion PR retains ownership; no new promotion is dispatched.",
                file=sys.stderr,
            )
    except (
        PromotionAdmissionError, PromotionDispatchError, VerificationError,
        subprocess.SubprocessError, OSError, ValueError,
    ) as error:
        print(f"::error::promotion-admission: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
