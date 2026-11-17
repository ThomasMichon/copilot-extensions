"""Validate the repository-scoped agent-index configuration schema."""
from __future__ import annotations

from typing import Any, cast

class ConfigError(ValueError):
    """The candidate configuration is present but unusable."""


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > 2048 or any(ord(char) < 32 for char in result):
        raise ConfigError(f"{field} must be a safe non-empty string")
    return result


def _normalize_indexer(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a mapping")
    item = {"machine": _text(value.get("machine"), f"{field}.machine", required=True)}
    for key in ("ssh", "endpoint", "shell"):
        if key in value:
            item[key] = _text(value.get(key), f"{field}.{key}", required=True)
    ssh = item.get("ssh")
    if ssh is not None and (
        ssh.startswith("-")
        or any(char.isspace() for char in ssh)
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._@:-" for char in ssh)
    ):
        raise ConfigError(
            f"{field}.ssh must be a safe SSH host or configuration alias"
        )
    if item.get("shell") not in (None, "bash", "pwsh"):
        raise ConfigError(f"{field}.shell must be bash or pwsh")
    return {key: value for key, value in item.items() if value is not None}


def _validate_config(data: dict[str, Any]) -> dict[str, Any]:
    indexers: list[dict[str, str]] = []
    plural_present = "indexers" in data
    singular_present = "indexer" in data
    if plural_present:
        raw = data["indexers"]
        if not isinstance(raw, list) or not raw:
            raise ConfigError("indexers must be a non-empty list")
        indexers = [
            _normalize_indexer(item, f"indexers[{index}]")
            for index, item in enumerate(raw)
        ]
        machines = [item["machine"].casefold() for item in indexers]
        if len(machines) != len(set(machines)):
            raise ConfigError("indexers contains duplicate machine identities")
    if singular_present:
        singular = _normalize_indexer(data["indexer"], "indexer")
        if indexers and singular != indexers[0]:
            raise ConfigError("indexer conflicts with the primary indexers entry")
        if not indexers:
            indexers = [singular]

    sources: list[dict[str, str]] = []
    if "corpus" in data:
        corpus = data["corpus"]
        if not isinstance(corpus, dict):
            raise ConfigError("corpus must be a mapping")
        if "sources" in corpus:
            raw_sources = corpus["sources"]
            if not isinstance(raw_sources, list):
                raise ConfigError("corpus.sources must be a list")
            names: set[str] = set()
            for index, raw in enumerate(raw_sources):
                if not isinstance(raw, dict):
                    raise ConfigError(f"corpus.sources[{index}] must be a mapping")
                source = {
                    "name": _text(
                        raw.get("name"),
                        f"corpus.sources[{index}].name",
                        required=True,
                    )
                }
                for key in ("repo", "trust_domain", "type"):
                    if key in raw:
                        source[key] = _text(
                            raw.get(key),
                            f"corpus.sources[{index}].{key}",
                            required=True,
                        )
                folded = cast(str, source["name"]).casefold()
                if folded in names:
                    raise ConfigError("corpus.sources contains duplicate names")
                names.add(folded)
                sources.append(
                    {key: value for key, value in source.items() if value is not None}
                )

    if not indexers and not sources:
        raise ConfigError(
            "configuration must declare indexer/indexers or corpus.sources"
        )
    return {"indexers": indexers, "sources": sources}
