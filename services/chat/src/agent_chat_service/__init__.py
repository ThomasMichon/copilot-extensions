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
    key_pattern = r"""[A-Za-z0-9_-]+|"[A-Za-z0-9_-]+"|'[A-Za-z0-9_-]+'"""
    if '"""' in text or "'''" in text:
        raise ValueError("Chat source project version metadata is invalid.")
    projects = []
    section = None
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            header = re.fullmatch(r"[ \t]*\[(.+?)\][ \t]*(?:#.*)?", line)
            if header is None or any(
                re.fullmatch(key_pattern, key.strip()) is None for key in header[1].split(".")
            ):
                raise ValueError("Chat source project version metadata is invalid.")
            keys = [key.strip().strip("\"'") for key in header[1].split(".")]
            section = None
            if keys == ["project"]:
                section = []
                projects.append(section)
        elif section is not None:
            section.append(line)
    fields = {}
    if len(projects) == 1:
        declarations = {"name": [], "version": []}
        for key, value in re.findall(r"(?m)^[ \t]*([^=\r\n]+)=(.*)$", "\n".join(projects[0])):
            key = key.strip()
            if key.startswith("#"):
                continue
            if re.fullmatch(key_pattern, key) is None:
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
