"""Observe suite intent through one explicitly addressed Manager association."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import cast

from . import runtime_source_repository, source_config
from .repository_suite import SCHEMA as ASSOCIATION_SCHEMA, read_association
from .runtime_source_config import RuntimeSource

SCHEMA = "worktree-manager.repository-suite-declaration"


def read_declaration(
    root: Path, *, parser_root: Path | None = None,
) -> dict[str, object]:
    """Read desired repository data; do not resolve or acquire the suite."""
    repository_root = read_association(root)
    policy = cast(dict[str, object], runtime_source_repository.read(
        repository_root, default_repository=source_config.DEFAULT_REPO,
        parser_root=parser_root,
    ))
    try:
        RuntimeSource(channel="main", repo=cast(str, policy["repository"]))
    except ValueError:
        raise ValueError("Associated repository suite requires a credential-free remote Git source") from None
    return {
        "schema": SCHEMA, "version": 1, "configuration_root": str(root),
        "repository_policy": policy,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--describe"]:
        print(json.dumps({
            "schema": SCHEMA + ".capabilities", "version": 1,
            "association_schema": ASSOCIATION_SCHEMA, "association_versions": [1],
            "operations": ["inspect"], "explicit_root": True, "read_only": True,
            "resolves_source": False,
        }, sort_keys=True))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_subparsers(dest="operation").add_parser("inspect")
    options = parser.parse_args(arguments)
    try:
        print(json.dumps(read_declaration(options.root), sort_keys=True))
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({"schema": SCHEMA, "version": 1, "error": str(error)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
