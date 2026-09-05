"""Installer/readiness contract adapter for agent-index."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import config

MODULE_ID = "agent-index/runtime"


@dataclass(frozen=True)
class CorpusConfigInspection:
    """Strict, read-only corpus configuration inventory."""

    sources: tuple[Mapping[str, Any], ...] = ()
    errors: tuple[str, ...] = ()
    present_paths: tuple[Path, ...] = ()


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def unconfigured() -> dict[str, Any]:
    """Return satisfied readiness for a delivered but inactive plugin."""
    return _result(
        "configuration-empty",
        "agent-index is installed but not configured for the current repository. "
        "No service was started or probed.",
    )


def client_configured() -> dict[str, Any]:
    """Return satisfied readiness for an explicitly routed client."""
    return _result(
        "ready",
        "agent-index is configured as a client. Retrieval routes to the "
        "repository's designated indexer; no local service is required.",
    )


def client_transport_missing() -> dict[str, Any]:
    """Return satisfied-empty readiness for an incomplete client designation."""
    return _result(
        "configuration-empty",
        "agent-index designates a remote indexer for the current repository, "
        "but no SSH alias or endpoint is configured. No local service was "
        "started or probed.",
    )


def _read_mapping(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, f"{path}: unreadable configuration: {exc}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"{path}: malformed YAML: {exc}"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, f"{path}: top-level configuration must be a mapping"
    return data, None


def _configured_paths() -> tuple[list[Path], list[str]]:
    paths = [config.config_path()]
    errors: list[str] = []
    home = config._agent_worktrees_home()
    projects_path = home / "projects.yaml"
    repos_path = home / "repos.yaml"
    if not projects_path.exists() and not repos_path.exists():
        return paths, errors
    if not projects_path.exists() or not repos_path.exists():
        missing = projects_path if not projects_path.exists() else repos_path
        errors.append(f"{missing}: adopted-project registry is incomplete")
        return paths, errors
    projects_data, projects_error = _read_mapping(projects_path)
    repos_data, repos_error = _read_mapping(repos_path)
    errors.extend(
        error for error in (projects_error, repos_error) if error is not None
    )
    if errors:
        return paths, errors
    assert projects_data is not None
    assert repos_data is not None
    projects = projects_data.get("projects", {})
    repos = repos_data.get("repos", {})
    if not isinstance(projects, dict):
        errors.append(f"{projects_path}: projects must be a mapping")
        return paths, errors
    if not isinstance(repos, dict):
        errors.append(f"{repos_path}: repos must be a mapping")
        return paths, errors
    platform_key = config._registry_platform_key()
    for name in projects:
        entry = repos.get(name)
        if not isinstance(entry, dict):
            errors.append(f"{repos_path}: adopted project {name!r} has no repo entry")
            continue
        raw = (
            entry.get(platform_key)
            or entry.get("windows")
            or entry.get("linux")
            or entry.get("wsl")
        )
        if not isinstance(raw, str) or not raw.strip():
            errors.append(
                f"{repos_path}: adopted project {name!r} has no usable checkout path"
            )
            continue
        root = Path(raw.strip()).expanduser()
        if not root.is_dir():
            errors.append(f"{root}: adopted project {name!r} is unavailable")
            continue
        paths.append(config.repo_config_path(root))
    return paths, errors


def inspect_configuration(
    paths: Sequence[Path] | None = None,
) -> CorpusConfigInspection:
    """Strictly inspect corpus declarations without creating or indexing data."""
    if paths is None:
        candidates, errors = _configured_paths()
    else:
        candidates, errors = list(paths), []
    candidates = list(dict.fromkeys(candidates))
    sources: dict[str, Mapping[str, Any]] = {}
    present: list[Path] = []
    for path in candidates:
        data, error = _read_mapping(path)
        if error is not None:
            errors.append(error)
            continue
        if data is None:
            continue
        present.append(path)
        corpus = data.get("corpus")
        if corpus is None:
            continue
        if not isinstance(corpus, dict):
            errors.append(f"{path}: corpus must be a mapping")
            continue
        raw_sources = corpus.get("sources", [])
        if not isinstance(raw_sources, list):
            errors.append(f"{path}: corpus.sources must be a list")
            continue
        for index, source in enumerate(raw_sources):
            if not isinstance(source, dict):
                errors.append(f"{path}: corpus.sources[{index}] must be a mapping")
                continue
            name = source.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(
                    f"{path}: corpus.sources[{index}].name must be a non-empty string"
                )
                continue
            normalized = name.strip()
            sources.setdefault(normalized, dict(source))
    return CorpusConfigInspection(
        sources=tuple(sources.values()),
        errors=tuple(errors),
        present_paths=tuple(present),
    )


def evaluate(
    status: Mapping[str, Any],
    inspection: CorpusConfigInspection,
) -> dict[str, Any]:
    """Map service and corpus status without starting a service or reindexing."""
    if not status.get("running"):
        detail = status.get("error") or "no healthy endpoint is discoverable"
        return _result(
            "failed",
            "The agent-index service is unavailable: "
            f"{detail}. Inspect the already-running agent-dispatch supervisor "
            "and `agent-index status`; plugin installers cannot provision or "
            "start the managed host.",
        )
    index = status.get("index")
    if not isinstance(index, Mapping) or index.get("chunks") is None:
        return _result(
            "failed",
            "The agent-index service is running, but corpus state is unknown. "
            "Inspect `agent-index status`; do not assume an empty index.",
        )
    chunks = index.get("chunks")
    if not isinstance(chunks, int) or isinstance(chunks, bool) or chunks < 0:
        return _result(
            "failed",
            "The agent-index service returned an invalid corpus count. Inspect "
            "`agent-index status` before reindexing.",
        )
    if inspection.errors:
        return _result(
            "failed",
            "Corpus configuration is malformed or unreadable: "
            + "; ".join(inspection.errors)
            + ". Fix the reported owner configuration; readiness did not reindex.",
        )
    if not inspection.sources:
        if chunks > 0:
            return _result(
                "failed",
                f"The service reports {chunks} indexed chunk(s), but no readable "
                "corpus source declaration owns them. Restore the source "
                "configuration before operating or reindexing.",
            )
        return _result(
            "configuration-empty",
            "The agent-index service is healthy, but no corpus sources are "
            "configured. No corpus was created or indexed.",
        )
    if chunks == 0:
        return _result(
            "configuration-empty",
            f"{len(inspection.sources)} corpus source(s) are configured, but the "
            "measured corpus contains no indexed chunks. Run reindex explicitly "
            "only if content should exist.",
        )
    return _result(
        "ready",
        f"The service is healthy with {chunks} indexed chunk(s) from "
        f"{len(inspection.sources)} configured source(s).",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
