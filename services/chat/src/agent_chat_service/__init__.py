"""Portable conversations and explicit clients; no implicit hosting or adoption."""

import re
from pathlib import Path


def _resolve_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("agent-chat-service").replace(".dev", "-dev")
    except PackageNotFoundError:
        manifest = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not manifest.is_file():
            raise
        text = manifest.read_text(encoding="utf-8")
    projects = re.findall(r"(?ms)^\s*\[project\]\s*\n(.*?)(?=^\s*\[|\Z)", text)
    fields = {}
    if len(projects) == 1:
        for key in ("name", "version"):
            matches = re.findall(
                rf"""(?m)^\s*{key}\s*=\s*(["'])([^"'\r\n]+)\1\s*(?:#.*)?$""",
                projects[0],
            )
            if len(matches) == 1:
                fields[key] = matches[0][1]
    if fields.get("name") != "agent-chat-service" or re.fullmatch(
        r"\d+\.\d+\.\d+(?:-dev\d+)?", fields.get("version", ""),
    ) is None:
        raise ValueError("Chat source project version metadata is invalid.")
    return fields["version"]


__version__ = _resolve_version()

from .store import ChatError, ChatStore, json_bytes, strict_json
from .client import Client, ClientError

__all__ = ["ChatError", "ChatStore", "Client", "ClientError", "json_bytes", "strict_json"]
