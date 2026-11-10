"""Explicit, destination-bound permission to use a vault entry for HTTP auth."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import config


class BindingError(ValueError):
    """A secret-free request or binding refusal."""


@dataclass(frozen=True)
class Destination:
    scheme: str
    host: str
    port: int
    path: str


@dataclass(frozen=True)
class HttpBinding:
    destination: Destination
    entry: str
    scheme: str


def destination(url: str, *, prefix: bool = False) -> Destination:
    if (
        not isinstance(url, str)
        or not url
        or any(ord(character) <= 32 or ord(character) == 127 for character in url)
    ):
        raise BindingError("URL must be an absolute HTTP(S) URL without whitespace")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
    except ValueError:
        raise BindingError("URL has an invalid host or port") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or not host.isascii()
        or host.endswith(".")
        or not re.fullmatch(r"[a-zA-Z0-9.:-]+", host)
        or parsed.username is not None
        or parsed.password is not None
        or "#" in url
        or "\\" in url
        or not 1 <= port <= 65535
        or (prefix and "?" in url)
    ):
        raise BindingError("URL must use an exact HTTP(S) host without userinfo or fragment")
    path = parsed.path or "/"
    if (
        not path.startswith("/")
        or "%" in path
        or ";" in path
        or "//" in path
        or not path.isascii()
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise BindingError("URL path contains ambiguous encoding or traversal")
    return Destination(parsed.scheme, host.lower(), port, path)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BindingError("HTTP binding document contains duplicate keys")
        result[key] = value
    return result


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def read_bindings(path: Path) -> list[HttpBinding]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, ValueError):
        raise BindingError("HTTP binding document is unreadable or invalid") from None
    if (
        not isinstance(raw, dict)
        or set(raw) != {"version", "bindings"}
        or type(raw["version"]) is not int
        or raw["version"] != 1
        or not isinstance(raw["bindings"], list)
    ):
        raise BindingError("HTTP binding document must use version 1 and a bindings list")
    bindings: list[HttpBinding] = []
    for item in raw["bindings"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"url_prefix", "entry", "scheme", "allow_injection"}
            or type(item.get("allow_injection")) is not bool
            or not _text(item.get("entry"))
            or "/" not in item["entry"]
            or item["entry"] != item["entry"].strip()
            or not isinstance(item.get("scheme"), str)
            or item.get("scheme") not in {"token", "bearer", "basic"}
        ):
            raise BindingError(
                "Each HTTP binding needs a full entry path, scheme and boolean allow_injection"
            )
        target = destination(item["url_prefix"], prefix=True)
        if item["allow_injection"]:
            bindings.append(HttpBinding(target, item["entry"], item["scheme"]))
    return bindings


def select_binding(url: str, bindings: list[HttpBinding]) -> HttpBinding:
    target = destination(url)
    matches: list[HttpBinding] = []
    for binding in bindings:
        allowed = binding.destination
        prefix = allowed.path.rstrip("/")
        if (target.scheme, target.host, target.port) == (
            allowed.scheme,
            allowed.host,
            allowed.port,
        ) and (target.path == prefix or target.path.startswith(prefix + "/")):
            matches.append(binding)
    if not matches:
        raise BindingError(f"no binding for {target.host}")
    if len(matches) != 1:
        raise BindingError(f"ambiguous binding for {target.host}")
    return matches[0]


def binding_file() -> Path:
    """Native or trusted-extension policy, never a request-side entry selector."""
    from .extensions import get_registry

    try:
        native = config.load_global_config(strict=True)
        if "http_bindings" in native:
            selected = native["http_bindings"]
        else:
            contributed = get_registry().collect_config(
                config.configuration_source(None),
                strict=True,
            )
            if "http_bindings" not in contributed:
                return config._global_config_dir() / "http-bindings.json"
            selected = contributed["http_bindings"]
        if not _text(selected):
            raise BindingError("http_bindings must name a binding file")
        path = Path(selected).expanduser()
        if not path.is_absolute():
            if "http_bindings" not in native:
                raise BindingError("Extension http_bindings must be an absolute path")
            path = config._global_config_dir() / path
        return path
    except BindingError:
        raise
    except Exception:
        raise BindingError("HTTP binding configuration could not be resolved") from None
