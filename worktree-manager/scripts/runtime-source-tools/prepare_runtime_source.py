#!/usr/bin/env python3
"""Prepare a self-contained runtime payload from a clean, merged Git commit."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import materialize_main as materializer

_TOOL_DIRECTORY = Path(__file__).resolve().parent
_RUNTIME_LIBRARY = (
    _TOOL_DIRECTORY.parent
    if (_TOOL_DIRECTORY.parent / "versioned_runtime.py").is_file()
    else _TOOL_DIRECTORY.parent / "libs" / "versioned-runtime"
)
sys.path.insert(0, str(_RUNTIME_LIBRARY))
from versioned_runtime import _AV_RETRY_ATTEMPTS, _AV_RETRY_BACKOFF, _is_transient_lock, construction_lease
from versioned_runtime_source import (
    SOURCE_FILE,
    SourceIdentity,
    SourceIdentityError,
    assembly_hash as _assembly_hash,
    payload_package_version,
    read_source_identity,
    runtime_version,
)

_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_PLUGIN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class SourcePreparationError(RuntimeError):
    """Source admission or materialization refused the selected input."""


@dataclass(frozen=True)
class PreparedSource:
    payload: Path
    identity: SourceIdentity
    reused: bool


def _remaining(deadline: float | None, cap: float = 120) -> float:
    if deadline is None:
        return cap
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise SourcePreparationError("Source preparation requires a finite deadline")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourcePreparationError("Source preparation deadline exceeded")
    return min(cap, remaining)


def _rename_prepared(payload: Path, destination: Path, *, deadline: float | None) -> None:
    for attempt in range(_AV_RETRY_ATTEMPTS):
        _remaining(deadline)
        try:
            payload.rename(destination)
            return
        except OSError as error:
            if os.name != "nt" or not _is_transient_lock(error) or attempt == _AV_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(min(_remaining(deadline), _AV_RETRY_BACKOFF * (attempt + 1)))


def _git(source: Path, *arguments: str, deadline: float | None = None) -> str:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
         "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "-C", str(source), *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=_remaining(deadline), env=environment, check=False, creationflags=creationflags,
    )
    _remaining(deadline)
    if result.returncode:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise SourcePreparationError(f"Source Git probe failed: {detail}")
    return result.stdout.strip()


def _admit(source: Path, commit: str, branch: str, *, deadline: float | None = None) -> tuple[str, str]:
    _remaining(deadline)
    git_command = _git if deadline is None else partial(_git, deadline=deadline)
    if branch not in ("dev", "main") or not _OBJECT_ID.fullmatch(commit):
        raise SourcePreparationError("Select a full commit from the fetched dev or main branch")
    git_dir = source / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink() or (
        getattr(git_dir.lstat(), "st_file_attributes", 0) & 0x400
    ):
        raise SourcePreparationError("Runtime source must be a standalone checkout, not a worktree")
    if Path(git_command(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise SourcePreparationError("Runtime source must name the exact checkout root")
    head = git_command(source, "rev-parse", "--verify", "HEAD")
    resolved = git_command(source, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved != commit:
        raise SourcePreparationError("Selected source does not resolve to the exact requested commit")
    merged_tip = git_command(source, "rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}")
    if git_command(source, "merge-base", commit, merged_tip) != commit:
        raise SourcePreparationError("Selected commit is not merged into the fetched source branch")
    if git_command(source, "status", "--porcelain=v1", "--untracked-files=all", "--ignored"):
        raise SourcePreparationError("Runtime source contains modified, untracked or ignored files")
    if git_command(source, "rev-parse", "--verify", "HEAD") != head:
        raise SourcePreparationError("Source checkout moved during admission")
    return head, git_command(source, "rev-parse", "--verify", f"{commit}^{{tree}}")


def _copy(source, destination, *, deadline: float | None = None) -> None:
    while True:
        _remaining(deadline)
        block = source.read(1 << 20)
        if not block:
            return
        destination.write(block)


def _extract_archive(archive: Path, destination: Path, *, deadline: float | None = None) -> None:
    with tarfile.open(archive, "r:") as source:
        for member in source:
            _remaining(deadline)
            name = PurePosixPath(member.name)
            if (
                name.is_absolute() or ".." in name.parts or not name.parts
                or "\\" in member.name or ":" in member.name
            ):
                raise SourcePreparationError("Source archive contains an unsafe path")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                content = source.extractfile(member)
                if content is None:
                    raise SourcePreparationError("Source archive file has no readable content")
                with content, target.open("xb") as output:
                    _copy(content, output, deadline=deadline)
                target.chmod(member.mode & 0o777)
            else:
                raise SourcePreparationError("Source archive links and special files are unsupported")


def _materialize(
    snapshot: Path, plugin: str, destination: Path, *, deadline: float | None = None,
) -> None:
    _remaining(deadline)
    source = snapshot / plugin if plugin == "worktree-manager" else snapshot / "plugins" / plugin
    if not (source / "pyproject.toml").is_file():
        raise SourcePreparationError(f"No Python runtime consumer at {source.relative_to(snapshot)}")
    def copy_file(source, destination):
        with open(source, "rb") as incoming, open(destination, "wb") as output:
            _copy(incoming, output, deadline=deadline)
        shutil.copystat(source, destination)
        return destination

    shutil.copytree(source, destination, copy_function=copy_file if deadline is not None else shutil.copy2)
    for metadata in (SOURCE_FILE, "snapshot-provenance.json"):
        if (destination / metadata).exists() or (destination / metadata).is_symlink():
            raise SourcePreparationError("Source payload already contains reserved deployment metadata")
    retired = materializer.find_retired_directory_pointers_in_plugin(destination)
    if retired:
        raise SourcePreparationError("Source payload contains retired directory vendor pointers")
    log = materializer.materialize_file_pointers(destination, canonical_root=snapshot)
    log.extend(materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination, canonical_root=snapshot,
    ))
    log.extend(materializer.materialize_installer_engine_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination, canonical_root=snapshot,
    ))
    log.extend(materializer.materialize_launch_wrapper_assets_into(
        source_consumer_dir=source, dest_consumer_dir=destination, canonical_root=snapshot,
        reuse_existing=True,
    ))
    refusals = [entry for entry in log if entry.startswith("SKIP ")]
    _remaining(deadline)
    if refusals:
        raise SourcePreparationError("Source materialization refused:\n" + "\n".join(refusals))


def prepare(
    source: Path, commit: str, plugin: str, output: Path, *, branch: str = "dev",
    deadline: float | None = None,
) -> PreparedSource:
    _remaining(deadline)
    git_command = _git if deadline is None else partial(_git, deadline=deadline)
    source = source.resolve(strict=True)
    output = output.resolve()
    if not _PLUGIN.fullmatch(plugin):
        raise SourcePreparationError("Plugin must be one portable plugin identifier")
    publication_root = (output / plugin).resolve()
    if output == source or source in output.parents or (
        publication_root == source or source in publication_root.parents
    ):
        raise SourcePreparationError("Source preparation output must be outside the checkout")
    head, tree = _admit(source, commit, branch, deadline=deadline)
    output.mkdir(parents=True, exist_ok=True)
    prefix = f".prepare-{plugin}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-"
    with tempfile.TemporaryDirectory(prefix=prefix, dir=output) as temporary:
        work = Path(temporary)
        archive = work / "source.tar"
        git_command(
            source, "-c", "tar.umask=0022", "archive", "--format=tar",
            "--output", str(archive), commit,
        )
        snapshot = work / "source"
        snapshot.mkdir()
        _extract_archive(archive, snapshot, deadline=deadline)
        payload = work / plugin
        _materialize(snapshot, plugin, payload, deadline=deadline)
        if git_command(source, "rev-parse", "--verify", "HEAD") != head or git_command(
            source, "status", "--porcelain=v1", "--untracked-files=all", "--ignored",
        ):
            raise SourcePreparationError("Source checkout changed while preparing the selected commit")
        try:
            version = payload_package_version(payload, plugin)
        except SourceIdentityError as error:
            raise SourcePreparationError(str(error)) from error
        digest = _assembly_hash(payload)
        identity = SourceIdentity(
            plugin, version, commit, tree, digest, runtime_version(version, commit, digest),
        )
        (payload / SOURCE_FILE).write_text(
            json.dumps(identity.document(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
        read_source_identity(payload, version, plugin=plugin)
        destination = output / plugin / identity.runtime_version
        destination.parent.mkdir(parents=True, exist_ok=True)
        with construction_lease(
            destination.parent, identity.runtime_version, timeout=_remaining(deadline, 60),
        ):
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink():
                    raise SourcePreparationError("Prepared runtime generation must not be linked")
                try:
                    existing = read_source_identity(destination, version, plugin=plugin)
                except SourceIdentityError as error:
                    raise SourcePreparationError(
                        f"Existing prepared generation conflicts with selected inputs: {error}"
                    ) from error
                if existing != identity:
                    raise SourcePreparationError(
                        "Existing prepared generation conflicts with selected inputs"
                    )
                return PreparedSource(destination, identity, reused=True)
            _rename_prepared(payload, destination, deadline=deadline)
            return PreparedSource(destination, identity, reused=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--plugin", required=True, help="Runtime plugin identifier or worktree-manager")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--branch", choices=("dev", "main"), default="dev")
    args = parser.parse_args(argv)
    try:
        prepared = prepare(args.source, args.commit, args.plugin, args.output, branch=args.branch)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"prepare-runtime-source: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "payload": str(prepared.payload),
        "identity": prepared.identity.document(),
        "reused": prepared.reused,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
