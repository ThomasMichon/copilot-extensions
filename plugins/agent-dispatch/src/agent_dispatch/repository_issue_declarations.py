"""Expand a validated repository issue loop into registrar declarations."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .registrar import ProfileDeclaration, load_declaration


def expand_repository_issue_loop(
    data: Mapping[str, Any],
    *,
    repo_root: str | Path | None = None,
    inherited_script_fields: frozenset[str] = frozenset(),
) -> tuple[ProfileDeclaration, ...]:
    from .repository_issue_loops import validate_config

    config = validate_config(
        data, cwd=repo_root, inherited_script_fields=inherited_script_fields,
        resolve_resources=False,
    )
    spec: dict[str, Any] = {
        "id": f"{config['name']}-source",
        "interval_seconds": config["tick_interval_seconds"],
        "lease_scope": f"repository-issue-loop:{config['name']}",
        "repository_issue_loop": dict(data),
    }
    if data.get("execution_repo") is not None:
        spec["cwd"] = f"repo:{data['execution_repo']}:."
    elif repo_root is not None:
        spec["cwd"] = str(Path(repo_root).resolve())
    common = {
        "owner": config["owner"],
        "description": config["description"],
    }
    source = load_declaration(
        {
            "name": f"{config['name']}-source",
            "kind": "emitter",
            "spec": spec,
            "filters": config["filters"],
            **common,
        }
    )
    pool = load_declaration(
        {
            "name": f"{config['name']}-workers",
            "labels": [config["task_label"]],
            "repos": config["repo"],
            **config["pool"],
            "concurrency": 1,
            "filters": config["worker_filters"],
            **common,
        }
    )
    return source, pool
