"""Resolve incompatible whole-file, block and Git configuration owners."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .resources import ResolvedResource, ResourceContext, ResourceFinding


def file_ownership_findings(resolved: list[ResolvedResource]) -> list[ResourceFinding]:
    from .resources import ResourceFinding

    findings: list[ResourceFinding] = []
    by_path: dict[str, list[ResolvedResource]] = {}
    for res in resolved:
        if res.type == "file":
            by_path.setdefault(res.identity[1], []).append(res)
        elif res.type == "git-config":
            by_path.setdefault("$HOME/.gitconfig", []).append(res)
    for path, group in sorted(by_path.items()):
        files = [res for res in group if res.type == "file"]
        blocks = {res.identity[2] for res in files}
        owners = sorted({owner for res in group for owner in res.contributors})
        if files and any(res.type == "git-config" for res in group):
            findings.append(
                ResourceFinding(
                    "error",
                    "resource-conflict",
                    f"file '{path}' has both Git-config and file declarations "
                    f"across packages: {', '.join(owners)}.",
                )
            )
        if "" in blocks and any(block != "" for block in blocks):
            findings.append(
                ResourceFinding(
                    "error",
                    "resource-conflict",
                    f"file '{path}' has both whole-file and managed-block declarations "
                    f"across packages: {', '.join(owners)}; a file is either wholly owned "
                    f"or block-owned, not both.",
                )
            )
    return findings


def conflicting_git_config_files(
    resolved: list[ResolvedResource], ctx: ResourceContext
) -> set[tuple]:
    from .resources import resolve_file_path

    git_resources = [res for res in resolved if res.type == "git-config"]
    if not git_resources:
        return set()
    try:
        target = (ctx.home / ".gitconfig").resolve()
    except (OSError, RuntimeError):
        return {res.identity for res in resolved if res.type in {"file", "git-config"}}
    files = []
    for res in resolved:
        if res.type != "file":
            continue
        path = resolve_file_path(res.desired["path"], ctx.home, ctx.repo_paths)
        try:
            if path is not None:
                # Reserve portable case aliases even before either file exists.
                if str(path.resolve()).casefold() == str(target).casefold():
                    files.append(res)
                elif path.exists() and target.exists() and path.samefile(target):
                    files.append(res)
        except (OSError, RuntimeError):
            files.append(res)
    return {res.identity for res in [*git_resources, *files]} if files else set()
