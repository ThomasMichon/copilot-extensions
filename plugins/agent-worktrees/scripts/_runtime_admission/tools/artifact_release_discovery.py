"""Read the existing source-bound artifact channel without publication tooling."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
import subprocess

from artifact_attestation import (
    INDEX_LIMIT, INDEX_NAME, INDEX_SCHEMA, PromotionArtifactError,
    _check, _gh, verify_attestation_subject,
)
from promotion_unit_contract import MAX_PACKET_FILE_BYTES
from promotion_source_history import is_ancestor, read_promoted_source

_BUNDLE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9][A-Za-z0-9.+-]*-[0-9a-f]{64}\.zip")
# Three unit-packet members, archive overhead, and the enclosing evidence member.
MAX_EVIDENCE_BYTES = 4 * MAX_PACKET_FILE_BYTES + 64 * 1024
MAX_EVIDENCE_ASSETS = 64
EVIDENCE_PREFIXES = (
    "promotion-unit-evidence-", "promotion-complete-source-", "promotion-source-manifest-",
    "promotion-build-source-", "promotion-build-manifest-",
)


def supplementary_evidence_asset(asset: dict, dev_sha: str) -> bool:
    """Admit only bounded auxiliary data, never executable artifacts."""
    name = asset.get("name")
    match = re.fullmatch(
        rf"promotion-(unit-evidence|complete-source|source-manifest|build-source|build-manifest)-{re.escape(dev_sha)}"
        r"-([0-9a-f]{40})-sha256-([0-9a-f]{64})\.(zip|json)",
        name,
    ) if isinstance(name, str) and isinstance(dev_sha, str) and re.fullmatch(r"[0-9a-f]{40}", dev_sha) else None
    limits = {"unit-evidence": MAX_EVIDENCE_BYTES, "complete-source": 512 << 20,
              "source-manifest": MAX_PACKET_FILE_BYTES, "build-source": 512 << 20,
              "build-manifest": MAX_PACKET_FILE_BYTES}
    limit = limits[match[1]] if match else 0
    return bool(
        match and match[4] == ("json" if match[1] in ("source-manifest", "build-manifest") else "zip")
        and asset.get("digest") == "sha256:" + match[3]
        and asset.get("state") == "uploaded"
        and type(asset.get("id")) is int and asset["id"] > 0
        and type(asset.get("size")) is int and 0 < asset["size"] <= limit
    )


def is_not_found(result: subprocess.CompletedProcess) -> bool:
    return result.returncode != 0 and re.fullmatch(
        r"(?:gh:\s+[^\r\n]*\(HTTP[ \t]+404\)|\(HTTP[ \t]+404\)|(?:gh:[ \t]+)?HTTP[ \t]+404(?::[^\r\n]*)?)",
        result.stderr.strip(),
    ) is not None


def tag_commit(repository: str, tag: str, *, runner=None) -> str | None:
    invoke = runner or _gh
    result = invoke(["api", f"repos/{repository}/git/ref/tags/{tag}"])
    if result.returncode:
        if is_not_found(result):
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
        result = invoke(["api", f"repos/{repository}/git/tags/{sha}"])
    raise PromotionArtifactError(f"{tag}: annotated tag chain exceeds validation limit")


def read_release(repository: str, tag: str, *, runner=None) -> dict | None:
    result = (runner or _gh)(
        ["api", f"repos/{repository}/releases/tags/{tag}"],
        verification=True, timeout=120,
    )
    if is_not_found(result):
        return None
    try:
        release = json.loads(_check(result))
        if release["draft"] is not False or release["tag_name"] != tag:
            raise PromotionArtifactError(f"{tag}: draft or mismatched release is not published")
        if not isinstance(release["assets"], list):
            raise TypeError("invalid release assets")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PromotionArtifactError(f"{tag}: invalid release response") from exc
    if (
        release.get("prerelease") is not True
        or type(release.get("id")) is not int or release["id"] <= 0
    ):
        raise PromotionArtifactError(f"{tag}: invalid candidate release")
    return release


def download(
    directory: Path, *, repository: str, tag: str, name: str, asset: dict, limit: int,
    runner=None,
) -> None:
    if type(asset.get("size")) is int and asset["size"] > limit:
        raise PromotionArtifactError(f"{tag}: source artifact exceeds its size limit")
    result = (runner or _gh)(
        ["api", f"repos/{repository}/releases/assets/{asset['id']}",
         "-H", "Accept: application/octet-stream"],
        verification=True, binary=True,
    )
    _check(result)
    if (
        not isinstance(result.stdout, bytes) or len(result.stdout) > limit
        or "sha256:" + hashlib.sha256(result.stdout).hexdigest() != asset["digest"]
    ):
        raise PromotionArtifactError(f"{tag}: downloaded frozen artifact digest mismatch")
    (directory / name).write_bytes(result.stdout)


def read_admitted_source_index(
    directory: Path, *, repository: str, dev_head: str, main_before: str,
    content_tree: str, is_ancestor: Callable[[str, str], bool],
    runner=None, verify_subject=None,
) -> tuple[dict, dict[str, dict]] | None:
    """Authenticate discovery against facts retained by the existing source owner.

    Only an actual channel 404 returns None. The source owner supplies ancestry
    against its independently acquired history; the subject cannot nominate its
    own trusted Main. This neither consumes bundle bytes nor approves a profile.
    """
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or any(not re.fullmatch(r"[0-9a-f]{40}", value)
               for value in (dev_head, main_before, content_tree))
        or not callable(is_ancestor)
    ):
        raise PromotionArtifactError("invalid source artifact reuse identity")
    invoke = runner or _gh
    tag = f"python-artifacts-{dev_head}"
    release = read_release(repository, tag, runner=invoke)
    if release is None:
        return None
    if tag_commit(repository, tag, runner=invoke) != dev_head:
        raise PromotionArtifactError(f"{tag}: source release tag is misbound")
    pages = json.loads(_check(invoke(
        ["api", "--paginate", "--slurp",
         f"repos/{repository}/releases/{release['id']}/assets?per_page=100"],
        verification=True,
    )))
    if not isinstance(pages, list) or any(
        not isinstance(page, list) or any(not isinstance(asset, dict) for asset in page)
        for page in pages
    ):
        raise PromotionArtifactError(f"{tag}: invalid source artifact pages")
    assets: dict[str, dict] = {}
    seen, evidence_count = set(), 0
    for page in pages:
        for asset in page:
            name = asset.get("name")
            if not isinstance(name, str) or name in seen:
                raise PromotionArtifactError(f"{tag}: duplicate or invalid source artifact")
            seen.add(name)
            if supplementary_evidence_asset(asset, dev_head):
                evidence_count += 1
                if evidence_count > MAX_EVIDENCE_ASSETS:
                    raise PromotionArtifactError(f"{tag}: too many supplementary evidence assets")
                continue
            if (
                not isinstance(name, str) or name in assets or "/" in name or "\\" in name
                or name in (".", "..") or len(name) > 255
                or (name != INDEX_NAME and not _BUNDLE.fullmatch(name))
                or type(asset.get("id")) is not int or asset["id"] <= 0
                or asset.get("state") != "uploaded"
                or not isinstance(asset.get("digest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"])
            ):
                raise PromotionArtifactError(f"{tag}: unsafe or incomplete source artifact")
            assets[name] = asset
    if INDEX_NAME not in assets or not 1 <= len(assets) <= 65:
        raise PromotionArtifactError(f"{tag}: frozen source artifact index is missing")
    download(
        directory, repository=repository, tag=tag, name=INDEX_NAME,
        asset=assets[INDEX_NAME], limit=INDEX_LIMIT, runner=invoke,
    )
    index = json.loads((directory / INDEX_NAME).read_text(encoding="utf-8"))
    if (
        not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA
        or type(index.get("schema_version")) is not int or index["schema_version"] != 2
        or "main_before" in index
        or index.get("dev_head") != dev_head or index.get("content_tree") != content_tree
        or not isinstance(index.get("artifacts"), list)
        or not 1 <= len(index["artifacts"]) <= 64
    ):
        raise PromotionArtifactError(f"{tag}: frozen source/composition differs from this candidate")
    control = index.get("admission")
    if (
        not isinstance(control, dict)
        or not isinstance(control.get("signer_digest"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", control["signer_digest"])
        or control.get("source_digest") != control["signer_digest"]
        or control.get("source_ref") != "refs/heads/main"
        or not is_ancestor(control["signer_digest"], main_before)
    ):
        raise PromotionArtifactError(f"{tag}: original artifact admission is not trusted Main history")
    (verify_subject or verify_attestation_subject)(
        directory / INDEX_NAME, repository=repository, signer_digest=control["signer_digest"],
        source_digest=control["source_digest"], source_ref=control["source_ref"],
    )
    expected = {INDEX_NAME}
    for entry in index["artifacts"]:
        if not isinstance(entry, dict):
            raise PromotionArtifactError(f"{tag}: invalid frozen artifact entry")
        name, digest = entry.get("filename"), entry.get("sha256")
        if (
            not isinstance(name, str) or name not in assets or name in expected
            or not name.endswith(".zip") or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or assets[name]["digest"] != "sha256:" + digest
        ):
            raise PromotionArtifactError(f"{tag}: frozen artifact set is incomplete or conflicting")
        expected.add(name)
    if expected != assets.keys():
        raise PromotionArtifactError(f"{tag}: unexpected files in the frozen source artifact set")
    return index, assets


def discover_promoted_source(
    directory: Path, *, repository: str, repo: Path, main_commit: str,
    deadline: float, runner=None, verify_subject=None,
) -> tuple[dict, dict[str, dict]] | None:
    """Discover the actual channel selected by the retained source provider."""
    selected = read_promoted_source(repo, main_commit, deadline=deadline)
    return read_admitted_source_index(
        directory, repository=repository, dev_head=selected.dev_head,
        main_before=selected.main_before, content_tree=selected.content_tree,
        is_ancestor=lambda ancestor, descendant: is_ancestor(
            repo, ancestor, descendant, deadline=deadline,
        ),
        runner=runner, verify_subject=verify_subject,
    )


def read_selected_source_artifact(
    directory: Path, *, index: dict, assets: dict[str, dict], repository: str,
    component: str, version: str, candidate_root: Path, runner=None, verify_subject=None,
) -> tuple[dict, str] | None:
    """Validate the actual selected bundle before it can bind a runtime profile.

    The source owner first authenticates this index through discovery. A missing
    component is typed absence; conflicting identities or failed byte/source
    validation are errors, never permission to substitute source fallback.
    """
    from artifact_bundle_validation import (
        _BUNDLE_LIMIT, _artifact_label, validate_artifact_entry, validate_bundle_binding,
    )
    from runtime_profile_release import release_profiles, validate_release_bindings
    from standalone_python_artifacts import project_key

    profiles = release_profiles(index)
    selected, names, projects = [], set(), set()
    for entry in index["artifacts"]:
        name = validate_artifact_entry(entry)
        project = project_key(entry)
        if name in names or project in projects:
            raise PromotionArtifactError("duplicate or mismatched artifact filename")
        names.add(name)
        projects.add(project)
        if _artifact_label(entry) == component:
            if entry["version"] != version:
                raise PromotionArtifactError("selected artifact version differs from acquired source")
            selected.append(entry)
    if not selected:
        return None
    if len(selected) != 1:
        raise PromotionArtifactError("selected component has ambiguous first-party artifacts")
    entry = selected[0]
    name = entry["filename"]
    asset = assets.get(name)
    if not isinstance(asset, dict) or asset.get("digest") != "sha256:" + entry["sha256"]:
        raise PromotionArtifactError("selected artifact differs from its admitted asset")
    download(
        directory, repository=repository, tag=f"python-artifacts-{index['dev_head']}",
        name=name, asset=asset, limit=_BUNDLE_LIMIT, runner=runner,
    )
    path = directory / name
    control = index["admission"]
    (verify_subject or verify_attestation_subject)(
        path, repository=repository, signer_digest=control["signer_digest"],
        source_digest=control["source_digest"], source_ref=control["source_ref"],
    )
    manifest, digest = validate_bundle_binding(path, entry, candidate_root=candidate_root)
    matching = [
        profile.record() for profile in profiles
        if profile.record()["artifact"]["artifact_id"] == entry["artifact_id"]
    ]
    if matching:
        validate_release_bindings(
            {**index, "runtime_profiles": {
                **index["runtime_profiles"], "profiles": matching,
            }},
            {entry["artifact_id"]: (manifest, digest)},
        )
    return manifest, digest
