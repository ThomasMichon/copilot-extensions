"""First-party wheel bundles built from a promotion's composed candidate."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from artifact_bundle_validation import (
    _artifact_label, _digest,
    inspect_wheel as inspect_wheel,
    validate_artifact_entry,
    validate_bundle as validate_bundle,
    validate_bundle_binding,
    validate_manifest as validate_manifest,
    validate_source_binding as validate_source_binding,
    validate_wheel as validate_wheel,
    validate_wheel_sources as validate_wheel_sources,
)
from artifact_source_build import (
    PromotionArtifactError, validate_static_build,
    validate_runtime_metadata as validate_runtime_metadata,
)
from promotion_bundle_members import _write_bundle_members, candidate_projects as candidate_projects
from standalone_python_artifacts import COMPONENTS as COMPONENTS, artifact_label as artifact_label, project_key
from artifact_attestation import INDEX_LIMIT as INDEX_LIMIT, INDEX_NAME, INDEX_SCHEMA as SCHEMA

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))

_BUNDLES_NAME = "wheel-bundles.json"
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLUGIN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.+-]*")


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise PromotionArtifactError(
            f"{argv[0]} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _bundle_filename(manifest: dict) -> str:
    plugin, version = _artifact_label(manifest), manifest["version"]
    artifact_id = manifest["artifact_id"]
    if (
        not isinstance(plugin, str) or not _PLUGIN.fullmatch(plugin)
        or not isinstance(version, str) or not _VERSION.fullmatch(version)
        or not isinstance(artifact_id, str)
        or not artifact_id.startswith("sha256:")
        or not _DIGEST.fullmatch(artifact_id[7:])
    ):
        raise PromotionArtifactError("invalid artifact bundle identity")
    return f"{plugin}-{version}-{artifact_id[7:]}.zip"


def bundle_wheels(manifest: dict, wheel_dir: Path, out_dir: Path) -> dict:
    """Bundle only the builder's declared first-party wheels and manifest."""
    filename = _bundle_filename(manifest)
    destination = out_dir / filename

    # Wheels are already compressed; storing them avoids zlib-version drift too.
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
        def write_wheel(wheel, info):
            name = wheel["filename"]
            path = wheel_dir / name
            try:
                if path.is_symlink() or f"sha256:{_digest(path)}" != wheel["sha256"]:
                    raise PromotionArtifactError(f"{name}: wheel digest changed before bundling")
                with path.open("rb") as source, bundle.open(info, "w") as target:
                    shutil.copyfileobj(source, target, length=1 << 20)
            except OSError as exc:
                raise PromotionArtifactError(f"{name}: cannot read wheel for bundling: {exc}") from exc

        _write_bundle_members(bundle, manifest, write_wheel)
    return {
        **{key: manifest[key] for key in ("plugin", "component", "composition") if key in manifest},
        "version": manifest["version"], "artifact_id": manifest["artifact_id"],
        "filename": filename, "sha256": _digest(destination),
    }


def build_candidate_bundles(
    root: Path, out_dir: Path, *, source_policy: str, min_age_days: int | None,
) -> None:
    """Trusted driver builds candidate data using exactly one shared lock."""
    import promote_release as promotion
    from application_sources import canonical_application
    from build_toolchain_lock import resolve_toolchain_lock

    builder = promotion._load_module(
        Path(__file__).with_name("build_python_artifacts.py"), "trusted_promotion_builder",
    )
    builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR = root, root / "plugins", root / "libs"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if any(out_dir.iterdir()):
            raise PromotionArtifactError("candidate bundle directory must be empty")
    except OSError as exc:
        raise PromotionArtifactError(f"{out_dir}: cannot prepare candidate bundle directory: {exc}") from exc
    projects = candidate_projects(root)
    from standalone_python_artifacts import build_standalone_artifacts, source_closure, validate_build
    for plugin in projects:
        if plugin == "composition:core":
            from core_python_artifacts import source_closure as core_closure
            core_closure(root, builder)
            continue
        if plugin.startswith("standalone:"):
            component = plugin.split(":", 1)[1]
            directory, dependencies, _resources = source_closure(component, root, builder)
            validate_build(directory, component=component, root=root)
            for _name, path in dependencies:
                validate_static_build(path)
            continue
        directory = canonical_application(root, plugin)
        for source in [directory, *(path for _name, path in builder.resolve_vendored_libs(directory))]:
            from plugin_runtime_resources import build_inputs
            build_inputs(source, root=root)
    entries = []
    with tempfile.TemporaryDirectory(prefix="promotion-build-toolchain-") as temporary:
        toolchain = resolve_toolchain_lock(
            Path(temporary) / "venv", python=sys.executable,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        for plugin, version in projects.items():
            if plugin == "composition:core":
                from core_python_artifacts import build_core_artifacts
                wheel_dir = out_dir / "composition-core"
                manifest = build_core_artifacts(
                    out_dir=wheel_dir, toolchain=toolchain, builder=builder,
                )
                if manifest["version"] != version or manifest["build_toolchain"] != toolchain.record:
                    raise PromotionArtifactError("core builder returned another release or toolchain")
                entries.append(bundle_wheels(manifest, wheel_dir, out_dir))
                shutil.rmtree(wheel_dir)
                continue
            standalone = plugin.startswith("standalone:")
            component = plugin.split(":", 1)[1] if standalone else None
            wheel_dir = out_dir / (f"standalone-{component}" if standalone else plugin)
            manifest = (
                build_standalone_artifacts(
                    component, out_dir=wheel_dir, toolchain=toolchain, builder=builder,
                ) if standalone else builder.build_plugin_artifacts(
                    plugin, out_dir=wheel_dir, toolchain=toolchain,
                )
            )
            if project_key(manifest) != plugin or manifest["version"] != version:
                raise PromotionArtifactError(f"{plugin}: builder returned another project identity")
            if manifest["build_toolchain"] != toolchain.record:
                raise PromotionArtifactError(f"{plugin}: builder did not use the trusted shared toolchain")
            entries.append(bundle_wheels(manifest, wheel_dir, out_dir))
            shutil.rmtree(wheel_dir)
    (out_dir / _BUNDLES_NAME).write_text(json.dumps(entries), encoding="utf-8")


def export_artifacts(
    *, repo: Path, commit: str, content_tree: str, dev_head: str, main_before: str,
    out_dir: Path, source_policy: str = "governed", min_age_days: int | None = None,
    reuse_repository: str | None = None,
) -> dict:
    """Build in a disposable candidate checkout; never mutate the promoted tree."""
    import promote_release as promotion

    if out_dir.exists():
        raise PromotionArtifactError(f"{out_dir}: export destination already exists")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="python-artifacts-", dir=out_dir.parent))
    scratch = None
    source_checkout = None
    try:
        scratch = promotion.add_scratch_worktree(commit, repo=repo)
        source_checkout = tempfile.TemporaryDirectory(prefix="promotion-artifact-source-")
        candidate = Path(source_checkout.name).resolve() / "inputs" / "source"
        candidate.mkdir(parents=True)
        _run(
            ["git", f"--attr-source={content_tree}", "checkout-index", "--all",
             f"--prefix={candidate}{os.sep}"], cwd=scratch,
        )
        projects = candidate_projects(candidate)
        if reuse_repository is not None:
            from reuse_promotion_artifacts import reuse_artifacts

            reused = reuse_artifacts(
                staging, repository=reuse_repository, dev_head=dev_head, main_before=main_before,
                content_tree=content_tree, candidate_root=candidate, repo=repo,
                source_policy=source_policy, min_age_days=min_age_days,
            )
            if reused is not None:
                staging.rename(out_dir)
                return reused
        argv = [
            sys.executable, str(Path(__file__).resolve()),
            "--repo", str(candidate), "--out-dir", str(staging), "--toolchain-source", source_policy,
        ]
        if min_age_days is not None:
            argv += ["--toolchain-min-age-days", str(min_age_days)]
        _run(argv, cwd=Path(__file__).resolve().parent)
        bundles_path = staging / _BUNDLES_NAME
        try:
            entries = json.loads(bundles_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PromotionArtifactError("could not read candidate bundle records") from exc
        bundles_path.unlink()
        index = {
            "schema": SCHEMA, "schema_version": 2,
            "dev_head": dev_head,
            "content_tree": content_tree, "artifacts": entries,
        }
        if reuse_repository is not None:
            signer, source, ref = (
                os.environ.get("GITHUB_WORKFLOW_SHA"), os.environ.get("GITHUB_SHA"),
                os.environ.get("GITHUB_REF"),
            )
            if (
                not isinstance(signer, str) or not _SHA.fullmatch(signer)
                or source != signer or ref != "refs/heads/main"
                or not promotion._is_ancestor(signer, main_before, cwd=repo)
            ):
                raise PromotionArtifactError("source artifact admission requires pinned trusted Main identity")
            index["admission"] = {
                "signer_digest": signer, "source_digest": source, "source_ref": ref,
            }
        (staging / INDEX_NAME).write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_artifacts(
            staging, content_tree=content_tree, dev_head=dev_head, main_before=main_before,
            projects=projects,
            candidate_root=candidate,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        staging.rename(out_dir)
        return index
    finally:
        if source_checkout is not None:
            source_checkout.cleanup()
        if scratch is not None:
            promotion.remove_scratch_worktree(scratch, repo=repo)
        if staging.exists():
            shutil.rmtree(staging)


def validate_composed_artifacts(
    directory: Path, *, scratch: Path, content_tree: str,
    dev_head: str, main_before: str,
    source_policy: str | None = None, min_age_days: int | None = None,
) -> dict:
    """Validate a composed index through its canonical checkout representation."""
    if not isinstance(content_tree, str) or not _SHA.fullmatch(content_tree):
        raise PromotionArtifactError("invalid composed source tree identity")
    actual_tree = _run(["git", "write-tree"], cwd=scratch).strip()
    if actual_tree != content_tree:
        raise PromotionArtifactError(
            f"composed source index changed (expected={content_tree}, actual={actual_tree}); "
            "recompose the candidate before validating artifacts"
        )
    with tempfile.TemporaryDirectory(prefix="promotion-artifact-validation-") as temporary:
        candidate = Path(temporary).resolve()
        _run(
            ["git", f"--attr-source={content_tree}", "checkout-index", "--all",
             f"--prefix={candidate}{os.sep}"], cwd=scratch,
        )
        return validate_artifacts(
            directory, content_tree=content_tree,
            dev_head=dev_head, main_before=main_before, projects=candidate_projects(candidate),
            candidate_root=candidate,
            source_policy=source_policy, min_age_days=min_age_days,
        )


def validate_candidate_artifacts(
    directory: Path, *, repo: Path, dev_head: str, main_before: str,
    source_policy: str | None = None, min_age_days: int | None = None,
) -> dict:
    """Recompose with trusted tools; candidate checkouts supply only source data."""
    import promote_release as promotion

    if not _SHA.fullmatch(dev_head) or not _SHA.fullmatch(main_before):
        raise PromotionArtifactError("invalid expected candidate commit identity")
    scratch = promotion.add_scratch_worktree(dev_head, repo=repo)
    try:
        state = promotion._read_pipeline_state(main_before, repo=repo)
        summary = promotion.consume_pending_changes(
            scratch, main_head=main_before, repo=repo,
            last_dev_head=(state.get("last_promotion") or {}).get("dev_head"),
            trusted_source=Path(__file__).resolve().parent.parent,
        )
        content_tree = promotion.build_promotion_tree(
            scratch, control=summary.get("control_preserved"),
        )
        return validate_composed_artifacts(
            directory, scratch=scratch, content_tree=content_tree,
            dev_head=dev_head, main_before=main_before,
            source_policy=source_policy, min_age_days=min_age_days,
        )
    except promotion.PromotionError as exc:
        raise PromotionArtifactError(f"cannot compose trusted artifact sources: {exc}") from exc
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def validate_artifacts(
    directory: Path, *, content_tree: str, dev_head: str, main_before: str,
    projects: dict[str, str] | None = None,
    candidate_root: Path | None = None,
    source_policy: str | None = None, min_age_days: int | None = None,
) -> dict:
    """Validate transfer and exact composition binding, not signer authenticity."""
    try:
        index_path = directory / INDEX_NAME
        if index_path.is_symlink():
            raise PromotionArtifactError("artifact index must not be a symlink")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or (
            index.get("schema") != SCHEMA
            or type(index.get("schema_version")) is not int or index["schema_version"] not in (1, 2)
        ):
            raise PromotionArtifactError("unsupported promotion artifact index")
        bindings = [("dev_head", dev_head), ("content_tree", content_tree)]
        if index["schema_version"] == 1:
            bindings.append(("main_before", main_before))
        elif "main_before" in index:
            raise PromotionArtifactError("source-owned artifact index must not bind a main container")
        for field, expected in bindings:
            if not isinstance(expected, str) or not _SHA.fullmatch(expected) or index.get(field) != expected:
                raise PromotionArtifactError(f"promotion artifact {field} mismatch")
        entries = index.get("artifacts")
        if not isinstance(entries, list):
            raise PromotionArtifactError("artifact entries must be a list")
        names, plugins = {INDEX_NAME}, set()
        shared_toolchain = None
        profile_manifests = {}
        for entry in entries:
            name = validate_artifact_entry(entry)
            key = project_key(entry)
            if name in names or key in plugins:
                raise PromotionArtifactError("duplicate or mismatched artifact filename")
            names.add(name)
            plugins.add(key)
            path = directory / name
            manifest, manifest_digest = validate_bundle_binding(path, entry, candidate_root=candidate_root)
            profile_manifests[entry["artifact_id"]] = manifest, manifest_digest
            toolchain = manifest["build_toolchain"]
            if shared_toolchain is not None and toolchain != shared_toolchain:
                raise PromotionArtifactError("artifact sets do not share one build-toolchain lock")
            shared_toolchain = toolchain
            provenance = toolchain["provenance"]
            if source_policy is not None and provenance["source_policy"] != source_policy:
                raise PromotionArtifactError("artifact toolchain source policy differs from required policy")
            if source_policy == "seasoned-public":
                from seasoned_public_feed import DEFAULT_MIN_AGE_DAYS
                floor = DEFAULT_MIN_AGE_DAYS if min_age_days is None else min_age_days
                if provenance["min_age_days"] != floor:
                    raise PromotionArtifactError("artifact toolchain seasoning differs from required policy")
        if "runtime_profiles" in index:
            from runtime_profile import ProfileError
            from runtime_profile_release import validate_release_bindings

            try:
                validate_release_bindings(index, profile_manifests)
            except ProfileError as exc:
                raise PromotionArtifactError(f"invalid release runtime profiles: {exc}") from exc
        if {path.name for path in directory.iterdir()} != names:
            raise PromotionArtifactError("unexpected files in promotion artifact directory")
        if projects is not None and {
            project_key(entry): entry["version"] for entry in entries
        } != projects:
            raise PromotionArtifactError("artifact project set differs from promotion candidate")
        return index
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionArtifactError(f"cannot read promotion artifacts: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", choices=("build", "validate"), default="build")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--dev-head")
    parser.add_argument("--main-before")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--toolchain-source", choices=("governed", "seasoned-public"), default="governed",
    )
    parser.add_argument("--toolchain-min-age-days", type=int, default=None)
    args = parser.parse_args(argv)
    if args.operation == "validate":
        if not args.dev_head or not args.main_before:
            parser.error("validate requires --dev-head and --main-before")
        try:
            index_path = args.out_dir / INDEX_NAME
            if index_path.is_symlink():
                raise PromotionArtifactError("artifact index must not be a symlink")
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(index, dict):
                raise PromotionArtifactError("invalid artifact index")
            validate_candidate_artifacts(
                args.out_dir, repo=args.repo, dev_head=args.dev_head, main_before=args.main_before,
                source_policy=args.toolchain_source, min_age_days=args.toolchain_min_age_days,
            )
        except (PromotionArtifactError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
            return 1
        return 0
    from build_toolchain_lock import ArtifactBuildError
    try:
        build_candidate_bundles(
            args.repo, args.out_dir,
            source_policy=args.toolchain_source, min_age_days=args.toolchain_min_age_days,
        )
    except (PromotionArtifactError, ArtifactBuildError) as exc:
        print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
