"""Prepare an unapproved native delivery from an authenticated core release."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))

import core_native_delivery as delivery
import prepare_runtime_source as source
import promote_release as promotion
from artifact_attestation import INDEX_LIMIT
from core_release_inputs import retain_released_core
from core_runtime_profile import validate_core_source_index
from installer_engine_process import ChildLaunchError, ChildTimeoutExpired, run_owned
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_wheel_snapshot import _digest, _open, _unchanged
from runtime_profile import ProfileError
from runtime_profile_delivery import _read_json
from runtime_profile_release import _unique_object

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CONTENT_TREE_PROBE = (
    "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
    "import promote_release as p; "
    "print(p._tree_excluding_state(sys.argv[3], repo=Path(sys.argv[2])))"
)


def _git(repository: Path, *arguments: str, deadline: float) -> str:
    try:
        return source._git(repository, *arguments, deadline=deadline)
    except subprocess.TimeoutExpired:
        raise ProfileError("source Git verification timed out") from None


def _content_tree(repository: Path, tree: str, *, deadline: float) -> str:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GIT_") and key.upper() not in ("GH_TOKEN", "GITHUB_TOKEN")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0",
        GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
    )
    command = [
        sys.executable, "-I", "-c", _CONTENT_TREE_PROBE, str(ROOT / "tools"), str(repository), tree,
    ]
    _check_deadline(deadline)
    try:
        result = run_owned(
            command, cwd=repository, env=environment,
            timeout=source._remaining(deadline), deadline=deadline,
            procutil=delivery._producer_procutil(), construction_fd=None,
        )
    except (ChildLaunchError, ChildTimeoutExpired, subprocess.CalledProcessError) as error:
        raise ProfileError("could not derive the selected source content tree") from error
    content_tree = result.stdout.strip()
    if _COMMIT.fullmatch(content_tree) is None:
        raise ProfileError("promotion owner returned an invalid source content tree")
    _check_deadline(deadline)
    return content_tree


def _selected_source(repository: Path, commit: str, main_head: str, *, deadline: float) -> tuple[str, str]:
    for label, identity in (("source", commit), ("Main", main_head)):
        if not isinstance(identity, str) or _COMMIT.fullmatch(identity) is None:
            raise ProfileError(f"{label} selection requires a full Git commit")
        if _git(repository, "rev-parse", f"{identity}^{{commit}}", deadline=deadline) != identity:
            raise ProfileError(f"{label} selection does not name the requested Git commit")
    _git(repository, "merge-base", "--is-ancestor", commit, main_head, deadline=deadline)
    selected_state = f"{commit}:{promotion.PIPELINE_STATE_PATH}"
    size = _git(repository, "cat-file", "-s", selected_state, deadline=deadline)
    if not size.isdecimal() or int(size) > INDEX_LIMIT:
        raise ProfileError("selected source release state exceeds its byte limit")
    raw = _git(repository, "show", selected_state, deadline=deadline)
    state = json.loads(raw, object_pairs_hook=_unique_object)
    last = state.get("last_promotion") if isinstance(state, dict) else None
    dev = last.get("dev_head") if isinstance(last, dict) else None
    validate_core_source_index({"dev_head": dev}, state)
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}", deadline=deadline)
    return dev, _content_tree(repository, tree, deadline=deadline)


def prepare_release_delivery(
    *, repository: str, source_checkout: Path, source_commit: str, main_head: str,
    materials: dict, material_paths: dict, destination: Path, uv: str,
    download_origins: tuple[str, ...], deadline: float, max_bytes: int, max_wheels: int = 256,
    native_lock: Path | None = None, offline_wheel_pool: Path | None = None,
    guarded_feed: bool = False, nonsecret_feed_url: str | None = None,
) -> dict:
    """Stage a candidate until all authenticated-release custody checks have finished."""
    _check_deadline(deadline)
    if type(max_bytes) is not int or max_bytes <= 0 or type(max_wheels) is not int or max_wheels <= 0:
        raise ProfileError("release delivery limits must be explicit positive integers")
    if type(guarded_feed) is not bool or sum(
        (native_lock is not None, offline_wheel_pool is not None, guarded_feed),
    ) != 1:
        raise ProfileError("release delivery requires exactly one native selection mode")
    if nonsecret_feed_url is not None and (not guarded_feed or download_origins):
        raise ProfileError("public feed identity requires delegated guarded-feed selection")
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination):
        raise FileExistsError("release delivery output already exists")
    checkout = Path(source_checkout).resolve(strict=True)
    dev, content_tree = _selected_source(checkout, source_commit, main_head, deadline=deadline)

    def is_ancestor(ancestor: str, descendant: str) -> bool:
        try:
            _git(checkout, "merge-base", "--is-ancestor", ancestor, descendant, deadline=deadline)
            return True
        except source.SourcePreparationError:
            return False

    with delivery.native.dependencies._temporary_inputs(destination.parent) as temporary:
        stage = Path(delivery.source_owner.native_retained_path(
            delivery.native.dependencies._private_retained_root(Path(temporary)).resolve() / "delivery.zip",
        ))
        with retain_released_core(
            repository=repository, dev_head=dev, main_head=main_head,
            content_tree=content_tree, is_ancestor=is_ancestor, deadline=deadline,
            max_bytes=max_bytes, max_wheels=max_wheels,
        ) as core:
            result = delivery.prepare_core_delivery(
                checkout, source_commit, main_head, core.manifest_path, core.wheel_dir,
                materials, material_paths, stage, uv=uv, download_origins=download_origins,
                deadline=deadline, max_bytes=max_bytes, native_lock=native_lock,
                offline_wheel_pool=offline_wheel_pool, guarded_feed=guarded_feed,
                artifact_index=core.index_path, nonsecret_feed_url=nonsecret_feed_url,
            )
        before = _ordinary(stage)
        with _open(stage) as stream:
            opened = _unchanged(stage, before, stream)
            if _digest(stream, max_bytes, deadline) != (result["sha256"], result["size"]):
                raise ProfileError("staged release delivery changed after producer readback")
            _unchanged(stage, before, stream, opened=opened)
            _check_deadline(deadline)
            os.link(stage, destination)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repository", required=True, help="first-party GitHub owner/repository")
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--main-head", required=True)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--material-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--native-lock", type=Path)
    selection.add_argument("--offline-wheel-pool", type=Path)
    selection.add_argument("--guarded-feed", action="store_true")
    parser.add_argument("--download-origin", action="append", default=[])
    parser.add_argument(
        "--nonsecret-feed-url",
        help="opt in to reproducible identity for this explicitly nonsecret anonymous HTTPS feed",
    )
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-bytes", type=int, default=512 << 20)
    parser.add_argument("--max-wheels", type=int, default=256)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    try:
        materials = _read_json(args.materials, deadline)
        if not isinstance(materials, dict) or any(not isinstance(name, str) for name in materials):
            raise ProfileError("release delivery materials must be an object with named entries")
        result = prepare_release_delivery(
            repository=args.repository, source_checkout=args.source_checkout,
            source_commit=args.source_commit, main_head=args.main_head,
            materials=materials, material_paths={name: args.material_dir / name for name in materials},
            destination=args.output, uv=args.uv, download_origins=tuple(args.download_origin),
            deadline=deadline, max_bytes=args.max_bytes, max_wheels=args.max_wheels,
            native_lock=args.native_lock, offline_wheel_pool=args.offline_wheel_pool,
            guarded_feed=args.guarded_feed, nonsecret_feed_url=args.nonsecret_feed_url,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except subprocess.TimeoutExpired:
        parser.exit(1, "release delivery refused: a required operation timed out\n")
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"release delivery refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
