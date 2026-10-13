#!/usr/bin/env python3
"""Tag a landed promotion commit from the release-pipeline state it carries.

``promote_release.py`` records only the tag *stem* (``promote-<UTC ts>``) in
``.github/release-pipeline-state.json``, because the landed commit's sha is
not known until it exists -- on the pull-request path the squash-merge
produces it. This tool runs after the merge and names the tag
``<stem>-<merge sha8>`` from the state file the merged commit itself carries,
so the pushed tag and the state it releases always agree.

Refuses (exit 1) when the merged commit is not on the main branch, its state
does not name the promoted dev commit, its stem is missing, or the tag
already points somewhere else. Re-running on the same commit is a no-op.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from coverage_guided_selection.ancestor_resolution import scrubbed_git_env
from promote_release import PIPELINE_STATE_PATH, PROMOTION_TAG_PREFIX

TAG_PREFIX = f"{PROMOTION_TAG_PREFIX}-"


class TagError(Exception):
    pass


def _git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    # Scrub inherited repository-selection variables (GIT_DIR, GIT_WORK_TREE,
    # ...) so `cwd=repo` really is the repository this fetches, tags and pushes.
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
        env=scrubbed_git_env(),
    )
    if check and result.returncode != 0:
        raise TagError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def tag_promotion(
    *,
    merge_sha: str,
    dev_sha: str,
    message: str,
    repo: Path,
    remote: str = "origin",
    main_branch: str = "main",
) -> tuple[str, bool]:
    """Create and push the promotion tag. Returns ``(tag_name, created)``;
    ``created`` is False when the tag already pointed at ``merge_sha``."""
    _git(["fetch", "-q", remote, main_branch], cwd=repo)
    known = _git(["rev-parse", "-q", "--verify", f"{merge_sha}^{{commit}}"], cwd=repo, check=False)
    if known.returncode != 0:
        raise TagError(f"{merge_sha} is not a known commit after fetching {remote}/{main_branch}")
    # Name and compare with the full commit id, so an abbreviated --merge-sha
    # yields the same tag and a re-run is still a no-op.
    merge_sha = known.stdout.strip()
    on_main = _git(
        ["merge-base", "--is-ancestor", merge_sha, f"{remote}/{main_branch}"],
        cwd=repo, check=False,
    )
    if on_main.returncode != 0:
        raise TagError(f"{merge_sha} is not on {remote}/{main_branch}")

    shown = _git(["show", f"{merge_sha}:{PIPELINE_STATE_PATH}"], cwd=repo, check=False)
    if shown.returncode != 0:
        raise TagError(f"{PIPELINE_STATE_PATH} is missing at {merge_sha}")
    try:
        promotion = json.loads(shown.stdout).get("last_promotion") or {}
    except (json.JSONDecodeError, AttributeError) as exc:
        raise TagError(f"{PIPELINE_STATE_PATH} at {merge_sha} is not valid state: {exc}") from exc
    if not isinstance(promotion, dict):
        raise TagError(f"{PIPELINE_STATE_PATH} at {merge_sha} has a non-object last_promotion")

    state_dev_head = promotion.get("dev_head")
    if state_dev_head != dev_sha:
        raise TagError(
            f"{PIPELINE_STATE_PATH} at {merge_sha} records dev_head={state_dev_head}, "
            f"expected the promoted {dev_sha}"
        )
    stem = promotion.get("tag")
    if not isinstance(stem, str) or not stem.startswith(TAG_PREFIX):
        raise TagError(f"{PIPELINE_STATE_PATH} at {merge_sha} has no {TAG_PREFIX}* tag stem ({stem!r})")

    tag_name = f"{stem}-{merge_sha[:8]}"
    # Ask for both the ref and its peeled form: an annotated tag reports the
    # commit on `^{}`, a lightweight tag only on the plain ref.
    listed: dict[str, str] = {}
    for line in _git(
        ["ls-remote", remote, f"refs/tags/{tag_name}", f"refs/tags/{tag_name}^{{}}"], cwd=repo
    ).stdout.splitlines():
        sha, _, ref = line.partition("\t")
        listed[ref] = sha
    existing = listed.get(f"refs/tags/{tag_name}^{{}}") or listed.get(f"refs/tags/{tag_name}")
    if existing:
        if existing == merge_sha:
            return tag_name, False
        raise TagError(f"{tag_name} already exists on {existing}, not {merge_sha}")

    # A previous run may have created the tag locally and then failed to push.
    local = _git(["rev-parse", "-q", "--verify", f"refs/tags/{tag_name}^{{commit}}"], cwd=repo, check=False)
    if local.returncode == 0:
        if local.stdout.strip() != merge_sha:
            raise TagError(f"local tag {tag_name} points at {local.stdout.strip()}, not {merge_sha}")
    else:
        _git(["tag", "-a", tag_name, "-m", message, merge_sha], cwd=repo)
    _git(["push", "-q", remote, f"refs/tags/{tag_name}"], cwd=repo)
    return tag_name, True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--merge-sha", required=True, help="the landed promotion commit")
    ap.add_argument("--dev-sha", required=True, help="the dev commit this run promoted")
    ap.add_argument("--message", default="release: promote dev to main")
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--main-branch", default="main")
    args = ap.parse_args(argv)
    try:
        tag_name, created = tag_promotion(
            merge_sha=args.merge_sha, dev_sha=args.dev_sha, message=args.message,
            repo=args.repo, remote=args.remote, main_branch=args.main_branch,
        )
    except TagError as exc:
        print(f"::error::tag-promotion: {exc}", file=sys.stderr)
        return 1
    if created:
        print(f"tag-promotion: pushed {tag_name} -> {args.merge_sha}")
    else:
        print(f"tag-promotion: {tag_name} already points at {args.merge_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
