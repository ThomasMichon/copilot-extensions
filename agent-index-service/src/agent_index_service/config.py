"""Strict, repository-independent configuration for local component composition."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__


class ConfigurationError(ValueError):
    """An explicit host configuration is missing or invalid."""


def _mapping(value: Any, label: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigurationError(f"{label} must be a mapping with string keys")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigurationError(f"{label}: unknown keys: {', '.join(sorted(unknown))}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigurationError(f"{label} must be a nonempty, trimmed string")
    if any(ord(char) < 32 for char in value):
        raise ConfigurationError(f"{label} must not contain control characters")
    return value


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ConfigurationError(f"{label} must be an integer in [{low}, {high}]")
    return value


def _path(value: Any, label: str) -> Path:
    path = Path(_text(value, label)).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{label} must be an absolute path")
    return path.resolve()


@dataclass(frozen=True)
class HostConfig:
    config_file: Path
    home: Path
    data: Path
    routing: Path
    sources: tuple[dict[str, Any], ...]
    port: int = 0
    engine_port: int = 8421
    model: str = "jinaai/jina-embeddings-v2-base-code"
    device: str = "cpu"
    batch_size: int = 16
    stream_batch_size: int = 64
    indexer_nice: int = 10

    def core_environment(self) -> dict[str, str]:
        inline = {"role": "host", "corpus": {"sources": list(self.sources)}}
        encoded = base64.urlsafe_b64encode(json.dumps(inline).encode("utf-8")).decode("ascii")
        return {
            "AGENT_INDEX_HOME": str(self.home),
            "AGENT_INDEX_CONFIG": str(self.config_file),
            "AGENT_INDEX_CONFIG_ROOT": str(self.config_file.parent),
            "AGENT_INDEX_DATA_DIR": str(self.data),
            "AGENT_INDEX_STATE_DIR": str(self.data),
            "AGENT_INDEX_ROUTING_DIR": str(self.routing),
            "AGENT_INDEX_RUN_DIR": str(self.home / "run"),
            "AGENT_INDEX_LOG_DIR": str(self.home / "logs"),
            "AGENT_INDEX_CACHE_DIR": str(self.home / "cache"),
            "AGENT_INDEX_BACKUP_DIR": str(self.home / "backups"),
            "AGENT_INDEX_PROVIDERS_DIR": str(self.home / "providers.d"),
            "AGENT_INDEX_ROLE": "host",
            "AGENT_INDEX_SOURCE_MODE": "explicit",
            "AGENT_INDEX_CONFIG_DATA_B64": encoded,
            "AGENT_INDEX_HOST": "127.0.0.1",
            "AGENT_INDEX_PORT": str(self.port),
            "AGENT_INDEX_RUNTIME_VERSION": __version__,
            "AGENT_INDEX_ENGINE_MODE": "external",
            "AGENT_INDEX_ENGINE_HOST": "127.0.0.1",
            "AGENT_INDEX_ENGINE_PORT": str(self.engine_port),
            "AGENT_INDEX_ENGINE_URL": f"http://127.0.0.1:{self.engine_port}",
            "AGENT_INDEX_SEARCH_IN_PROCESS": "0",
            "AGENT_INDEX_MODEL": self.model,
            "AGENT_INDEX_DEVICE": self.device,
            "AGENT_INDEX_BATCH_SIZE": str(self.batch_size),
            "AGENT_INDEX_STREAM_BATCH_SIZE": str(self.stream_batch_size),
            "AGENT_INDEX_INDEXER_NICE": str(self.indexer_nice),
        }


def _source(value: Any, index: int) -> dict[str, Any]:
    label = f"sources[{index}]"
    raw = _mapping(value, label, {"name", "type", "path", "ref", "auth", "trust_domain"})
    name = _text(raw.get("name"), f"{label}.name")
    kind = _text(raw.get("type", name.split(":", 1)[0]), f"{label}.type")
    if kind not in {"git", "github"} or name.split(":", 1)[0] != kind:
        raise ConfigurationError(f"{label}: supported source types are git and github")
    result: dict[str, Any] = {"name": name, "type": kind}
    if kind == "git":
        result["_repo_path"] = str(_path(raw.get("path"), f"{label}.path"))
        if "ref" in raw:
            result["ref"] = _text(raw["ref"], f"{label}.ref")
    else:
        parts = name.removeprefix("github:").split("/")
        if not name.startswith("github:") or len(parts) != 2 or not all(parts):
            raise ConfigurationError(f"{label}.name must be github:owner/repo")
        if any(":" in part or "\\" in part or " " in part for part in parts):
            raise ConfigurationError(f"{label}.name must be github:owner/repo")
        if "path" in raw or "ref" in raw:
            raise ConfigurationError(f"{label}: path/ref are only supported for git")
    if "auth" in raw:
        auth = _mapping(raw["auth"], f"{label}.auth", {"account"})
        result["auth"] = {"account": _text(auth.get("account"), f"{label}.auth.account")}
    if "trust_domain" in raw:
        result["trust_domain"] = _text(raw["trust_domain"], f"{label}.trust_domain")
    return result


def parse_config(value: Any, config_file: Path) -> HostConfig:
    raw = _mapping(
        value, "config",
        {"schema_version", "home", "data", "routing", "listener", "sources", "engine", "limits"},
    )
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ConfigurationError("schema_version must be 1")
    home = _path(raw.get("home"), "home")
    listener = _mapping(raw.get("listener", {}), "listener", {"host", "port"})
    if listener.get("host", "127.0.0.1") != "127.0.0.1":
        raise ConfigurationError(
            "listener.host must be 127.0.0.1; remote transport is future scope"
        )
    engine = _mapping(raw.get("engine", {}), "engine", {"host", "port", "mode", "model", "device"})
    if engine.get("host", "127.0.0.1") != "127.0.0.1":
        raise ConfigurationError("engine.host must be 127.0.0.1; remote adapters are future scope")
    if engine.get("mode", "external") != "external":
        raise ConfigurationError(
            "engine.mode must be external; this program never starts an engine"
        )
    device = engine.get("device", "cpu")
    if device not in ("cpu", "cuda"):
        raise ConfigurationError("engine.device must be cpu or cuda")
    values = raw.get("sources")
    if not isinstance(values, list) or not values:
        raise ConfigurationError("sources must be a nonempty list (no implicit repository source)")
    sources = tuple(_source(source, i) for i, source in enumerate(values))
    names = [source["name"].casefold() for source in sources]
    if len(set(names)) != len(names):
        raise ConfigurationError("source names must be unique, case-insensitively")
    limits = _mapping(raw.get("limits", {}), "limits", {
        "batch_size", "stream_batch_size", "indexer_nice",
    })
    return HostConfig(
        config_file=config_file.resolve(),
        home=home,
        data=_path(raw["data"], "data") if "data" in raw else home / "data",
        routing=_path(raw["routing"], "routing") if "routing" in raw else home,
        sources=sources,
        port=_integer(listener.get("port", 0), "listener.port", 0, 65535),
        engine_port=_integer(engine.get("port", 8421), "engine.port", 1, 65535),
        model=_text(engine.get("model", "jinaai/jina-embeddings-v2-base-code"), "engine.model"),
        device=device,
        batch_size=_integer(limits.get("batch_size", 16), "limits.batch_size", 1, 65536),
        stream_batch_size=_integer(
            limits.get("stream_batch_size", 64), "limits.stream_batch_size", 1, 65536,
        ),
        indexer_nice=_integer(limits.get("indexer_nice", 10), "limits.indexer_nice", 0, 19),
    )


def load_config(path: Path) -> HostConfig:
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader: Any, node: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str) or key in result:
                raise ConfigurationError("config keys must be unique strings")
            result[key] = loader.construct_object(value_node)
        return result

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read host configuration {path}: {exc}") from exc
    return parse_config(value, path)
