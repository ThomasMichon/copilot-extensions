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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from promotion_python_artifacts import (
    INDEX_NAME, PromotionArtifactError, candidate_projects, validate_artifacts,
    validate_source_binding,
)
from verify_promotion_workflows import VerificationError
from standalone_python_artifacts import artifact_label


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
) -> dict:
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
    def verify(entry: dict) -> None:
        verify_attestation_subject(directory / entry["filename"], **identity)

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(verify, validated["artifacts"]))
    return validated


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


def verify_asset_downloads(repository: str, assets: list[tuple[str, int, str]]) -> None:
    """Read every independently validated asset with bounded consumer authority."""
    def verify(asset: tuple[str, int, str]) -> None:
        subject, identifier, digest = asset
        downloaded = _gh(
            ["api", f"repos/{repository}/releases/assets/{identifier}",
             "-H", "Accept: application/octet-stream"],
            verification=True, binary=True, timeout=120,
        )
        _check(downloaded)
        if (
            not isinstance(downloaded.stdout, bytes)
            or hashlib.sha256(downloaded.stdout).hexdigest() != digest
        ):
            raise PromotionArtifactError(f"{subject}: downloaded published asset digest mismatch")

    with ThreadPoolExecutor(max_workers=4) as workers:
        for _ in workers.map(verify, assets):
            pass


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
    """Verify the source-owned set, or publish legacy version-1 assets."""
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
    if index["schema_version"] == 2:
        from promotion_artifact_readiness import verify_ready_artifacts

        verify_ready_artifacts(
            directory, repository=repository, candidate_commit=merged_commit,
            dev_head=dev_head, repo=repo,
        )
        return
    if admission_cache is None:
        admission_cache = {}
    for entry in index["artifacts"]:
        tag = f"{artifact_label(entry)}-v{entry['version']}"
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
    if index["schema_version"] == 2:
        from promotion_artifact_readiness import verify_ready_artifacts

        verify_ready_artifacts(
            directory, repository=repository, candidate_commit=candidate_commit,
            dev_head=dev_head, repo=repo,
        )
        return index
    if admission_cache is None:
        admission_cache = {}
    downloads = []
    for entry in index["artifacts"]:
        tag = f"{artifact_label(entry)}-v{entry['version']}"
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
        downloads.append((f"{tag}/{entry['filename']}", matches[0]["id"], entry["sha256"]))
    verify_asset_downloads(repository, downloads)
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
    parser.add_argument(
        "--qualification-profile", choices=("legacy-ci-v1", "source-units-v2", "candidate-units-v3"), default="legacy-ci-v1",
    )
    parser.add_argument("--unit-packet-dir", type=Path)
    parser.add_argument("--complete-source-dir", type=Path)
    parser.add_argument("--unit-manifest")
    parser.add_argument("--dev-root", type=Path)
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
    unit_inputs = (args.unit_packet_dir, args.unit_manifest, args.dev_root)
    if args.qualification_profile in ("source-units-v2", "candidate-units-v3"):
        if args.operation != "publish-candidate" or any(value is None for value in unit_inputs):
            parser.error("source-units-v2 publication requires candidate publication, unit packet, manifest and Dev checkout")
    elif any(value is not None for value in unit_inputs):
        parser.error("legacy qualification cannot consume source-unit packet inputs")
    try:
        qualification_digest = None
        if args.operation == "publish-candidate":
            if not os.environ.get("ARTIFACT_VERIFY_TOKEN"):
                raise PromotionArtifactError("candidate publication requires independent consumer read authority")
            from qualify_promotion_candidate import verify_receipt

            if args.qualification_profile in ("source-units-v2", "candidate-units-v3"):
                from promotion_unit_contract import decode_json

                receipt = decode_json(args.qualification_receipt.read_bytes())
            else:
                receipt = json.loads(args.qualification_receipt.read_text(encoding="utf-8"))
            unit_arguments = {}
            if args.qualification_profile in ("source-units-v2", "candidate-units-v3"):
                unit_arguments = {
                    "dev_root": args.dev_root, "unit_packet_dir": args.unit_packet_dir,
                    "unit_manifest": args.unit_manifest,
                }
            qualification_digest = verify_receipt(
                receipt, repository=args.repository, candidate_sha=args.candidate_commit,
                dev_sha=args.dev_head, main_before=args.main_before,
                control_sha=args.signer_digest, promotion_run=args.promotion_run,
                candidate_root=args.candidate_root,
                expected_profile=args.qualification_profile, **unit_arguments,
            )
        signed_index = verify_attestations(
            args.directory, repository=args.repository, signer_digest=args.signer_digest,
            source_digest=args.source_digest, source_ref=args.source_ref, repo=args.repo,
        )
        repo = args.repo
        source_candidate = (
            args.operation == "publish-candidate" and signed_index["schema_version"] == 2
        )
        if source_candidate:
            if args.qualification_profile in ("source-units-v2", "candidate-units-v3") and args.complete_source_dir is None:
                raise PromotionArtifactError("source candidate publication requires complete source subjects")
            from promotion_artifact_readiness import verify_ready_artifact_set

            tag, index = verify_ready_artifact_set(
                args.directory, repository=args.repository, candidate_commit=args.candidate_commit,
                dev_head=args.dev_head, repo=repo,
            )
            print(f"artifact-ready: {tag}")
        elif args.operation == "prepare" or (
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
            if not source_candidate:
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
            if source_candidate and args.qualification_profile in ("source-units-v2", "candidate-units-v3"):
                from promotion_release_evidence import publish_evidence

                publication["unit_evidence"] = publish_evidence(
                    args.directory, packet_directory=args.unit_packet_dir,
                    repository=args.repository, tag=tag, dev_sha=args.dev_head,
                    candidate_sha=args.candidate_commit, index=index, receipt=receipt,
                    qualification_digest=qualification_digest,
                )
                print(f"unit-evidence-published: {tag}/{publication['unit_evidence']['filename']}")
                from promotion_release_evidence import publish_complete_source

                publication["complete_source"] = publish_complete_source(
                    args.directory, source_directory=args.complete_source_dir,
                    repository=args.repository, tag=tag, index=index, receipt=receipt,
                    qualification_digest=qualification_digest,
                )
                print(f"complete-source-published: {tag}/{args.candidate_commit}")
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
