#!/usr/bin/env python3
"""Resolve the single repository-scoped agent-index activation config."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

CONFIG_RELATIVE = Path(".agent-index") / "config.yaml"
WORKTREES_CONFIG_RELATIVE = Path(".agent-worktrees") / "config.yaml"
CONFIG_DATA_ENV = "AGENT_INDEX_CONFIG_DATA_B64"
REPO_ENV = "AGENT_INDEX_REPO"
WORKTREES_COMMAND_ENV = "AGENT_WORKTREES_COMMAND"
MAX_CONFIG_BYTES = 256 * 1024
MAX_FORWARDED_CONFIG_BYTES = 16 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class ConfigError(ValueError):
    """The candidate configuration is present but unusable."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ConfigError(f"duplicate key: {key}")
        value[key] = item
    return value


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _safe_file(path: Path, root: Path) -> tuple[str, Path | None]:
    """Return absent/ready/invalid for an ordinary file contained by *root*."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return "invalid", None
    candidate = resolved_root / path.relative_to(root)
    current = resolved_root
    for part in candidate.relative_to(resolved_root).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return "absent", None
        except OSError:
            return "invalid", None
        if _is_link_or_reparse(info):
            return "invalid", None
        if current != candidate and not stat.S_ISDIR(info.st_mode):
            return "invalid", None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        size = resolved.stat().st_size
    except (OSError, ValueError):
        return "invalid", None
    if not resolved.is_file() or size > MAX_CONFIG_BYTES:
        return "invalid", None
    return "ready", resolved


def _strip_comment(raw: str) -> str:
    quote = ""
    escaped = False
    for index, character in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {"'", '"'}:
            if not quote:
                quote = character
            elif quote == character:
                quote = ""
            continue
        if character == "#" and not quote and (
            index == 0 or raw[index - 1].isspace()
        ):
            return raw[:index]
    if quote:
        raise ConfigError("unterminated quoted scalar")
    return raw


def _split_mapping(text: str) -> tuple[str, str]:
    quote = ""
    depth = 0
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {"'", '"'}:
            if not quote:
                quote = character
            elif quote == character:
                quote = ""
            continue
        if quote:
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                raise ConfigError("unbalanced flow mapping")
        elif character == ":" and depth == 0:
            key = text[:index].strip()
            if (
                not key
                or not (key[0].isalpha() or key[0] == "_")
                or not all(char.isalnum() or char in "_-" for char in key)
            ):
                raise ConfigError("unsupported mapping key")
            return key, text[index + 1 :].strip()
    raise ConfigError("expected a mapping entry")


def _parse_scalar(text: str) -> Any:
    if not text:
        raise ConfigError("missing scalar")
    if text[0] == '"' or text[0] == "'":
        if len(text) < 2 or text[-1] != text[0]:
            raise ConfigError("unterminated quoted scalar")
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError as exc:
                raise ConfigError("invalid quoted scalar") from exc
        return text[1:-1].replace("''", "'")
    folded = text.casefold()
    if folded in {"true", "false"}:
        return folded == "true"
    if folded in {"null", "~"}:
        return None
    if text.startswith(("&", "*", "!")):
        raise ConfigError("YAML aliases, anchors, and tags are unsupported")
    if text.startswith("{"):
        if not text.endswith("}"):
            raise ConfigError("unterminated flow mapping")
        inner = text[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, Any] = {}
        quote = ""
        start = 0
        parts: list[str] = []
        for index, character in enumerate(inner):
            if character in {"'", '"'}:
                if not quote:
                    quote = character
                elif quote == character:
                    quote = ""
            elif character == "," and not quote:
                parts.append(inner[start:index])
                start = index + 1
        if quote:
            raise ConfigError("unterminated flow mapping quote")
        parts.append(inner[start:])
        for part in parts:
            key, value = _split_mapping(part.strip())
            if key in result:
                raise ConfigError(f"duplicate key: {key}")
            result[key] = _parse_scalar(value)
        return result
    if text.startswith("["):
        if not text.endswith("]"):
            raise ConfigError("unterminated flow list")
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    if any(character in text for character in "[]{}") or text in {"|", ">"}:
        raise ConfigError("unsupported YAML scalar")
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    tokens: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw:
            raise ConfigError("tabs are not supported in YAML indentation")
        content = _strip_comment(raw).rstrip()
        if not content.strip():
            continue
        stripped = content.lstrip(" ")
        if stripped.startswith(("%", "---", "...")):
            raise ConfigError("YAML directives and document markers are unsupported")
        tokens.append((len(content) - len(stripped), stripped))
    if not tokens:
        return {}

    def parse_mapping(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(tokens):
            current_indent, content = tokens[index]
            if current_indent < indent:
                break
            if current_indent != indent or content.startswith("-"):
                raise ConfigError("invalid mapping indentation")
            key, value = _split_mapping(content)
            if key in result:
                raise ConfigError(f"duplicate key: {key}")
            index += 1
            if value:
                result[key] = _parse_scalar(value)
            elif index < len(tokens) and (
                tokens[index][0] > indent
                or (
                    tokens[index][0] == indent
                    and tokens[index][1].startswith("-")
                )
            ):
                result[key], index = parse_block(index, tokens[index][0])
            else:
                result[key] = {}
        return result, index

    def parse_list(index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while index < len(tokens):
            current_indent, content = tokens[index]
            if current_indent < indent:
                break
            if current_indent == indent and not content.startswith("-"):
                break
            if current_indent != indent:
                raise ConfigError("invalid list indentation")
            remainder = content[1:].strip()
            index += 1
            if not remainder:
                if index >= len(tokens) or tokens[index][0] <= indent:
                    raise ConfigError("list item is missing a value")
                item, index = parse_block(index, tokens[index][0])
                result.append(item)
                continue
            if ":" not in remainder:
                result.append(_parse_scalar(remainder))
                if index < len(tokens) and tokens[index][0] > indent:
                    raise ConfigError("scalar list item has nested content")
                continue
            key, value = _split_mapping(remainder)
            item: dict[str, Any] = {}
            if value:
                item[key] = _parse_scalar(value)
            elif index < len(tokens) and tokens[index][0] > indent:
                item[key], index = parse_block(index, tokens[index][0])
            else:
                item[key] = {}
            if index < len(tokens) and tokens[index][0] > indent:
                continuation_indent = tokens[index][0]
                continuation, index = parse_mapping(index, continuation_indent)
                for continuation_key, continuation_value in continuation.items():
                    if continuation_key in item:
                        raise ConfigError(f"duplicate key: {continuation_key}")
                    item[continuation_key] = continuation_value
            result.append(item)
        return result, index

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if tokens[index][0] != indent:
            raise ConfigError("invalid YAML indentation")
        if tokens[index][1].startswith("-"):
            return parse_list(index, indent)
        return parse_mapping(index, indent)

    value, end = parse_block(0, tokens[0][0])
    if end != len(tokens) or not isinstance(value, dict):
        raise ConfigError("configuration root must be a mapping")
    return value


def _load_yaml_mapping(path: Path) -> tuple[str, dict[str, Any] | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "invalid", None
    try:
        import yaml
        from yaml.constructor import ConstructorError
        from yaml.events import AliasEvent
        from yaml.resolver import BaseResolver
    except ImportError:
        try:
            return "ready", _parse_simple_yaml(text)
        except ConfigError:
            return "invalid", None

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise ConstructorError(
                None, None, "expected a mapping node", node.start_mark
            )
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, AliasEvent) or getattr(event, "anchor", None):
                raise ConfigError("YAML aliases and anchors are not supported")
        value = yaml.load(text, Loader=UniqueKeyLoader)
    except Exception:
        return "invalid", None
    return ("ready", value) if isinstance(value, dict) else ("invalid", None)


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
                folded = source["name"].casefold()
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


def _load_candidate(path: Path, root: Path) -> tuple[str, dict[str, Any] | None]:
    state, safe_path = _safe_file(path, root)
    if state != "ready" or safe_path is None:
        return state, None
    state, data = _load_yaml_mapping(safe_path)
    if state != "ready" or data is None:
        return state, None
    try:
        return "ready", _validate_config(data)
    except ConfigError:
        return "invalid", None


def _git_root(start: Path) -> Path | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        root = Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None


def _requires_external(root: Path) -> tuple[str, bool]:
    path = root / WORKTREES_CONFIG_RELATIVE
    state, safe_path = _safe_file(path, root)
    if state == "absent":
        return "ready", False
    if state != "ready" or safe_path is None:
        return "invalid", False
    state, data = _load_yaml_mapping(safe_path)
    if state != "ready" or data is None:
        return state, False
    for key in ("stateless", "requires_external_state_root"):
        if key in data and not isinstance(data[key], bool):
            return "invalid", False
    return "ready", bool(
        data.get("stateless") or data.get("requires_external_state_root")
    )


def _worktrees_command() -> str | None:
    explicit = os.environ.get(WORKTREES_COMMAND_ENV)
    if explicit is not None:
        value = explicit.strip()
        return value or None
    return shutil.which("agent-worktrees")


def _worktrees_argv(command: str, *arguments: str) -> list[str]:
    if os.name == "nt" and Path(command).suffix.casefold() == ".ps1":
        shell = shutil.which("pwsh") or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        return [shell, "-NoProfile", "-File", command, *arguments]
    return [command, *arguments]


def _external_state_root(root: Path) -> tuple[str, Path | None]:
    command = _worktrees_command()
    if not command:
        return "unavailable", None
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "COPILOT_PLUGIN_ROOT",
            "PLUGIN_ROOT",
            "CLAUDE_PLUGIN_ROOT",
            "AGENT_INDEX_PAYLOAD_ROOT",
            "PYTHONHOME",
            "PYTHONPATH",
        }
    }
    try:
        result = subprocess.run(
            _worktrees_argv(command, "state-root", "--json"),
            cwd=str(root),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable", None
    if result.returncode != 0:
        return "unavailable", None
    try:
        payload = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except (ConfigError, TypeError, ValueError):
        return "invalid", None
    if (
        not isinstance(payload, dict)
        or payload.get("requires_external") is not True
        or payload.get("bound") is not True
        or payload.get("source") != "knowledge_repo"
        or not isinstance(payload.get("repo"), str)
        or not payload["repo"].strip()
        or not isinstance(payload.get("state_root"), str)
        or not payload["state_root"].strip()
        or payload.get("error") not in (None, "")
    ):
        return "invalid", None
    try:
        state_root = Path(payload["state_root"]).expanduser().resolve(strict=True)
        state_root.relative_to(state_root.anchor)
        if state_root == root.resolve(strict=True) or not state_root.is_dir():
            return "invalid", None
    except (OSError, ValueError):
        return "unavailable", None
    return "ready", state_root


def _inline_config() -> tuple[str, dict[str, Any] | None]:
    encoded = os.environ.get(CONFIG_DATA_ENV)
    if encoded is None:
        return "absent", None
    if not encoded or len(encoded) > MAX_FORWARDED_CONFIG_BYTES * 2:
        return "invalid", None
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(raw) > MAX_FORWARDED_CONFIG_BYTES:
            raise ConfigError("forwarded config is too large")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        if not isinstance(value, dict):
            raise ConfigError("forwarded config must be an object")
        normalized = _validate_config(value)
    except (ConfigError, ValueError, UnicodeError):
        return "invalid", None
    return "ready", normalized


def _result(
    *,
    opted_in: bool,
    source: str,
    reason: str,
    config: Path | None,
    repo_root: Path | None,
    requires_external: bool,
    normalized: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalized or {"indexers": [], "sources": []}
    return {
        "schema": "agent-index.effective-config",
        "version": 1,
        "opted_in": opted_in,
        "state": "active" if opted_in else "inactive",
        "source": source,
        "reason": reason,
        "config": str(config) if config is not None else None,
        "repo_root": str(repo_root) if repo_root is not None else None,
        "requires_external": requires_external,
        "indexers": normalized["indexers"],
        "sources": normalized["sources"],
    }


def resolve(cwd: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Resolve the only configuration allowed to activate agent-index."""
    inline_state, inline = _inline_config()
    if inline_state != "absent":
        if inline_state == "ready" and inline is not None:
            return _result(
                opted_in=True,
                source="forwarded",
                reason="forwarded-config",
                config=None,
                repo_root=None,
                requires_external=False,
                normalized=inline,
            )
        return _result(
            opted_in=False,
            source="forwarded",
            reason="forwarded-config-invalid",
            config=None,
            repo_root=None,
            requires_external=False,
        )

    override = os.environ.get(REPO_ENV)
    start = Path(override if override is not None else (cwd or os.getcwd())).expanduser()
    root = _git_root(start)
    if root is None:
        return _result(
            opted_in=False,
            source="repository",
            reason=(
                "repository-override-unavailable"
                if override is not None
                else "repository-unavailable"
            ),
            config=None,
            repo_root=None,
            requires_external=False,
        )

    local_path = root / CONFIG_RELATIVE
    local_state, local = _load_candidate(local_path, root)
    if local_state == "ready" and local is not None:
        policy_state, requires_external = _requires_external(root)
        return _result(
            opted_in=True,
            source="repository",
            reason="repository-config",
            config=local_path.resolve(),
            repo_root=root,
            requires_external=(
                requires_external if policy_state == "ready" else False
            ),
            normalized=local,
        )
    if local_state != "absent":
        return _result(
            opted_in=False,
            source="repository",
            reason=f"repository-config-{local_state}",
            config=local_path,
            repo_root=root,
            requires_external=False,
        )

    policy_state, requires_external = _requires_external(root)
    if policy_state != "ready":
        return _result(
            opted_in=False,
            source="repository",
            reason=f"repository-state-policy-{policy_state}",
            config=None,
            repo_root=root,
            requires_external=False,
        )
    if not requires_external:
        return _result(
            opted_in=False,
            source="repository",
            reason="repository-config-absent",
            config=None,
            repo_root=root,
            requires_external=False,
        )

    state, state_root = _external_state_root(root)
    if state != "ready" or state_root is None:
        return _result(
            opted_in=False,
            source="external-state-root",
            reason=f"external-state-root-{state}",
            config=None,
            repo_root=root,
            requires_external=True,
        )
    external_path = state_root / CONFIG_RELATIVE
    external_state, external = _load_candidate(external_path, state_root)
    if external_state == "ready" and external is not None:
        return _result(
            opted_in=True,
            source="external-state-root",
            reason="external-state-root-config",
            config=external_path.resolve(),
            repo_root=root,
            requires_external=True,
            normalized=external,
        )
    return _result(
        opted_in=False,
        source="external-state-root",
        reason=f"external-state-root-config-{external_state}",
        config=external_path if external_state != "absent" else None,
        repo_root=root,
        requires_external=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = resolve(args.cwd)
    if args.check:
        return 0 if result["opted_in"] else 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
