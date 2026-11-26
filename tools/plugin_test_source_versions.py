"""Version-bearing inputs within the existing leased test environment."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import shutil
import time
import sys
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from promotion_source_archive import git_text, git_timeout_options, is_ancestor
from promotion_versions import prepare_versions
from cache_directory_paths import ordinary_cache_root


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ordinary(path: Path, root: Path) -> None:
    if ".." in path.parts or not path.absolute().is_relative_to(root.absolute()):
        raise ValueError(f"source version input escapes repository: {path}")
    for parent in (path, *path.parents):
        if parent.is_symlink() or (
            parent.exists() and getattr(parent.lstat(), "st_reparse_tag", 0) != 0
        ):
            raise ValueError(f"source version input is indirect: {parent}")
        if parent.exists() and not (
            stat.S_ISREG(parent.stat().st_mode) or stat.S_ISDIR(parent.stat().st_mode)
        ):
            raise ValueError(f"source version input is not ordinary: {parent}")
        if parent == root:
            break


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"version owner is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version_inputs(
    repo: Path, overrides: dict[str, Path], constraints: tuple[str, ...],
    files: tuple[Path, ...], *, selected: dict[str, Path] | None = None,
) -> dict[str, Any] | None:
    """Keep already-satisfied source composition unchanged."""
    from packaging.requirements import Requirement
    from packaging.markers import default_environment
    versions = {
        name: tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        for name, path in (selected if selected is not None else overrides).items()
    }
    active = [Requirement(value) for value in constraints]
    if all(
        (requirement.marker is not None and not requirement.marker.evaluate())
        or requirement.specifier.contains(versions[requirement.name], prereleases=True)
        for requirement in active
    ):
        return None
    head = git_text(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    git_dir = Path(git_text(repo, ["rev-parse", "--absolute-git-dir"]))
    source_index = git_dir / "test-source-index"
    index_file = None
    if source_index.exists():
        _ordinary(source_index, git_dir)
        index_file = source_index
    tree = git_text(repo, ["write-tree"], index_file=index_file)
    main = git_text(repo, ["rev-parse", "--verify", "refs/ce-test/pinned-main^{commit}"], check=False)
    if not main:
        main = git_text(repo, ["rev-parse", "--verify", "origin/main^{commit}"])
    if git_text(repo, ["diff", "--name-only"], index_file=index_file):
        raise ValueError("version preparation requires staged, pinned source inputs")
    state = json.loads(git_text(repo, ["show", f"{main}:.github/release-pipeline-state.json"]))
    last = state["last_promotion"]["dev_head"]
    if not isinstance(last, str) or re.fullmatch(r"[0-9a-f]{40,64}", last) is None:
        raise ValueError("Main's consumed Dev boundary is not an immutable object identity")
    git_text(repo, ["rev-parse", "--verify", f"{last}^{{commit}}"])
    if not is_ancestor(repo, last, head):
        raise ValueError("Main's consumed Dev boundary is not in the candidate history")
    inputs = set(files)
    inputs.update((repo / ".changefiles").glob("*.json"))
    inputs.update(repo / "tools" / name for name in (
        "plugin_test_source_versions.py", "plugin_test_cache.py", "promotion_versions.py",
        "accumulate_bumps.py", "changefile.py", "uv_editable_ref.py",
        "promotion_source_archive.py", "launch_wrapper_assets_ref.py",
    ))
    indexed = set(git_text(
        repo, ["ls-files", "-z"], index_file=index_file, errors="surrogateescape",
    ).split("\0"))
    for path in inputs:
        _ordinary(path, repo)
        if path.relative_to(repo).as_posix() not in indexed:
            raise ValueError(f"source version input must be staged and pinned: {path}")
    return {
        "candidate_commit": head, "candidate_tree": tree, "main": main,
        "last_dev_head": last,
        "target_environment": default_environment(),
        "sources": {str(path.relative_to(repo)): _digest(path) for path in sorted(inputs)},
        "original_versions": versions,
        "constraints": list(constraints),
        "canonical": {name: path.relative_to(repo).as_posix() for name, path in overrides.items()},
        "selected_sources": {
            name: path.relative_to(repo).as_posix()
            for name, path in (selected if selected is not None else overrides).items()
        },
    }


def validate_prepared(venv: Path, record: dict[str, Any], *, deadline: float) -> None:
    from installer_engine import _source_files
    venv = ordinary_cache_root(venv)
    root = venv / ".source-inputs"
    _ordinary(root, venv)
    if {str(path.relative_to(root)) for path in _source_files((root,))} != set(record["prepared_hashes"]):
        raise ValueError("prepared source version input set changed")
    for relative, expected in record["prepared_hashes"].items():
        if time.monotonic() >= deadline:
            raise TimeoutError("source version preparation exhausted the existing deadline")
        path = root / relative
        _ordinary(path, root)
        if _digest(path) != expected:
            raise ValueError("prepared source version input changed")
    for name, relative in record["inputs"]["canonical"].items():
        manifest = root / relative / "pyproject.toml"
        _ordinary(manifest, root)
        actual = tomllib.loads(manifest.read_text(encoding="utf-8"))
        if actual["project"]["version"] != record["generated_versions"][name]:
            raise ValueError("prepared source version metadata differs from its record")


def _pending_write(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".update")
    _ordinary(temporary, path.parent)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True)
    temporary.replace(path)


def bind_preparation_owner(venv: Path, private: str) -> None:
    pending = ordinary_cache_root(venv) / ".dep-fingerprint.pending"
    state = json.loads(pending.read_text(encoding="utf-8"))
    if state["phase"] != "preparing" or state["owner"]["private_state"] is not None:
        raise ValueError("preparation owner cannot replace an existing binding")
    state["owner"]["private_state"] = private
    _pending_write(pending, state)


@contextmanager
def preparation_ready(venv: Path) -> Iterator[dict[str, bool]]:
    venv = ordinary_cache_root(venv)
    pending = venv / ".dep-fingerprint.pending"
    if pending.exists() or pending.with_name(pending.name + ".update").exists():
        raise ValueError(
            f"unsettled source preparation at {pending}; retain the environment "
            "for its existing owner's cleanup reconciliation before retrying"
        )
    venv.mkdir(parents=True, exist_ok=True)
    state = {"phase": "preparing", "attempt": uuid.uuid4().hex, "owner": {
        "pid": os.getpid(), "private_state": None, "leased_environment": str(venv),
    }}
    _ordinary(pending, venv)
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True)
    cleanup = {"confirmed": False}
    try:
        yield cleanup
        git_timeout_options()
        if not cleanup["confirmed"]:
            raise ValueError("preparation cleanup is unconfirmed; original owner retained")
        _ordinary(pending, venv)
        completed = json.loads(pending.read_text(encoding="utf-8"))
        if completed["phase"] != "complete" or not isinstance(completed["payload"], str):
            raise ValueError("preparation did not produce a completed readiness record")
        temporary = pending.with_name(pending.name + ".update")
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(completed["payload"])
        temporary.replace(venv / ".dep-fingerprint")
        pending.unlink()
    except BaseException as error:
        try:
            (venv / ".dep-fingerprint").unlink(missing_ok=True)
            if cleanup["confirmed"]:
                pending.unlink(missing_ok=True)
                pending.with_name(pending.name + ".update").unlink(missing_ok=True)
        except OSError as invalidation:
            raise RuntimeError(f"source readiness invalidation failed: {invalidation}") from error
        raise


def stage_ready(venv: Path, payload: str) -> None:
    venv = ordinary_cache_root(venv)
    pending = venv / ".dep-fingerprint.pending"
    _ordinary(pending, venv)
    state = json.loads(pending.read_text(encoding="utf-8"))
    if state["phase"] != "preparing" or state["owner"]["private_state"] is None:
        raise ValueError("readiness requires the active preparation owner")
    state.update(phase="complete", payload=payload)
    _pending_write(pending, state)


def prepare_sources(
    repo: Path, plugin: Path, venv: Path, inputs: dict[str, Any] | None,
    files: tuple[Path, ...], *, deadline: float,
) -> tuple[Path, Path, dict[str, Any] | None]:
    if inputs is None:
        return repo, plugin, None
    venv = ordinary_cache_root(venv)
    root = venv / ".source-inputs"
    if root.exists():
        raise ValueError("partial source preparation cannot be reused")

    def check_time() -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("source version preparation exhausted the existing deadline")

    check_time()
    root.mkdir()
    copied: set[Path] = set()
    for source in (*files, *(repo / ".changefiles").glob("*.json")):
        check_time()
        _ordinary(source, repo)
        relative = source.relative_to(repo)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if _digest(source) != _digest(destination):
            raise ValueError("source changed while preparing versions")
        copied.add(relative)
    acc = _load(repo / "tools/accumulate_bumps.py", "test_source_accumulator")
    acc.REPO, acc.PLUGINS_DIR, acc.MARKETPLACE = root, root / "plugins", root / ".github/plugin/marketplace.json"
    changefiles = _load(repo / "tools/changefile.py", "test_source_changefiles")
    changefiles.REPO, changefiles.CHANGEFILES_DIR = root, root / ".changefiles"
    entries = list(changefiles.read_changefiles())
    consumers = frozenset(inputs["canonical"].values())
    if any(not consumer.replace("\\", "/").startswith("libs/") for consumer in consumers):
        raise ValueError("source version preparation accepts canonical libraries only")
    entries = [
        (path, data) for path, data in entries
        if any(change["plugin"] in consumers for change in data.get("changes", []))
    ]
    original = {relative: _digest(root / relative) for relative in copied}
    computed, applied, pending = prepare_versions(
        root, inputs["main"], acc=acc, repo=repo, entries=entries,
        last_dev_head=inputs["last_dev_head"], consumers=consumers,
    )
    check_time()
    allowed = {
        path.relative_to(root) / "pyproject.toml"
        for consumer in consumers for path in acc.library_roots(consumer)
    }
    for relative, digest in original.items():
        check_time()
        if relative not in allowed and _digest(root / relative) != digest:
            raise ValueError("version owner changed a source body or unrelated input")
        if relative in allowed:
            before = tomllib.loads((repo / relative).read_text(encoding="utf-8"))
            after = tomllib.loads((root / relative).read_text(encoding="utf-8"))
            before["project"].pop("version")
            after["project"].pop("version")
            if before != after:
                raise ValueError("version preparation changed dependency metadata")
    for relative, digest in inputs["sources"].items():
        check_time()
        if _digest(repo / relative) != digest:
            raise ValueError("pinned version preparation inputs changed")
    record = {
        "inputs": inputs, "computed": computed, "applied": applied,
        "applicable_changefiles": [path.name for path, _ in pending],
        "generated_versions": {
            name: tomllib.loads((root / path / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
            for name, path in inputs["canonical"].items()
        },
        "prepared_hashes": {str(path): _digest(root / path) for path in sorted(copied)},
        "identity": "unpublished-test-source-candidate",
    }
    validate_prepared(venv, record, deadline=deadline)
    return root, root / plugin.relative_to(repo), record
