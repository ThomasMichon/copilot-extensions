"""Verify promotion artifact signing identity and publish immutable release assets."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from promotion_python_artifacts import (
    INDEX_NAME, PromotionArtifactError, candidate_projects, validate_artifacts,
    validate_source_binding,
)


def _gh(
    argv: list[str], *, verification: bool = False, binary: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if verification and "ARTIFACT_VERIFY_TOKEN" in env:
        env["GH_TOKEN"] = env["ARTIFACT_VERIFY_TOKEN"]
    env.pop("ARTIFACT_VERIFY_TOKEN", None)
    return subprocess.run(
        ["gh", *argv], capture_output=True, text=not binary, env=env, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _check(result: subprocess.CompletedProcess) -> str:
    if result.returncode:
        raise PromotionArtifactError(
            f"gh failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _is_not_found(result: subprocess.CompletedProcess) -> bool:
    return result.returncode != 0 and re.fullmatch(
        r"(?:gh:\s+[^\r\n]*\(HTTP[ \t]+404\)|\(HTTP[ \t]+404\)|(?:gh:[ \t]+)?HTTP[ \t]+404(?::[^\r\n]*)?)",
        result.stderr.strip(),
    ) is not None


def verify_attestations(
    directory: Path, *, repository: str, signer_digest: str, source_digest: str,
    source_ref: str,
) -> None:
    """Require the pinned hosted promotion workflow, never repo-write authority."""
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not re.fullmatch(r"[0-9a-f]{40}", signer_digest)
        or not re.fullmatch(r"[0-9a-f]{40}", source_digest)
        or not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", source_ref)
    ):
        raise PromotionArtifactError("invalid expected attestation identity")
    index = directory / INDEX_NAME
    if not index.is_file() or index.is_symlink():
        raise PromotionArtifactError("missing or unsafe promotion artifact index")
    # Verify the index before using any publisher-controlled identity fields.
    def verify(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise PromotionArtifactError(f"{path.name}: unsafe attestation subject")
        _check(_gh([
            "attestation", "verify", str(path), "--repo", repository,
            "--signer-workflow", f"{repository}/.github/workflows/validate-and-promote.yml",
            "--signer-digest", signer_digest, "--source-digest", source_digest,
            "--source-ref", source_ref, "--deny-self-hosted-runners",
        ], verification=True))

    verify(index)
    try:
        signed_index = json.loads(index.read_text(encoding="utf-8"))
        if not isinstance(signed_index, dict):
            raise PromotionArtifactError("invalid signed promotion artifact index")
        validated = validate_artifacts(
            directory, content_tree=signed_index.get("content_tree"),
            dev_head=signed_index.get("dev_head"), main_before=signed_index.get("main_before"),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionArtifactError(f"cannot read signed promotion artifact index: {exc}") from exc
    for entry in validated["artifacts"]:
        verify(directory / entry["filename"])


def _tag_commit(repository: str, tag: str) -> str | None:
    result = _gh(["api", f"repos/{repository}/git/ref/tags/{tag}"])
    if result.returncode:
        if _is_not_found(result):
            return None
        _check(result)
    seen = set()
    for _ in range(8):
        try:
            obj = json.loads(_check(result))["object"]
            kind, sha = obj["type"], obj["sha"]
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) or sha in seen:
                raise ValueError("invalid or cyclic tag object")
            seen.add(sha)
            if kind == "commit":
                return sha
            if kind != "tag":
                raise ValueError("tag does not resolve to a commit")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise PromotionArtifactError(f"{tag}: invalid tag response") from exc
        result = _gh(["api", f"repos/{repository}/git/tags/{sha}"])
    raise PromotionArtifactError(f"{tag}: annotated tag chain exceeds validation limit")


def _validate_tag_binding(
    directory: Path, entry: dict, *, tag_commit: str, merged_commit: str, repo: Path,
) -> None:
    """A stable version tag may precede unrelated promotions, but not its payload."""
    import promote_release as promotion

    if tag_commit == merged_commit:
        return
    if not promotion._is_ancestor(tag_commit, merged_commit, cwd=repo):
        raise PromotionArtifactError("release tag is not a shipped ancestor of the merged candidate")
    scratch = promotion.add_scratch_worktree(tag_commit, repo=repo)
    try:
        with zipfile.ZipFile(directory / entry["filename"]) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
        validate_source_binding(manifest, scratch)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def _release(result: subprocess.CompletedProcess, tag: str) -> dict:
    try:
        release = json.loads(_check(result))
        if release["draft"] is not False or release["tag_name"] != tag:
            raise PromotionArtifactError(f"{tag}: draft or mismatched release is not published")
        if not isinstance(release["assets"], list):
            raise TypeError("invalid release assets")
        return release
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PromotionArtifactError(f"{tag}: invalid release response") from exc


def validate_commit_artifacts(
    directory: Path, *, commit: str, dev_head: str, repo: Path,
) -> dict:
    """Bind the signed set to a commit's actual source tree and parent."""
    import promote_release as promotion

    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PromotionArtifactError("invalid promotion commit")
    tree = promotion._git(["rev-parse", f"{commit}^{{tree}}"], cwd=repo)
    main_before = promotion._git(["rev-parse", f"{commit}^"], cwd=repo)
    scratch = promotion.add_scratch_worktree(commit, repo=repo)
    try:
        return validate_artifacts(
            directory, content_tree=promotion._tree_excluding_state(tree, repo=repo),
            dev_head=dev_head, main_before=main_before, projects=candidate_projects(scratch),
            candidate_root=scratch,
        )
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def publish_artifacts(
    directory: Path, *, repository: str, merged_commit: str, dev_head: str, repo: Path,
    candidate_commit: str | None = None,
) -> None:
    """Publish only after verifying the actual merged candidate's exact composition."""
    if candidate_commit is not None:
        import promote_release as promotion

        if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (candidate_commit, merged_commit)):
            raise PromotionArtifactError("invalid candidate/final commit identity")
        candidate_tree = promotion._git(["rev-parse", f"{candidate_commit}^{{tree}}"], cwd=repo)
        merged_tree = promotion._git(["rev-parse", f"{merged_commit}^{{tree}}"], cwd=repo)
        if candidate_tree != merged_tree:
            raise PromotionArtifactError("final merge differs from the complete ready candidate tree")
    index = validate_commit_artifacts(
        directory, commit=merged_commit, dev_head=dev_head, repo=repo,
    )
    for entry in index["artifacts"]:
        tag = f"{entry['plugin']}-v{entry['version']}"
        asset = str(directory / entry["filename"])
        result = _gh(["api", f"repos/{repository}/releases/tags/{tag}"])
        absent = False
        if result.returncode:
            if not _is_not_found(result):
                _check(result)
            absent = True
        release = None if absent else _release(result, tag)
        tag_commit = _tag_commit(repository, tag)
        if tag_commit is not None:
            _validate_tag_binding(
                directory, entry, tag_commit=tag_commit, merged_commit=merged_commit, repo=repo,
            )
        elif not absent:
            raise PromotionArtifactError(f"{tag}: published release has no tag")
        if absent:
            _check(_gh([
                "release", "create", tag, "--repo", repository, "--target", merged_commit,
                "--title", tag, "--notes",
                "Attested first-party Python wheels. Runtime dependency admission is "
                "consumer-side; these assets contain no third-party runtime packages.",
            ]))
            created_commit = _tag_commit(repository, tag)
            if created_commit != (tag_commit or merged_commit):
                raise PromotionArtifactError(f"{tag}: release tag changed during creation")
            release = _release(_gh(["api", f"repos/{repository}/releases/tags/{tag}"]), tag)
        if release is None:
            raise PromotionArtifactError(f"{tag}: release state was not validated")
        try:
            matches = [a for a in release["assets"] if a["name"] == entry["filename"]]
        except (KeyError, TypeError) as exc:
            raise PromotionArtifactError(f"{tag}: invalid release response") from exc
        if matches:
            if len(matches) != 1 or matches[0].get("digest") != f"sha256:{entry['sha256']}":
                raise PromotionArtifactError(f"{tag}: existing immutable asset digest mismatch")
            continue
        _check(_gh(["release", "upload", tag, "--repo", repository, asset]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "prepare", "publish"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--signer-digest", required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--merged-commit")
    parser.add_argument("--candidate-commit")
    parser.add_argument("--dev-head")
    args = parser.parse_args(argv)
    if args.operation == "publish" and (
        not args.merged_commit or not args.candidate_commit or not args.dev_head
    ):
        parser.error("publish requires --merged-commit, --candidate-commit and --dev-head")
    if args.operation == "prepare" and (not args.candidate_commit or not args.dev_head):
        parser.error("prepare requires --candidate-commit and --dev-head")
    try:
        verify_attestations(
            args.directory, repository=args.repository, signer_digest=args.signer_digest,
            source_digest=args.source_digest, source_ref=args.source_ref,
        )
        repo = Path(__file__).resolve().parent.parent
        if args.operation == "prepare" or (
            args.operation == "publish" and args.candidate_commit
        ):
            from promotion_artifact_readiness import prepare_artifacts, verify_ready_artifacts

            operation = prepare_artifacts if args.operation == "prepare" else verify_ready_artifacts
            tag = operation(
                args.directory, repository=args.repository, candidate_commit=args.candidate_commit,
                dev_head=args.dev_head, repo=repo,
            )
            print(f"artifact-ready: {tag}")
        if args.operation == "publish":
            publish_artifacts(
                args.directory, repository=args.repository, merged_commit=args.merged_commit,
                dev_head=args.dev_head, repo=repo, candidate_commit=args.candidate_commit,
            )
    except PromotionArtifactError as exc:
        print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
