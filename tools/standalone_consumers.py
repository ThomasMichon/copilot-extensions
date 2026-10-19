"""Explicit standalone distributions participating in repository releases."""

from __future__ import annotations

import re
from pathlib import Path

STANDALONE_CONSUMERS = (
    "worktree-manager", "services/index", "fleet-manager", "services/dispatch",
    "services/chat",
)


def local_dependency_source(project: Path, dependency: str, *, source_root: Path) -> Path:
    """Select an unambiguous declared path without substituting canonical copies.

    Standalone compatibility overrides must follow the selected core's metadata:
    development references and materialized embedded libraries are different
    source identities. Full graph containment and seasoning remain owned by the
    install policy; this reader neither repairs nor rewrites metadata.
    """
    # The existing policy reader also supports fresh Python 3.10 without pip.
    from seasoned_uv import DependencyPolicyError
    from installer_dependency_policy import _contained, _toml

    def identity(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    try:
        roots = (source_root.resolve(),)
        data = _toml(_contained(project / "pyproject.toml", roots))
        sources = data.get("tool", {}).get("uv", {}).get("sources", {})
        matches = [entry for name, entry in sources.items() if identity(name) == identity(dependency)]
        if len(matches) != 1:
            raise ValueError("expected one declared local dependency source")
        entry = matches[0]
        if (
            not isinstance(entry, dict)
            or set(entry) - {"path", "editable"}
            or not isinstance(entry.get("path"), str)
            or not entry["path"].strip()
            or ("editable" in entry and not isinstance(entry["editable"], bool))
        ):
            raise ValueError("expected an unconditional local path dependency")
        source = _contained(project / entry["path"], roots)
        metadata = _toml(_contained(source / "pyproject.toml", roots))
        name = metadata["project"]["name"]
        if not isinstance(name, str) or identity(name) != identity(dependency):
            raise ValueError("local dependency identity differs from its source mapping")
        return source.resolve()
    except (OSError, AttributeError, KeyError, TypeError, ValueError, DependencyPolicyError) as exc:
        raise ValueError(f"invalid local source mapping for {dependency}") from exc
