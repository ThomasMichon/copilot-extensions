"""Producer-proven repository ownership and receiver-local marketplace rebinding."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from .conventions import LOCAL_MARKETPLACE_SOURCE_KINDS
from .settings import RepoPluginSettings, read_repo_settings, split_source


def github_repository_slug(remote: str) -> str | None:
    if not remote:
        return None
    url = remote.strip()
    match = re.match(
        r"https?://(?:[^@/]+@)?(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        url,
    )
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    match = re.match(
        r"(?:ssh://)?git@github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url,
    )
    return f"{match.group(1)}/{match.group(2)}" if match else None


def requires_repository_binding(specs, repo_roots) -> bool:
    marketplaces = {split_source(spec["source"])[1] for spec in specs}
    for root in repo_roots:
        settings = read_repo_settings(root)
        for marketplace in marketplaces:
            source = settings.marketplaces.get(marketplace, {}).get("source", {})
            if (
                source.get("source") in LOCAL_MARKETPLACE_SOURCE_KINDS
                and isinstance(source.get("path"), str) and Path(source["path"]).is_absolute()
            ):
                return True
    return False


def _repository_lines(path: Path, arguments, environment=None) -> list[str] | None:
    env = dict(os.environ if environment is None else environment)
    for key in tuple(env):
        if key.startswith("GIT_"):
            env.pop(key)
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--path-format=absolute", *arguments],
        capture_output=True, text=True, timeout=30, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        return None
    return result.stdout.strip().splitlines()


def repository_info(path: Path, environment=None) -> tuple[Path, Path] | None:
    lines = _repository_lines(path, ["--show-toplevel", "--git-common-dir"], environment)
    if lines is None:
        return None
    if len(lines) != 2:
        raise ValueError("Repository identity query returned an invalid result")
    return Path(lines[0]).resolve(strict=True), Path(lines[1]).resolve(strict=True)


def _bare_repository(path: Path, environment=None) -> tuple[Path, Path] | None:
    lines = _repository_lines(
        path, ["--is-bare-repository", "--git-common-dir", "--absolute-git-dir"], environment,
    )
    if lines is None:
        return None
    if len(lines) != 3:
        raise ValueError("Bare repository identity query returned an invalid result")
    if lines[0] != "true":
        return None
    return Path(lines[1]).resolve(strict=True), Path(lines[2]).resolve(strict=True)


def common_identity(info: tuple[Path, Path]) -> str:
    return "git-common:" + hashlib.sha256(str(info[1]).encode()).hexdigest()


def owned_relative(root: Path, payload: Path, repository: str, bound_roots, environment=None) -> dict | None:
    owner = repository_info(root, environment)
    payload_owner = repository_info(payload, environment)
    for bound in bound_roots:
        bound = Path(bound).resolve(strict=True)
        binding = repository_info(bound, environment)
        if binding is not None and owner is not None and binding[1] == owner[1]:
            if payload_owner != owner:
                raise ValueError("A nested repository cannot be relocated as its parent's plugin")
            relative = root.relative_to(owner[0])
            return {"repository": repository, "marketplace_relative": relative.as_posix()}
        if binding is None and root.is_relative_to(bound):
            bare = _bare_repository(bound, environment)
            if bare is None:
                continue
            git_dir = bare[1]
            if bound != git_dir and not (git_dir.name == ".git" and bound == git_dir.parent):
                raise ValueError("Registered bare source is not its repository root")
            if _bare_repository(root, environment) != bare or _bare_repository(payload, environment) != bare:
                raise ValueError("A nested repository cannot be relocated as its parent's plugin")
            relative = root.relative_to(bound)
            return {"repository": repository, "marketplace_relative": relative.as_posix()}
    return None


def bind_target(item: dict, repo: Path, repository: str | None, environment=None) -> dict:
    from .reflection import _plan_item

    descriptor = item["target_relative"]
    if (
        not isinstance(descriptor, dict) or set(descriptor) != {"repository", "marketplace_relative"}
        or not repository or descriptor["repository"] != repository
    ):
        raise ValueError("Target-relative source does not match the execution repository")
    info = repository_info(repo, environment)
    if info is None or (repository.startswith("git-common:") and common_identity(info) != repository):
        raise ValueError("Target-relative source has a different Git repository identity")
    source = item.get("local_source", {})
    definition = source.get("definition", {})
    origin = definition.get("source", {})
    if not source.get("authored_absolute") or origin.get("source") not in LOCAL_MARKETPLACE_SOURCE_KINDS:
        raise ValueError("Target-relative rebinding requires an authored absolute local source")
    raw = descriptor["marketplace_relative"]
    if not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw:
        raise ValueError("Invalid target-relative marketplace path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Target-relative marketplace escapes the execution checkout")
    root = repo
    for part in relative.parts:
        root /= part
        if root.is_symlink() or getattr(root.lstat(), "st_reparse_tag", 0):
            raise ValueError("Linked target-relative marketplace paths are not permitted")
    root.resolve(strict=True).relative_to(repo)
    _name, marketplace = split_source(item["source"])
    local_definition = {**definition, "source": {**origin, "path": "./" + relative.as_posix()}}
    settings = RepoPluginSettings(marketplaces={marketplace: local_definition})
    result = _plan_item(
        item, (repo, settings), settings.marketplaces, local_mode="reference",
    )
    reference = result["reference"]
    if repository_info(root, environment) != info or repository_info(Path(reference["payload"]), environment) != info:
        raise ValueError("Target-relative marketplace or plugin belongs to a nested repository")
    reference["definition"] = local_definition
    return {
        **result, "target_relative": descriptor, "target_owned": True,
        "local_source": source,
    }
