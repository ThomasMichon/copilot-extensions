"""The native hand-formatted repository registry serializer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .repos import ReposRegistry


def render_registry(registry: ReposRegistry) -> str:
    from .repos import _quote

    header = "# ~/.agent-worktrees/repos.yaml"  # marketplace-isolation: allow legacy
    lines = [header, "# Registry of known repositories and source roots.", ""]
    if registry.srcroot:
        lines.append("srcroot:")
        for plat in ("windows", "wsl", "linux"):
            if plat in registry.srcroot:
                lines.append(f"  {plat}: {_quote(registry.srcroot[plat])}")
        lines.append("")
    if registry.account_map:
        lines.append("account_map:")
        for owner in sorted(registry.account_map):
            lines.append(f"  {_quote(owner)}: {_quote(registry.account_map[owner])}")
        lines.append("")
    # Always render the ``repos:`` key explicitly, even when empty: an
    # all-comment document (no keys at all) parses to ``None``, not a
    # mapping, so the reader would record an ``"invalid"`` baseline and the
    # *next* write would fail with a spurious conflict even though nothing
    # changed concurrently -- e.g. after removing a registry's last repo.
    if not registry.repos:
        lines.append("repos: {}")
        lines.append("")
        return "\n".join(lines) + "\n"
    lines.append("repos:")
    for name in sorted(registry.repos):
        entry = registry.repos[name]
        lines.extend([f"  {name}:", f"    class: {entry.repo_class}"])
        lines.append(f"    agent: {'true' if entry.agent else 'false'}")
        if entry.remote:
            lines.append(f"    remote: {_quote(entry.remote)}")
        if entry.account:
            lines.append(f"    account: {_quote(entry.account)}")
        if entry.copilot_account:
            lines.append(f"    copilot_account: {_quote(entry.copilot_account)}")
        if entry.default_branch:
            lines.append(f"    default_branch: {_quote(entry.default_branch)}")
        if entry.tags:
            rendered = ", ".join(_quote(tag) for tag in entry.tags)
            lines.append(f"    tags: [{rendered}]")
        if entry.contributing:
            lines.append(f"    contributing: {_quote(entry.contributing)}")
        if entry.visibility:
            lines.append(f"    visibility: {entry.visibility}")
        for plat in ("windows", "wsl", "linux"):
            if plat in entry.paths:
                lines.append(f"    {plat}: {_quote(entry.paths[plat])}")
        lines.append("")
    return "\n".join(lines) + "\n"
