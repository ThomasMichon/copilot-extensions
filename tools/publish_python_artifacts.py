"""Verify promotion artifact signing identity and publish immutable release assets."""
from __future__ import annotations

import argparse
import hashlib
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
from verify_promotion_workflows import VerificationError


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


def verify_attestation_subject(
    path: Path, *, repository: str, signer_digest: str, source_digest: str,
    source_ref: str,
) -> None:
    """Authenticate one immutable subject against the pinned hosted Main producer."""
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not re.fullmatch(r"[0-9a-f]{40}", signer_digest)
        or not re.fullmatch(r"[0-9a-f]{40}", source_digest)
        or not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", source_ref)
    ):
        raise PromotionArtifactError("invalid expected attestation identity")
    if path.is_symlink() or not path.is_file():
        raise PromotionArtifactError(f"{path.name}: unsafe attestation subject")
    _check(_gh([
        "attestation", "verify", str(path), "--repo", repository,
        "--signer-workflow", f"{repository}/.github/workflows/validate-and-promote.yml",
        "--signer-digest", signer_digest, "--source-digest", source_digest,
        "--source-ref", source_ref, "--deny-self-hosted-runners",
    ], verification=True))


def verify_attestations(
    directory: Path, *, repository: str, signer_digest: str, source_digest: str,
    source_ref: str, repo: Path | None = None,
) -> None:
    """Require the pinned hosted promotion workflow, never repo-write authority."""
    identity = dict(
        repository=repository, signer_digest=signer_digest,
        source_digest=source_digest, source_ref=source_ref,
    )
    index = directory / INDEX_NAME
    verify_attestation_subject(index, **identity)
    try:
        signed_index = json.loads(index.read_text(encoding="utf-8"))
        if not isinstance(signed_index, dict):
            raise PromotionArtifactError("invalid signed promotion artifact index")
        validated = validate_artifacts(
            directory, content_tree=signed_index.get("content_tree"),
            dev_head=signed_index.get("dev_head"), main_before=signed_index.get("main_before"),
        )
        if validated["schema_version"] == 2:
            admission = validated.get("admission")
            if (
                source_ref != "refs/heads/main" or signer_digest != source_digest
                or not isinstance(admission, dict)
                or not isinstance(admission.get("signer_digest"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", admission["signer_digest"])
                or admission.get("source_digest") != admission["signer_digest"]
                or admission.get("source_ref") != "refs/heads/main"
            ):
                raise PromotionArtifactError("source-owned publication requires original trusted Main admission")
            if admission["signer_digest"] != signer_digest:
                import promote_release as promotion

                if not promotion._is_ancestor(
                    admission["signer_digest"], signer_digest,
                    cwd=repo or Path(__file__).resolve().parent.parent,
                ):
                    raise PromotionArtifactError("original source admission is not trusted Main history")
                verify_attestation_subject(
                    index, repository=repository, signer_digest=admission["signer_digest"],
                    source_digest=admission["source_digest"], source_ref=admission["source_ref"],
                )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionArtifactError(f"cannot read signed promotion artifact index: {exc}") from exc
    for entry in validated["artifacts"]:
        verify_attestation_subject(directory / entry["filename"], **identity)


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
    repository: str, admission_cache: dict[tuple[str, str], dict] | None = None,
) -> None:
    """A stable version tag may precede unrelated promotions, but not its payload."""
    import promote_release as promotion

    if tag_commit == merged_commit:
        return
    index = json.loads((directory / INDEX_NAME).read_text(encoding="utf-8"))
    if index["schema_version"] == 2:
        tag_tree = promotion._tree_excluding_state(
            promotion._git(["rev-parse", f"{tag_commit}^{{tree}}"], cwd=repo), repo=repo,
        )
        current_tree = promotion._tree_excluding_state(
            promotion._git(["rev-parse", f"{merged_commit}^{{tree}}"], cwd=repo), repo=repo,
        )
        if tag_tree == current_tree and promotion._git(
            ["ls-tree", tag_commit, promotion.PIPELINE_STATE_PATH], cwd=repo,
        ):
            state = json.loads(promotion._git(
                ["show", f"{tag_commit}:{promotion.PIPELINE_STATE_PATH}"], cwd=repo,
            ))
            if (
                isinstance(state, dict) and isinstance(state.get("last_promotion"), dict)
                and state["last_promotion"].get("dev_head") == index["dev_head"]
            ):
                return
    if not promotion._is_ancestor(tag_commit, merged_commit, cwd=repo):
        if index["schema_version"] != 2:
            raise PromotionArtifactError("release tag is not a shipped ancestor of the merged candidate")
        from promotion_source_alias import validate_prior_source_alias

        validate_prior_source_alias(
            repository=repository, tag_commit=tag_commit, current_commit=merged_commit,
            current_index=index, repo=repo,
            cache={} if admission_cache is None else admission_cache,
        )
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
    admission_cache: dict[tuple[str, str], dict] | None = None,
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
    if admission_cache is None:
        admission_cache = {}
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
                repository=repository, admission_cache=admission_cache,
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


def verify_published_artifacts(
    directory: Path, *, repository: str, candidate_commit: str, dev_head: str, repo: Path,
    admission_cache: dict[tuple[str, str], dict] | None = None,
) -> dict:
    """Read every required final asset with consumer authority before Main moves."""
    index = validate_commit_artifacts(directory, commit=candidate_commit, dev_head=dev_head, repo=repo)
    if admission_cache is None:
        admission_cache = {}
    for entry in index["artifacts"]:
        tag = f"{entry['plugin']}-v{entry['version']}"
        release = _release(
            _gh(["api", f"repos/{repository}/releases/tags/{tag}"], verification=True), tag,
        )
        if type(release.get("id")) is not int or release["id"] <= 0:
            raise PromotionArtifactError(f"{tag}: invalid published release identity")
        tag_commit = _tag_commit(repository, tag)
        if tag_commit is None:
            raise PromotionArtifactError(f"{tag}: published release has no immutable tag")
        _validate_tag_binding(
            directory, entry, tag_commit=tag_commit, merged_commit=candidate_commit, repo=repo,
            repository=repository, admission_cache=admission_cache,
        )
        pages = json.loads(_check(_gh(
            ["api", "--paginate", "--slurp",
             f"repos/{repository}/releases/{release['id']}/assets?per_page=100"],
            verification=True,
        )))
        if not isinstance(pages, list) or any(
            not isinstance(page, list) or any(not isinstance(asset, dict) for asset in page)
            for page in pages
        ):
            raise PromotionArtifactError(f"{tag}: invalid published asset pages")
        matches = [
            asset for page in pages for asset in page
            if isinstance(asset, dict) and asset.get("name") == entry["filename"]
        ]
        if (
            len(matches) != 1 or matches[0].get("digest") != f"sha256:{entry['sha256']}"
            or matches[0].get("state") != "uploaded"
            or type(matches[0].get("id")) is not int or matches[0]["id"] <= 0
        ):
            raise PromotionArtifactError(f"{tag}: required published asset is missing or conflicting")
        downloaded = _gh(
            ["api", f"repos/{repository}/releases/assets/{matches[0]['id']}",
             "-H", "Accept: application/octet-stream"],
            verification=True, binary=True,
        )
        _check(downloaded)
        if (
            not isinstance(downloaded.stdout, bytes)
            or hashlib.sha256(downloaded.stdout).hexdigest() != entry["sha256"]
        ):
            raise PromotionArtifactError(f"{tag}: downloaded published asset digest mismatch")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "prepare", "publish", "publish-candidate"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--signer-digest", required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--merged-commit")
    parser.add_argument("--candidate-commit")
    parser.add_argument("--dev-head")
    parser.add_argument("--main-before")
    parser.add_argument("--promotion-run")
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--qualification-receipt", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    if args.operation == "publish" and (
        not args.merged_commit or not args.candidate_commit or not args.dev_head
    ):
        parser.error("publish requires --merged-commit, --candidate-commit and --dev-head")
    if args.operation == "prepare" and (not args.candidate_commit or not args.dev_head):
        parser.error("prepare requires --candidate-commit and --dev-head")
    if args.operation == "publish-candidate" and not all((
        args.candidate_commit, args.dev_head, args.main_before, args.promotion_run,
        args.candidate_root, args.qualification_receipt,
    )):
        parser.error("publish-candidate requires candidate, source, base and qualification identities")
    try:
        qualification_digest = None
        if args.operation == "publish-candidate":
            if not os.environ.get("ARTIFACT_VERIFY_TOKEN"):
                raise PromotionArtifactError("candidate publication requires independent consumer read authority")
            from qualify_promotion_candidate import verify_receipt

            receipt = json.loads(args.qualification_receipt.read_text(encoding="utf-8"))
            qualification_digest = verify_receipt(
                receipt, repository=args.repository, candidate_sha=args.candidate_commit,
                dev_sha=args.dev_head, main_before=args.main_before,
                control_sha=args.signer_digest, promotion_run=args.promotion_run,
                candidate_root=args.candidate_root,
            )
        verify_attestations(
            args.directory, repository=args.repository, signer_digest=args.signer_digest,
            source_digest=args.source_digest, source_ref=args.source_ref, repo=args.repo,
        )
        repo = args.repo
        if args.operation == "prepare" or (
            args.operation in ("publish", "publish-candidate") and args.candidate_commit
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
        if args.operation == "publish-candidate":
            admission_cache: dict[tuple[str, str], dict] = {}
            publish_artifacts(
                args.directory, repository=args.repository, merged_commit=args.candidate_commit,
                dev_head=args.dev_head, repo=repo, candidate_commit=args.candidate_commit,
                admission_cache=admission_cache,
            )
            index = verify_published_artifacts(
                args.directory, repository=args.repository, candidate_commit=args.candidate_commit,
                dev_head=args.dev_head, repo=repo,
                admission_cache=admission_cache,
            )
            publication = {
                "dev_head": args.dev_head, "candidate": args.candidate_commit,
                "qualification": qualification_digest,
                "index_digest": hashlib.sha256((args.directory / INDEX_NAME).read_bytes()).hexdigest(),
                "artifacts": index["artifacts"],
            }
            digest = hashlib.sha256(json.dumps(publication, sort_keys=True).encode()).hexdigest()
            if args.github_output is not None:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write(f"source_release={tag}\npublication_digest={digest}\n")
            print(f"artifact-published: {digest}")
    except (PromotionArtifactError, VerificationError, OSError, ValueError) as exc:
        print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
