"""Bounded local candidate inventory; presence must still be matched to the wire."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys

MAX_FILES = 8192
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def scanner_module(name: str):
    root = Path(__file__).resolve().parents[2] / "reviewing-customizations" / "scripts"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module(name)


def copilot_home(
    home: Path | None = None, environ: dict[str, str] | None = None, *,
    working_directory: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get("COPILOT_HOME")
    if not configured:
        return (home or Path.home()) / ".copilot"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (working_directory or Path.cwd()) / path
    return path.resolve()


def read_settings(path: Path) -> dict:
    return scanner_module("scan_plugin_sources")._strict_settings(path, allow_line_comments=True)


def repository_root(directory: Path) -> Path:
    resolved = directory.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("working_directory_not_directory")
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved


def collect_inventory(
    directory: Path, *, home: Path | None = None, config_root: Path | None = None,
    session_id: str | None = None, agent_worktrees_command: str | None = None,
) -> dict:
    home = home or Path.home()
    config_root = config_root or copilot_home(home, working_directory=directory)
    if agent_worktrees_command is not None:
        command = Path(agent_worktrees_command).expanduser()
        if not command.is_absolute() or not command.is_file():
            raise ValueError("catalog_command_must_be_existing_absolute_path")
        agent_worktrees_command = str(command)
    root = repository_root(directory)
    source_reader = scanner_module("scan_plugin_sources")
    budget = scanner_module("context_budget")
    skill_reader = scanner_module("scan_skills")
    warnings: list[dict] = []
    total_bytes = 0
    file_count = 0
    cache: dict[Path, str | None] = {}

    def read(path: Path) -> str | None:
        nonlocal total_bytes, file_count
        path = path.resolve()
        if path in cache:
            return cache[path]
        cache[path] = None
        remaining = MAX_TOTAL_BYTES - total_bytes
        if file_count >= MAX_FILES or remaining <= 0:
            warnings.append({"kind": "inventory_read_budget_exhausted", "path": str(path)})
            return None
        try:
            if not path.is_file():
                return None
            file_count += 1
            with path.open("rb") as stream:
                limit = min(MAX_FILE_BYTES, remaining)
                if os.fstat(stream.fileno()).st_size > limit:
                    warnings.append({"kind": "inventory_byte_limit", "path": str(path)})
                    return None
                raw = stream.read(limit)
                grew_beyond_read = os.fstat(stream.fileno()).st_size > len(raw)
            total_bytes += len(raw)
            if grew_beyond_read:
                warnings.append({"kind": "inventory_byte_limit", "path": str(path)})
                return None
            cache[path] = raw.decode("utf-8-sig")
            return cache[path]
        except (OSError, UnicodeError) as error:
            warnings.append({
                "kind": "inventory_file_unreadable", "path": str(path),
                "error_type": type(error).__name__,
            })
            return None

    inventory: dict = {
        "instructions": [], "skills": [], "agents": [], "mcp_servers": [],
        "warnings": warnings,
        "coverage": {
            "basis": "filesystem_candidates_at_analysis_time",
            "working_directory": str(directory.resolve()),
            "repository": str(root),
            "hook_execution": False, "server_connections": False,
            "session_instruction_inventory": "selected_session" if session_id else "not_requested",
        },
    }
    try:
        plugins = source_reader.assemble_enabled_plugins(
            root, home=home, copilot_home=config_root, strict=True,
            include_metadata=False, agent_worktrees_command=agent_worktrees_command,
            allow_legacy_command=False,
            settings_line_comments=True,
            read_text=read,
        )
    except (OSError, ValueError) as error:
        warnings.append({"kind": "plugin_discovery_failed", "error_type": type(error).__name__})
        plugins = []
    for plugin in plugins:
        if plugin.error:
            warnings.append({"kind": "plugin_source_unavailable", "plugin": plugin.origin})
    plugins = [plugin for plugin in plugins if not plugin.error]
    always, conditional = budget._repo_instruction_files(root)
    personal = budget._personal_instruction_files(home, copilot_home=config_root)
    custom, _aliases = budget._custom_instruction_files(home, base_directory=directory)
    instructions = always | conditional | personal | custom
    if session_id is not None:
        from uuid import UUID

        if str(UUID(session_id)) != session_id:
            raise ValueError("invalid_session_id")
        session_instructions = config_root / "session-state" / session_id / "instructions"
        if session_instructions.is_dir():
            instructions.update(session_instructions.rglob("*.instructions.md"))
    metadata = budget._metadata_files(root, plugins)
    for folder in (config_root / "skills", home / ".agents" / "skills"):
        metadata.update(folder.glob("*/SKILL.md"))
    for folder in (config_root / "agents", home / ".claude" / "agents"):
        metadata.update(folder.glob("*.agent.md"))
    origins = {plugin.payload_root.resolve(): plugin.plugin_name for plugin in plugins}
    for path in sorted(instructions, key=str):
        if file_count >= MAX_FILES or total_bytes >= MAX_TOTAL_BYTES:
            warnings.append({"kind": "instruction_inventory_truncated"})
            break
        text = read(path)
        if text is not None:
            inventory["instructions"].append({"path": str(path), "text": text})
    for path in sorted(metadata, key=str):
        if file_count >= MAX_FILES or total_bytes >= MAX_TOTAL_BYTES:
            warnings.append({"kind": "metadata_inventory_truncated"})
            break
        text = read(path)
        if text is None:
            continue
        split = skill_reader.split_frontmatter(text)
        if split is None:
            warnings.append({"kind": "metadata_frontmatter_missing", "path": str(path)})
            continue
        frontmatter = split[0]
        name = skill_reader.get_field(frontmatter, "name") or ""
        if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
            name = name[1:-1]
        if not name and path.name.endswith(".agent.md"):
            name = path.name.removesuffix(".agent.md")
        description = skill_reader.get_field_block(frontmatter, "description").strip()
        if len(description) >= 2 and description[0] == description[-1] and description[0] in "\"'":
            description = description[1:-1]
        if not name:
            warnings.append({"kind": "metadata_name_missing", "path": str(path)})
            continue
        aliases = {name}
        for plugin_root, plugin_name in origins.items():
            if path.resolve().is_relative_to(plugin_root):
                aliases.add(f"{plugin_name}:{name}")
        inventory["skills" if path.name == "SKILL.md" else "agents"].append({
            "path": str(path), "name": name, "description": description,
            "frontmatter": frontmatter, "aliases": sorted(aliases),
        })
    from snapshot_mcp_inventory import collect_mcp_servers

    inventory["mcp_servers"], mcp_complete = collect_mcp_servers(
        directory, root, config_root, plugins, read,
        lambda path: (
            (path.resolve() in cache and cache[path.resolve()] is not None)
            or (file_count < MAX_FILES and total_bytes < MAX_TOTAL_BYTES)
        ),
        source_reader.strict_json_document, warnings,
    )
    mcp_complete = mcp_complete and not any(warning["kind"] in {
        "plugin_discovery_failed", "plugin_source_unavailable",
    } for warning in warnings)
    inventory["coverage"]["mcp_complete"] = mcp_complete
    inventory["coverage"].update(files_examined=file_count, source_bytes_read=total_bytes)
    return inventory
