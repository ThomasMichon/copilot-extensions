"""Release-owned version preparation, without materialization or publication."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promotion_source_archive import changefile_existed_at, main_version_inputs


def seed_versions(
    scratch: Path, main_head: str, *, acc: Any, repo: Path,
    consumers: frozenset[str] | None = None,
) -> None:
    plugins = [
        path.name for path in sorted((scratch / "plugins").iterdir()) if path.is_dir()
    ] if (scratch / "plugins").is_dir() else []
    standalone = acc.iter_standalone_consumer_names() + acc.iter_library_consumer_names()
    if consumers is not None:
        plugins = [name for name in plugins if name in consumers]
        standalone = [name for name in standalone if name in consumers]
    metadata = main_version_inputs(repo, main_head, [
        *(f"plugins/{name}/plugin.json" for name in plugins),
        *(f"{name}/pyproject.toml" for name in standalone),
    ])
    result: dict[str, tuple[str, str]] = {}
    for name in plugins:
        raw = metadata.get(f"plugins/{name}/plugin.json", "").strip()
        if not raw:
            continue
        try:
            version = json.loads(raw)["version"]
        except (json.JSONDecodeError, KeyError):
            continue
        current = acc.read_plugin_json_version(name)
        if current is not None and current != version:
            result[name] = current, version
    parser = getattr(acc, "_project_version_from_text", None)
    for name in standalone:
        version = (
            parser(metadata.get(f"{name}/pyproject.toml", "")) if parser is not None
            else acc._project_version_at(main_head, f"{name}/pyproject.toml")
        )
        current = acc.read_consumer_version(name)
        if consumers is not None and version is None:
            raise ValueError(f"released version metadata is unavailable for {name}")
        if version is not None and current is not None and current != version:
            result[name] = current, version
    if result:
        applied = acc.apply(result, metadata_only=True) if consumers is not None else acc.apply(result)
        if missing := set(result) - set(applied):
            raise ValueError(f"cannot seed shipped versions for: {', '.join(sorted(missing))}")


def prepare_versions(
    scratch: Path, main_head: str, *, acc: Any, repo: Path,
    entries: list[tuple[Path, dict[str, Any]]], last_dev_head: str | None,
    allocate_versions: bool = True, consumers: frozenset[str] | None = None,
) -> tuple[dict[str, tuple[str, str]], list[str], list[tuple[Path, dict[str, Any]]]]:
    """Use the same Main seed, consumed boundary and allocator for both callers."""
    seed_versions(scratch, main_head, acc=acc, repo=repo, consumers=consumers)
    newly_applicable = [
        (path, data) for path, data in entries
        if allocate_versions and (
            last_dev_head is None or not changefile_existed_at(last_dev_head, path.name, repo=repo)
        )
    ]
    grouped: dict[str, list[str]] = {}
    for _, data in newly_applicable:
        for change in data.get("changes", []):
            if consumers is None or change["plugin"] in consumers:
                grouped.setdefault(change["plugin"], []).append(change["type"])
    computed = acc.compute(grouped)
    if consumers is not None:
        computed = {name: pair for name, pair in computed.items() if name in consumers}
    applied = (
        acc.apply(computed, metadata_only=True) if consumers is not None else acc.apply(computed)
    ) if computed else []
    if missing := set(computed) - set(applied):
        raise ValueError(
            "promotion refused: computed a version bump for "
            f"{', '.join(sorted(missing))} but apply() could not write it -- "
            "fix the underlying manifest(s) before promoting again."
        )
    return computed, applied, newly_applicable
