"""Discover and register the first harness before selecting a Manager release."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

from . import harness_state, registered_suite, runtime_source_repository, source_config

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "submodule.recurse=false",
         "-c", "protocol.ext.allow=never", "-C", str(directory), *args],
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, check=True,
        timeout=180, stdout=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout.strip()


def _github(value: str) -> tuple[str, str]:
    parts = value.removeprefix("https://github.com/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(_NAME.fullmatch(part) for part in parts):
        raise ValueError("Expected a GitHub owner/repository, HTTPS URL, or existing checkout path")
    return parts[1], "https://github.com/" + "/".join(parts)


def repository_remote(value: str) -> tuple[str, str]:
    """GitHub shorthand or a provider-qualified Azure Repos HTTPS clone URL."""
    if "://" not in value or value.startswith("https://github.com/"):
        return _github(value)
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    parts = parsed.path.lstrip("/").split("/")
    azure = ((host == "dev.azure.com" and len(parts) == 4) or
             (re.fullmatch(r"[a-z0-9-]+\.visualstudio\.com", host) and len(parts) == 3))
    if (parsed.scheme != "https" or parsed.netloc != host or parsed.query or parsed.fragment
            or not azure or parts[-2] != "_git"):
        raise ValueError("Expected a GitHub or Azure Repos HTTPS clone URL; use a local checkout for other hosts")
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", unquote(p)) for p in (*parts[:-2], parts[-1])):
        raise ValueError("Invalid Azure Repos path")
    name = unquote(parts[-1]).removesuffix(".git")
    if not _NAME.fullmatch(name):
        raise ValueError("Repository name is not a supported registration label")
    return name, value


def _anchor(path: Path) -> Path:
    runtime_source_repository._ordinary(path)
    if not path.is_dir() or not Path(_git(path, "rev-parse", "--show-toplevel")).samefile(path):
        raise ValueError("Harness must name a checkout root")
    return path


def acquire_checkout(srcroot: Path, checkout: Path, remote: str) -> Path:
    """Clone without executing repository hooks, or fast-forward a matching clean anchor."""
    runtime_source_repository._ordinary(srcroot)
    runtime_source_repository._ordinary(checkout)
    if not checkout.exists():
        if checkout.parent != srcroot:
            raise ValueError("New checkout must be directly under the selected source root")
        srcroot.mkdir(parents=True, exist_ok=True)
        _git(srcroot, "clone", "--no-recurse-submodules", "--", remote, str(checkout))
    else:
        _anchor(checkout)
        actual = _git(checkout, "remote", "get-url", "origin")
        if repository_remote(actual)[1].casefold() != remote.casefold():
            raise ValueError("Existing checkout has a different origin")
        if _git(checkout, "status", "--porcelain"):
            raise ValueError("Refusing to update a dirty checkout")
        branch = _git(checkout, "symbolic-ref", "--short", "HEAD")
        upstream = _git(checkout, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if upstream != f"origin/{branch}":
            raise ValueError("Refusing to refresh from a different tracking branch")
        _git(checkout, "fetch", "--no-tags", "origin", f"refs/heads/{branch}")
        _git(checkout, "merge", "--ff-only", "FETCH_HEAD")
        if _git(checkout, "rev-parse", "HEAD") != _git(checkout, "rev-parse", "FETCH_HEAD"):
            raise ValueError("Local checkout has unpublished commits")
    return _anchor(checkout)


def first_registration(root: Path, srcroot: Path, repository: str) -> Path:
    """Create one complete registry or reuse an identical prior registration."""
    for path in (root, srcroot):
        if not path.is_absolute():
            raise ValueError("Footprint and source root must be absolute")
        runtime_source_repository._ordinary(path)
    registry = root / "registry"
    local = Path(repository).expanduser()
    remote = None
    if local.is_absolute() or local.is_dir():
        checkout = _anchor(local.absolute())
        name = checkout.name
        if not _NAME.fullmatch(name):
            raise ValueError("Invalid harness name")
    else:
        name, remote = repository_remote(repository)
        checkout = srcroot / name

    existing = registry.exists() or registry.is_symlink()
    if existing:
        default, anchors = registered_suite.registrations(root)
        if name != default or name not in anchors:
            raise ValueError("Existing registry requires an explicit registration update")
        registered = anchors[name]
        if remote is None and not registered.samefile(checkout):
            raise ValueError("Existing harness name refers to another checkout")
        checkout = registered
    if remote:
        acquire_checkout(srcroot, checkout, remote)
    if not existing:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            staging = Path(temporary) / "registry"
            staging.mkdir()
            entry = {harness_state._platform_key(): str(checkout)}
            if remote:
                entry["remote"] = remote
            for filename, document in (
                ("repos.yaml", {"repos": {name: entry}}),
                ("projects.yaml", {"projects": {name: {}}}),
                ("footprint.json", {"schema": "copilot-extensions.suite-footprint", "version": 1,
                                    "root": str(root), "default_project": name, "srcroot": str(srcroot)}),
            ):
                (staging / filename).write_text(json.dumps(document) + "\n", encoding="utf-8")
            staging.rename(registry)
    return registered_suite.registrations(root)[1][name]


def refresh_registrations(root: Path, srcroot: Path, repository: str | None) -> Path:
    default, anchors = registered_suite.registrations(root)
    rows = harness_state.repos_registry(
        strict=True, input_roots=harness_state.ProfileInputRoots(root / "registry", {}),
    )["repos"]
    for name, anchor in anchors.items():
        if name == default and repository:
            # first_registration refreshed a remote request; a local request
            # explicitly selects the working checkout without a fetch.
            continue
        remote = rows[name].get("remote")
        if remote:
            acquire_checkout(srcroot, anchor, repository_remote(remote)[1])
    return anchors[default]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--srcroot", type=Path, default=Path.home() / "src")
    parser.add_argument("--repo", default=os.environ.get("HARNESS_REPO"))
    args = parser.parse_args(argv)
    anchor = None
    if args.repo:
        anchor = first_registration(args.root, args.srcroot, args.repo)
    elif (args.root / "registry").exists():
        default, anchors = registered_suite.registrations(args.root)
        anchor = anchors[default]
    selection = runtime_source_repository.read(anchor, default_repository=source_config.DEFAULT_REPO)
    print(json.dumps(selection, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
