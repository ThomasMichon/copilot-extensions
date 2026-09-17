from __future__ import annotations

import re
from pathlib import Path

from scan_plugin_sources import PluginSource
from scan_skills import (
    get_field,
    get_field_block,
    has_mcp_fallback,
    has_mcp_troubleshooting_skill,
    readme_documents_dependencies,
    split_frontmatter,
)

BLOCKING = "blocking"
WARNING = "warning"


def plugin_root_for_agent(
    root: Path,
    agent_file: Path,
    source: PluginSource | None,
) -> Path | None:
    """Return the package root for a plugin-owned agent, else None."""
    if source is not None:
        return source.payload_root
    candidate = agent_file.parent.parent
    if candidate.parent.resolve() == (root.resolve() / "plugins"):
        return candidate
    return None


def frontmatter_tool_names(frontmatter: str) -> set[str] | None:
    """Return normalized tool names, or None when tools are unrestricted."""
    if not re.search(r"(?im)^tools\s*:", frontmatter):
        return None
    raw = get_field_block(frontmatter, "tools")
    without_comments = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    return {
        token.lower()
        for token in re.findall(
            r"[A-Za-z*][A-Za-z0-9_.*:/-]*",
            without_comments,
        )
    }


def agent_can_invoke_task(frontmatter: str) -> bool:
    """Whether an agent's declared tool surface includes the Task/agent tool."""
    tools = frontmatter_tool_names(frontmatter)
    return tools is None or bool({"*", "agent", "task"} & tools)


def has_anti_self_delegation(text: str, agent_name: str) -> bool:
    """Require an explicit do-not-spawn/delegate line naming this agent type."""
    flat = re.sub(r"\s+", " ", text)
    name = re.escape(agent_name.strip().strip("'\""))
    if not name:
        return False
    return bool(
        re.search(
            rf"(?i)do\s+not\b.{{0,160}}"
            rf"(?:task\s+tool|spawn|delegate)\b.{{0,160}}"
            rf"(?:another\s+)?[`'\"]?{name}[`'\"]?\s+agent\b",
            flat,
        )
    )


def resolve_owned_agent_roots(
    root: Path,
    values: list[str] | None,
) -> tuple[Path, ...]:
    """Validate explicit repo-owned agent directories."""
    repo = root.resolve(strict=True)
    resolved_roots: set[Path] = set()
    for raw in values or []:
        relative = Path(raw)
        if not raw.strip() or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                f"--owned-agent-root {raw!r} must be a non-empty "
                "repository-relative directory without '..'",
            )
        candidate = repo / relative
        if not candidate.exists():
            raise ValueError(f"--owned-agent-root {raw!r} does not exist")
        if candidate.is_symlink():
            raise ValueError(f"--owned-agent-root {raw!r} must not be a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(repo)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"--owned-agent-root {raw!r} must resolve inside the repository"
            ) from exc
        if resolved == repo or not resolved.is_dir():
            raise ValueError(
                f"--owned-agent-root {raw!r} must name a directory below "
                "the repository root",
            )
        for agent_file in resolved.glob("*.agent.md"):
            if agent_file.is_symlink():
                raise ValueError(
                    f"--owned-agent-root {raw!r} contains symlinked agent "
                    f"{agent_file.name!r}",
                )
            try:
                agent_file.resolve(strict=True).relative_to(resolved)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"--owned-agent-root {raw!r} contains an agent outside "
                    "the declared directory",
                ) from exc
        resolved_roots.add(resolved)
    return tuple(sorted(resolved_roots, key=str))


def repo_owned_agent_files(
    root: Path,
    owned_agent_roots: tuple[Path, ...] = (),
) -> set[Path]:
    """Return standard and explicitly declared repository-owned agents."""
    files = (
        set(root.glob(".github/agents/*.agent.md"))
        | set(root.glob(".claude/agents/*.agent.md"))
    )
    for agent_root in owned_agent_roots:
        files.update(agent_root.glob("*.agent.md"))
    return {path.resolve() for path in files if path.is_file()}


def scan_agents(
    root: Path,
    report,
    plugin_sources: list[PluginSource] | None = None,
    owned_agent_roots: tuple[Path, ...] = (),
) -> None:
    agent_files: dict[Path, PluginSource | None] = {}
    owned_plugin_agents: set[tuple[str, str]] = set()
    checked_mcp_plugins: set[Path] = set()
    for af in repo_owned_agent_files(root, owned_agent_roots):
        agent_files[af.resolve()] = None
    for af in root.glob("plugins/*/agents/*.agent.md"):
        agent_files[af.resolve()] = None
        owned_plugin_agents.add((af.parent.parent.name, af.name))
    for source in sorted(plugin_sources or [], key=lambda item: not item.controlled):
        for af in source.payload_root.glob("agents/*.agent.md"):
            plugin_name = source.origin.rsplit("/", 1)[-1]
            logical_key = (plugin_name, af.name)
            if not source.controlled and logical_key in owned_plugin_agents:
                continue
            resolved = af.resolve()
            if resolved not in agent_files:
                agent_files[resolved] = source
            elif source.controlled and agent_files[resolved] is not None:
                agent_files[resolved] = source
            if source.controlled:
                owned_plugin_agents.add(logical_key)

    for af, source in sorted(agent_files.items(), key=lambda item: str(item[0])):
        external = source is not None and not source.controlled
        severity = WARNING if external else BLOCKING
        if external:
            identity = source.origin
            if source.version:
                identity += f"@{source.version}"
            else:
                identity += "@<unknown-version>"
            path: Path | str = f"<plugin:{identity}>/agents/{af.name}"
            upstream = source.source or f"the `{source.origin}` marketplace source"
            suffix = (
                f" External enabled plugin `{identity}` is advisory because this "
                f"repo cannot edit its installed payload. Disable or configure "
                f"the plugin here, or fix it upstream at {upstream} using that "
                f"repo's contribution workflow (prefer its `<repo>-harness` "
                f"contributing skill when enabled)."
            )
        else:
            path = af
            suffix = ""

        def add(check: str, message: str) -> None:
            report.add(severity, check, path, message + suffix)

        text = af.read_text(encoding="utf-8", errors="replace")
        frontmatter_body = split_frontmatter(text)
        if frontmatter_body is None:
            add("agent-frontmatter", ".agent.md has no YAML frontmatter (--- block)")
            continue
        frontmatter, body = frontmatter_body
        if "description" not in frontmatter.lower():
            add("agent-frontmatter", "frontmatter missing `description`")

        declared_name = get_field(frontmatter, "name")
        agent_name = (
            declared_name.strip().strip("'\"")
            if declared_name
            else af.name.removesuffix(".agent.md")
        )
        has_mcp = bool(re.search(r"(?im)^\s*mcp-servers\s*:", frontmatter))
        plugin_root = plugin_root_for_agent(root, af, source)
        if has_mcp and plugin_root is not None:
            plugin_key = plugin_root.resolve()
            if plugin_key not in checked_mcp_plugins:
                checked_mcp_plugins.add(plugin_key)
                if not has_mcp_troubleshooting_skill(plugin_root):
                    add(
                        "mcp-troubleshooting-skill",
                        "plugin packages an MCP-owning agent but has no "
                        "discoverable troubleshooting skill whose name or "
                        "description identifies setup/diagnosis/repair and "
                        "names the MCP or bridge failure path",
                    )
                if not readme_documents_dependencies(plugin_root):
                    add(
                        "plugin-readme-dependencies",
                        "plugin packages an MCP-owning agent but its README.md "
                        "has no explicit Dependencies, Prerequisites, or "
                        "Requirements section",
                    )
        readiness_match = re.search(
            r"(?ims)^##\s+MCP\s+Readiness\b(.*?)(?=^##\s|\Z)",
            body,
        )
        readiness = re.sub(
            r"\s+",
            " ",
            readiness_match.group(1) if readiness_match else "",
        )

        if has_mcp and readiness_match is None:
            add(
                "mcp-readiness",
                "declares mcp-servers but has no `## MCP Readiness` section "
                "(probe one tool on startup and preserve the exact error)",
            )

        if agent_can_invoke_task(frontmatter):
            anti_scope = readiness if has_mcp else body
            if not has_anti_self_delegation(anti_scope, agent_name):
                location = " in `## MCP Readiness`" if has_mcp else ""
                add(
                    "anti-recursion",
                    "Task-capable agent has no agent-specific anti-self-"
                    f"delegation line{location} (use: \"Do NOT use the task "
                    f"tool to spawn another `{agent_name}` agent.\")",
                )

        uses_agent_mcp = bool(
            re.search(
                r"(?im)^\s*command\s*:\s*['\"]?agent-mcp['\"]?"
                r"\s*(?:#.*)?$",
                frontmatter,
            )
        )
        if uses_agent_mcp and not has_mcp_fallback(readiness):
            add(
                "mcp-fallback",
                "uses agent-mcp but has no equivalent materialized CLI "
                "fallback over the same bridge config",
            )
