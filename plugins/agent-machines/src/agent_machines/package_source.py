"""Optional ``source`` for ``package`` resources: install a uv-tool/pipx
package from a local path or a git URL instead of an index by name.

The package ``id`` still names the installed tool, so detection and
uninstall are unchanged; only the install/update argv differs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .resources import ResourceContribution, ResourceFinding

SOURCE_MANAGERS = ("uv-tool", "pipx")


def validate(decl: dict[str, Any]) -> None:
    """Raise ``ValueError`` for an invalid ``package.source`` declaration."""
    if "source" not in decl:
        return
    source = decl["source"]
    if decl.get("type") != "package" or decl.get("manager") not in SOURCE_MANAGERS:
        raise ValueError(f"package source is only valid for managers {SOURCE_MANAGERS}")
    if not isinstance(source, dict) or set(source) - {"path", "git", "ref"}:
        raise ValueError("package source must be a mapping of path, or git with optional ref")
    if ("path" in source) == ("git" in source) or ("ref" in source and "git" not in source):
        raise ValueError(
            "package source declares exactly one of 'path' or 'git' ('ref' needs 'git')"
        )
    for key, value in source.items():
        if not isinstance(value, str) or not value.strip() or value.startswith("-"):
            raise ValueError(
                f"package source {key!r} must be a non-empty string not starting with '-'"
            )
        if key != "path" and (
            any(ch.isspace() for ch in value) or (key == "ref" and "@" in value)
        ):
            raise ValueError(f"package source {key!r} must not contain whitespace or '@'")


def resolve(member: ResourceContribution) -> dict[str, str] | None:
    """Return the member's source with a relative ``path`` anchored at the
    declaring requirement-package file's directory."""
    source = member.declaration.get("source")
    if not source:
        return None
    if "git" in source:
        return {key: str(source[key]) for key in ("git", "ref") if key in source}
    path = Path(str(source["path"])).expanduser()
    if not path.is_absolute() and member.package.source_path is not None:
        path = Path(member.package.source_path).parent / path
    return {"path": str(path.resolve())}


def merge(
    members: list[ResourceContribution], identity: tuple[Any, ...]
) -> tuple[dict[str, str] | None, list[ResourceFinding]]:
    from .resources import ResourceFinding

    sources = {repr(value): value for value in (resolve(m) for m in members) if value}
    if len(sources) > 1:
        return None, [
            ResourceFinding(
                "error",
                "resource-conflict",
                f"package '{identity[2]}' ({identity[1]}) declares conflicting sources "
                "across packages.",
            )
        ]
    return next(iter(sources.values()), None), []


def install_argv(desired: dict[str, Any]) -> list[str] | None:
    """The forced install argv for a sourced package, else ``None``."""
    source = desired.get("source")
    if not source:
        return None
    if "path" in source:
        spec = source["path"]
    else:
        url = source["git"] if source["git"].startswith("git+") else f"git+{source['git']}"
        spec = f"{url}@{source['ref']}" if source.get("ref") else url
    prefix = ["uv", "tool"] if desired.get("manager") == "uv-tool" else ["pipx"]
    return [*prefix, "install", "--force", spec]
