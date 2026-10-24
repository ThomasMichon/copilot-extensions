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
        declarations = {"name": [], "version": []}
        for key, value in re.findall(r"(?m)^[ \t]*([^=\r\n]+)=(.*)$", projects[0]):
            key = key.strip()
            if key.startswith("#"):
                continue
            if re.fullmatch(r"""[A-Za-z0-9_-]+|"[A-Za-z0-9_-]+"|'[A-Za-z0-9_-]+'""", key) is None:
                raise ValueError("Chat source project version metadata is invalid.")
            key = key.strip("\"'")
            if key in declarations:
                declarations[key].append(value)
        for key in ("name", "version"):
            if len(declarations[key]) == 1:
                match = re.fullmatch(
                    r"""\s*(["'])([^"'\r\n]+)\1\s*(?:#.*)?""", declarations[key][0],
                )
                if match:
                    fields[key] = match[2]
    if fields.get("name") != "agent-chat-service" or re.fullmatch(
        r"\d+\.\d+\.\d+(?:-dev\d+)?", fields.get("version", ""),
    ) is None:
        raise ValueError("Chat source project version metadata is invalid.")
    return fields["version"]


__version__ = _resolve_version()

from .store import ChatError, ChatStore, json_bytes, strict_json
from .client import Client, ClientError

__all__ = ["ChatError", "ChatStore", "Client", "ClientError", "json_bytes", "strict_json"]
