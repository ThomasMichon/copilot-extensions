"""MCP configuration candidates, without server execution or credential output."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_MCP_SERVERS = 1024
MAX_MCP_TOOL_NAMES = 8192
MAX_MCP_NAME_CHARACTERS = 256


def collect_mcp_servers(
    directory: Path, root: Path, config_root: Path, plugins: list,
    read: Callable[[Path], str | None], can_read: Callable[[Path], bool],
    parse: Callable[..., Any], warnings: list[dict],
) -> tuple[list[dict], bool]:
    records: list[dict] = []
    documents: dict[Path, dict | None] = {}
    seen_names: set[str] = set()
    warning_keys: set[tuple[str, str]] = set()
    complete = True
    tool_names_retained = 0

    def warn(kind: str, source: str) -> None:
        nonlocal complete
        complete = False
        key = (kind, source)
        if key not in warning_keys:
            warnings.append({"kind": kind, "path": source})
            warning_keys.add(key)

    def document(path: Path) -> dict | None:
        path = path.resolve()
        if path in documents:
            return documents[path]
        if not can_read(path):
            warn("mcp_shared_read_budget_exhausted", str(path))
            return None
        text = read(path)
        if text is None:
            warn("mcp_config_unavailable", str(path))
            documents[path] = None
            return None
        try:
            value = parse(text, path, allow_line_comments=True)
            if not isinstance(value, dict):
                raise ValueError("mcp_document_not_object")
        except ValueError:
            warn("mcp_config_invalid_json", str(path))
            documents[path] = None
            return None
        documents[path] = value
        return value

    def add_servers(servers: Any, source: str, *, layered: bool) -> None:
        nonlocal tool_names_retained
        if not isinstance(servers, dict):
            warn("mcp_servers_not_object", source)
            return
        for name, definition in servers.items():
            if not isinstance(name, str) or not isinstance(definition, dict):
                warn("mcp_server_invalid", source)
                continue
            if len(name) > MAX_MCP_NAME_CHARACTERS:
                warn("mcp_server_name_inventory_limit", source)
                continue
            if layered and name in seen_names:
                continue
            if len(records) >= MAX_MCP_SERVERS:
                warn("mcp_server_inventory_truncated", source)
                break
            tools = definition.get("tools", [])
            names: list[str] = []
            seen_tools: set[str] = set()
            names_complete = True
            if not isinstance(tools, list):
                warn("mcp_tool_names_not_list", source)
                names_complete = False
                tools = []
            for tool in tools:
                if not isinstance(tool, str):
                    warn("mcp_tool_name_invalid", source)
                    names_complete = False
                    continue
                if len(tool) > MAX_MCP_NAME_CHARACTERS:
                    warn("mcp_tool_name_inventory_limit", source)
                    names_complete = False
                    continue
                if any(char in tool for char in "*?[]") or tool in seen_tools:
                    continue
                if tool_names_retained >= MAX_MCP_TOOL_NAMES:
                    warn("mcp_tool_name_inventory_truncated", source)
                    names_complete = False
                    break
                seen_tools.add(tool)
                names.append(tool)
                tool_names_retained += 1
            records.append({
                "name": name, "tools": names, "source": source,
                "tool_names_complete": names_complete,
            })
            if layered:
                seen_names.add(name)

    def file_servers(
        path: Path, *, layered: bool, declared: bool = False, allow_bare: bool = True,
    ) -> None:
        if not path.is_file():
            if declared or path.exists():
                warn("mcp_declared_file_unavailable", str(path))
            return
        value = document(path)
        if value is not None:
            if value and not allow_bare and "mcpServers" not in value:
                warn("mcp_global_schema_unrecognized", str(path))
                return
            add_servers(value.get("mcpServers", value), str(path), layered=layered)

    # Closest project definitions win; .mcp.json wins over .github/mcp.json.
    current = directory.resolve(strict=True)
    while True:
        primary = current / ".mcp.json"
        alternate = current / ".github" / "mcp.json"
        file_servers(primary if primary.exists() else alternate, layered=True)
        if current == root or current.parent == current:
            break
        current = current.parent
    file_servers(config_root / "mcp-config.json", layered=True, allow_bare=False)

    for plugin in plugins:
        manifests = (
            plugin.payload_root / "plugin.json",
            plugin.payload_root / ".claude-plugin" / "plugin.json",
        )
        manifest_path = next((path for path in manifests if path.is_file()), None)
        if manifest_path is not None and not can_read(manifest_path):
            warn("mcp_shared_read_budget_exhausted", str(plugin.payload_root))
            break
        manifest = document(manifest_path) if manifest_path is not None else {}
        if manifest is None:
            continue
        declaration = manifest.get("mcpServers", ".mcp.json")
        if isinstance(declaration, dict):
            add_servers(
                declaration, str(manifest_path) + "#/mcpServers", layered=False,
            )
        elif isinstance(declaration, str):
            configured = Path(declaration).expanduser()
            path = configured if configured.is_absolute() else plugin.payload_root / configured
            file_servers(path, layered=False, declared="mcpServers" in manifest)
        else:
            warn("mcp_declaration_invalid", str(manifest_path))
    return records, complete
