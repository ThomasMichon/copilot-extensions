"""First-party wheel bundles built from a promotion's composed candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

SCHEMA = "copilot-extensions.promotion-python-artifacts"
INDEX_NAME = "python-artifacts.json"
_BUNDLES_NAME = "wheel-bundles.json"
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLUGIN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[0-9][A-Za-z0-9.+-]*")


class PromotionArtifactError(RuntimeError):
    pass


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


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def candidate_projects(root: Path) -> dict[str, str]:
    projects = {}
    for project in sorted((root / "plugins").glob("*/pyproject.toml")):
        try:
            data = tomllib.loads(project.read_text(encoding="utf-8"))
            projects[project.parent.name] = data["project"]["version"]
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            raise PromotionArtifactError(f"{project}: invalid Python project") from exc
    return projects


def bundle_wheels(manifest: dict, wheel_dir: Path, out_dir: Path) -> dict:
    """Bundle only the builder's declared first-party wheels and manifest."""
    plugin, version = manifest["plugin"], manifest["version"]
    artifact_id = manifest["artifact_id"]
    if (
        not isinstance(plugin, str) or not _PLUGIN.fullmatch(plugin)
        or not isinstance(version, str) or not _VERSION.fullmatch(version)
        or not isinstance(artifact_id, str)
        or not artifact_id.startswith("sha256:")
        or not _DIGEST.fullmatch(artifact_id[7:])
    ):
        raise PromotionArtifactError("invalid artifact bundle identity")
    filename = f"{plugin}-{version}-{artifact_id[7:]}.zip"
    destination = out_dir / filename
    names = {"manifest.json"}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True) + "\n")
        for wheel in manifest["wheels"]:
            name = wheel["filename"]
            if (
                wheel["role"] not in ("plugin", "vendored-lib")
                or not isinstance(name, str) or Path(name).name != name
                or "/" in name or "\\" in name or not name.endswith(".whl")
                or name in names
            ):
                raise PromotionArtifactError("invalid or duplicate first-party wheel entry")
            names.add(name)
            path = wheel_dir / name
            if path.is_symlink() or _digest(path) != wheel["sha256"]:
                raise PromotionArtifactError(f"{name}: wheel digest changed before bundling")
            bundle.write(path, name)
    return {
        "plugin": plugin, "version": version, "artifact_id": artifact_id,
        "filename": filename, "sha256": _digest(destination),
    }


def build_candidate_bundles(
    root: Path, out_dir: Path, *, source_policy: str, min_age_days: int | None,
) -> None:
    """Runs inside the disposable candidate: resolve exactly one shared lock."""
    from build_python_artifacts import build_plugin_artifacts
    from build_toolchain_lock import resolve_toolchain_lock

    if any(out_dir.iterdir()):
        raise PromotionArtifactError("candidate bundle directory must be empty")
    projects = candidate_projects(root)
    entries = []
    with tempfile.TemporaryDirectory(prefix="promotion-build-toolchain-") as temporary:
        toolchain = resolve_toolchain_lock(
            Path(temporary) / "venv", python=sys.executable,
            source_policy=source_policy, min_age_days=min_age_days,
        )
        for plugin, version in projects.items():
            wheel_dir = out_dir / plugin
            manifest = build_plugin_artifacts(plugin, out_dir=wheel_dir, toolchain=toolchain)
            if manifest["plugin"] != plugin or manifest["version"] != version:
                raise PromotionArtifactError(f"{plugin}: builder returned another project identity")
            entries.append(bundle_wheels(manifest, wheel_dir, out_dir))
            shutil.rmtree(wheel_dir)
    (out_dir / _BUNDLES_NAME).write_text(json.dumps(entries), encoding="utf-8")


def export_artifacts(
    *, repo: Path, commit: str, content_tree: str, dev_head: str, main_before: str,
    out_dir: Path, source_policy: str = "governed", min_age_days: int | None = None,
) -> dict:
    """Build in a disposable candidate checkout; never mutate the promoted tree."""
    import promote_release as promotion

    if out_dir.exists():
        raise PromotionArtifactError(f"{out_dir}: export destination already exists")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="python-artifacts-", dir=out_dir.parent))
    scratch = None
    try:
        scratch = promotion.add_scratch_worktree(commit, repo=repo)
        projects = candidate_projects(scratch)
        argv = [
            sys.executable, str(scratch / "tools" / "promotion_python_artifacts.py"),
            "--out-dir", str(staging), "--toolchain-source", source_policy,
        ]
        if min_age_days is not None:
            argv += ["--toolchain-min-age-days", str(min_age_days)]
        _run(argv, cwd=scratch)
        bundles_path = staging / _BUNDLES_NAME
        try:
            entries = json.loads(bundles_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PromotionArtifactError("could not read candidate bundle records") from exc
        bundles_path.unlink()
        index = {
            "schema": SCHEMA, "schema_version": 1,
            "dev_head": dev_head, "main_before": main_before,
            "content_tree": content_tree, "artifacts": entries,
        }
        (staging / INDEX_NAME).write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validate_artifacts(
            staging, content_tree=content_tree, dev_head=dev_head, main_before=main_before,
            projects=projects,
        )
        staging.rename(out_dir)
        return index
    finally:
        if scratch is not None:
            promotion.remove_scratch_worktree(scratch, repo=repo)
        if staging.exists():
            shutil.rmtree(staging)


def validate_artifacts(
    directory: Path, *, content_tree: str, dev_head: str, main_before: str,
    projects: dict[str, str] | None = None,
) -> dict:
    """Validate transfer and exact composition binding, not signer authenticity."""
    try:
        index_path = directory / INDEX_NAME
        if index_path.is_symlink():
            raise PromotionArtifactError("artifact index must not be a symlink")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict) or (
            index.get("schema") != SCHEMA
            or type(index.get("schema_version")) is not int or index["schema_version"] != 1
        ):
            raise PromotionArtifactError("unsupported promotion artifact index")
        for field, expected in (
            ("dev_head", dev_head), ("main_before", main_before),
            ("content_tree", content_tree),
        ):
            if not _SHA.fullmatch(expected) or index.get(field) != expected:
                raise PromotionArtifactError(f"promotion artifact {field} mismatch")
        entries = index.get("artifacts")
        if not isinstance(entries, list):
            raise PromotionArtifactError("artifact entries must be a list")
        names, plugins = {INDEX_NAME}, set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise PromotionArtifactError("invalid artifact entry")
            plugin, version = entry.get("plugin"), entry.get("version")
            identity, digest = entry.get("artifact_id"), entry.get("sha256")
            if (
                not isinstance(plugin, str) or not _PLUGIN.fullmatch(plugin)
                or not isinstance(version, str) or not _VERSION.fullmatch(version)
                or not isinstance(identity, str) or not identity.startswith("sha256:")
                or not _DIGEST.fullmatch(identity[7:])
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
            ):
                raise PromotionArtifactError("invalid artifact entry identity")
            name = f"{plugin}-{version}-{identity[7:]}.zip"
            if entry.get("filename") != name or name in names or plugin in plugins:
                raise PromotionArtifactError("duplicate or mismatched artifact filename")
            names.add(name)
            plugins.add(plugin)
            path = directory / name
            if path.is_symlink() or not path.is_file() or _digest(path) != digest:
                raise PromotionArtifactError(f"{name}: artifact digest mismatch")
        if {path.name for path in directory.iterdir()} != names:
            raise PromotionArtifactError("unexpected files in promotion artifact directory")
        if projects is not None and {
            entry["plugin"]: entry["version"] for entry in entries
        } != projects:
            raise PromotionArtifactError("artifact project set differs from promotion candidate")
        return index
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionArtifactError(f"cannot read promotion artifacts: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--toolchain-source", choices=("governed", "seasoned-public"), default="governed",
    )
    parser.add_argument("--toolchain-min-age-days", type=int, default=None)
    args = parser.parse_args(argv)
    from build_toolchain_lock import ArtifactBuildError

    try:
        build_candidate_bundles(
            Path(__file__).resolve().parent.parent, args.out_dir,
            source_policy=args.toolchain_source, min_age_days=args.toolchain_min_age_days,
        )
    except (PromotionArtifactError, ArtifactBuildError) as exc:
        print(f"promotion-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
