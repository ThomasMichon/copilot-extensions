"""Transport one composed candidate as Git data, never as another release owner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid

from candidate_qualification_receipt import regular_bytes
from promotion_unit_contract import decode_json

SCHEMA = "copilot-extensions.frozen-promotion-candidate"
FILES = {"candidate.bundle", "candidate.json"}
_SHA = re.compile(r"[0-9a-f]{40}")


def validate_record(record: object, *, dev_sha: str, main_before: str, candidate_sha: str) -> dict:
    if (
        not isinstance(record, dict) or set(record) != {
            "schema", "version", "dev_sha", "main_before", "candidate_sha",
            "content_tree", "tree", "prepared_at", "bundle_sha256", "report",
        }
        or record["schema"] != SCHEMA or type(record["version"]) is not int or record["version"] != 1
        or any(not isinstance(record[key], str) or not _SHA.fullmatch(record[key])
               for key in ("dev_sha", "main_before", "candidate_sha", "content_tree", "tree"))
        or record["dev_sha"] != dev_sha or record["main_before"] != main_before
        or record["candidate_sha"] != candidate_sha
        or not isinstance(record["bundle_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", record["bundle_sha256"])
        or not isinstance(record["report"], dict)
        or not isinstance(record["prepared_at"], str)
    ):
        raise ValueError("frozen candidate does not bind the admitted source/base")
    from promotion_candidate import CandidateIdentityError, candidate_clock

    try:
        candidate_clock(record["prepared_at"])
    except CandidateIdentityError as exc:
        raise ValueError("frozen candidate preparation time is invalid") from exc
    report = record["report"]
    if any(report.get(key) != record[field] for key, field in (
        ("dev_head", "dev_sha"), ("main_before", "main_before"), ("commit", "candidate_sha"),
    )):
        raise ValueError("frozen candidate report identity conflicts")
    return record


def export_candidate(
    directory: Path, *, repo: Path, report: dict, content_tree: str, prepared_at: str,
) -> dict:
    import promote_release as promotion

    directory.mkdir(parents=True, exist_ok=False)
    reference = f"refs/promotion-freeze/{uuid.uuid4().hex}"
    commit = report["commit"]
    try:
        promotion._git(["update-ref", reference, commit], cwd=repo)
        promotion._git(
            ["bundle", "create", str((directory / "candidate.bundle").resolve()),
             reference, "^" + report["main_before"]], cwd=repo,
        )
    finally:
        promotion._git(["update-ref", "-d", reference], cwd=repo)
    record = {
        "schema": SCHEMA, "version": 1,
        "dev_sha": report["dev_head"], "main_before": report["main_before"],
        "candidate_sha": commit, "content_tree": content_tree,
        "tree": promotion._git(["rev-parse", f"{commit}^{{tree}}"], cwd=repo),
        "prepared_at": prepared_at,
        "bundle_sha256": hashlib.sha256((directory / "candidate.bundle").read_bytes()).hexdigest(),
        "report": report,
    }
    validate_record(
        record, dev_sha=report["dev_head"], main_before=report["main_before"], candidate_sha=commit,
    )
    (directory / "candidate.json").write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    return record


def import_candidate(
    directory: Path, *, repo: Path, dev_sha: str, main_before: str, candidate_sha: str,
) -> dict:
    """Import only object data and recheck the full candidate and content trees."""
    import promote_release as promotion

    if any(not isinstance(value, str) or not _SHA.fullmatch(value)
           for value in (dev_sha, main_before, candidate_sha)):
        raise ValueError("invalid expected candidate commit identity")
    if directory.is_symlink() or {path.name for path in directory.iterdir()} != FILES:
        raise ValueError("frozen candidate transport has an unexpected inventory")
    record = validate_record(
        decode_json(regular_bytes(directory, "candidate.json")),
        dev_sha=dev_sha, main_before=main_before, candidate_sha=candidate_sha,
    )
    bundle = directory / "candidate.bundle"
    if bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("frozen candidate bundle is missing, indirect or oversized")
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != record["bundle_sha256"]:
        raise ValueError("frozen candidate bundle bytes changed")
    promotion._git(["bundle", "verify", str(bundle.resolve())], cwd=repo)
    heads = promotion._git(["bundle", "list-heads", str(bundle.resolve())], cwd=repo).splitlines()
    if len(heads) != 1 or heads[0].split()[0] != record["candidate_sha"]:
        raise ValueError("frozen bundle advertises another candidate")
    promotion._git(
        ["fetch", "--no-tags", "--no-write-fetch-head", str(bundle.resolve()), record["candidate_sha"]],
        cwd=repo,
    )
    if (
        promotion._git(["show", "-s", "--format=%P", record["candidate_sha"]], cwd=repo) != main_before
        or promotion._git(["rev-parse", record["candidate_sha"] + "^{tree}"], cwd=repo) != record["tree"]
        or promotion._tree_excluding_state(record["tree"], repo=repo) != record["content_tree"]
    ):
        raise ValueError("frozen candidate Git object differs from its composition")
    state = decode_json(promotion._git(
        ["show", record["candidate_sha"] + ":" + promotion.PIPELINE_STATE_PATH], cwd=repo,
    ))
    last = state.get("last_promotion", {})
    if (
        last.get("dev_head") != dev_sha or last.get("main_before") != main_before
        or last.get("promoted_at") != record["prepared_at"]
    ):
        raise ValueError("frozen candidate state differs from its preparation")
    return record


def publish_frozen(
    directory: Path, *, repo: Path, dev_head: str, main_head: str,
    promotion_time: str | None, python_artifacts_dir: Path | None,
    push: bool, candidate_branch: str | None, candidate_sha: str,
) -> dict:
    import promote_release as promotion
    from promotion_python_artifacts import validate_composed_artifacts

    record = import_candidate(
        directory, repo=repo, dev_sha=dev_head, main_before=main_head, candidate_sha=candidate_sha,
    )
    if promotion_time is not None and promotion_time != record["prepared_at"]:
        raise promotion.PromotionError("frozen candidate preparation time changed")
    if python_artifacts_dir is None:
        raise promotion.PromotionError("frozen publication requires validated candidate artifacts")
    if push and not candidate_branch:
        raise promotion.PromotionError("frozen publication requires the normal candidate PR path")
    scratch = promotion.add_scratch_worktree(record["candidate_sha"], repo=repo)
    try:
        promotion._git(["read-tree", record["content_tree"]], cwd=scratch)
        validate_composed_artifacts(
            python_artifacts_dir, scratch=scratch, content_tree=record["content_tree"],
            dev_head=dev_head, main_before=main_head,
        )
        if push:
            promotion.push_candidate(record["candidate_sha"], candidate_branch, cwd=scratch, git=promotion._git)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)
    return {
        **record["report"], "pushed": push,
        "candidate_branch": candidate_branch if push else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--dev-sha", required=True)
    parser.add_argument("--main-before", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--build-artifacts", type=Path)
    parser.add_argument("--validate-artifacts", type=Path)
    parser.add_argument("--reuse-artifacts-repository")
    args = parser.parse_args()
    record = import_candidate(
        args.directory, repo=args.repo, dev_sha=args.dev_sha, main_before=args.main_before,
        candidate_sha=args.candidate_sha,
    )
    if args.checkout is not None:
        import promote_release as promotion

        promotion._git(
            ["worktree", "add", "--detach", str(args.checkout.resolve()), record["candidate_sha"]],
            cwd=args.repo,
        )
    if args.build_artifacts is not None:
        from promotion_python_artifacts import export_artifacts

        export_artifacts(
            repo=args.repo, commit=record["candidate_sha"], content_tree=record["content_tree"],
            dev_head=args.dev_sha, main_before=args.main_before, out_dir=args.build_artifacts,
            source_policy="seasoned-public", min_age_days=7,
            reuse_repository=args.reuse_artifacts_repository,
        )
    if args.validate_artifacts is not None:
        import promote_release as promotion
        from promotion_python_artifacts import validate_composed_artifacts

        scratch = promotion.add_scratch_worktree(record["candidate_sha"], repo=args.repo)
        try:
            promotion._git(["read-tree", record["content_tree"]], cwd=scratch)
            validate_composed_artifacts(
                args.validate_artifacts, scratch=scratch, content_tree=record["content_tree"],
                dev_head=args.dev_sha, main_before=args.main_before,
                source_policy="seasoned-public", min_age_days=7,
            )
        finally:
            promotion.remove_scratch_worktree(scratch, repo=args.repo)
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
