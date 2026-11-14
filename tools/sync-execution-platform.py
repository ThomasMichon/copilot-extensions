#!/usr/bin/env python3
"""Publish canonical stdlib helpers for pre-runtime ownership admission."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "libs/machine-transport/src/machine_transport/execution_platform.py"
TARGET = ROOT / "plugins/agent-worktrees/scripts/execution_platform.py"


def identity_source() -> bytes:
    source = (ROOT / "plugins/agent-worktrees/src/agent_worktrees/project_state.py").read_text(
        encoding="utf-8"
    )
    names = {
        "IDENTITY_SCHEMA", "IDENTITY_VERSION", "RepositoryIdentity",
        "_is_link_or_reparse", "_normalize_repository_remote", "_repository_id", "_identity",
        "_receipt", "_validate_receipt",
    }
    parts = [
        '"""Generated project identity reader; source: agent_worktrees.project_state."""',
        "from __future__ import annotations",
        "import hashlib\nimport json\nimport stat\nimport time",
        "from dataclasses import dataclass\nfrom pathlib import Path",
        "from urllib.parse import urlsplit, urlunsplit",
        "from remote_identity import normalize_remote",
    ]
    for node in ast.parse(source).body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name not in names:
            continue
        start = min([node.lineno, *(item.lineno for item in getattr(node, "decorator_list", []))])
        text = "\n".join(source.splitlines()[start - 1:node.end_lineno])
        if name == "_identity":
            header = (
                "def _identity(project: str, *, remote: str | None = None) -> RepositoryIdentity:\n"
                "    context = registry_paths.installation_context()"
            )
            normalized = (
                "    normalized = (\n"
                "        _normalized_repository_remote(project) if remote is None\n"
                "        else _normalize_repository_remote(project, remote)\n"
                "    )"
            )
            if text.count(header) != 1 or text.count(normalized) != 1:
                raise ValueError("project identity adapter requires source reconciliation")
            text = text.replace(
                header,
                "def project_identity(project: str, remote: str, context: dict) -> RepositoryIdentity:",
            ).replace(normalized, "    normalized = _normalize_repository_remote(project, remote)")
        parts.append(text)
        names.remove(name)
    if names:
        raise ValueError(f"project identity source omitted {sorted(names)}")
    return ("\n\n".join(parts) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = {
        TARGET: SOURCE.read_bytes(),
        TARGET.with_name("remote_identity.py"): (
            ROOT / "libs/plugin-resolve/src/plugin_resolve/remote_identity.py"
        ).read_bytes(),
        TARGET.with_name("managed_project_identity.py"): identity_source(),
    }
    for target, content in outputs.items():
        if args.check:
            if not target.is_file() or target.read_bytes() != content:
                print(f"Standalone admission helper is out of sync: {target.name}")
                return 1
        else:
            target.write_bytes(content)
    print("Standalone admission helpers are synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
