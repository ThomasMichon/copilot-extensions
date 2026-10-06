from __future__ import annotations

import re
from pathlib import Path

from scan_plugin_sources import PluginSource, _plugin_declares_agents, _plugin_manifest_path
from scan_skills import (
    get_field,
    get_field_block,
    has_disabled_mcp_fallback_marker,
    has_mcp_fallback,
    has_mcp_troubleshooting_skill,
    readme_documents_dependencies,
    split_frontmatter,
)

# Built-in Copilot CLI tool name that grants shell/PowerShell execution --
# the materialized-CLI-fallback recipe (invoking a materialized stub via
# `.ps1`/`.cmd`) is unusable without it.
SHELL_EXECUTION_TOOL = "execute"

# Inline-comment escape hatch for a deliberately narrowed per-server tools
# allow-list -- same convention as tools/check-headless-launch.py's own
# `headless-guard: allow <reason>` marker.
_MCP_TOOLS_ALLOW_MARKER = "mcp-tools-allowlist: allow"

BLOCKING = "blocking"
WARNING = "warning"


def _mcp_tools_allow_reason(raw_value: str) -> str | None:
    """Return the stated reason if ``raw_value`` carries the allow marker.

    Mirrors ``check-headless-launch.py``'s ``_allowed`` comment contract: the
    marker must be followed by ``:``/space and a non-empty reason, or it does
    not count -- a bare marker with no reason still trips the finding.
    """
    if "#" not in raw_value:
        return None
    comment = raw_value.split("#", 1)[1].strip()
    if not comment.startswith(_MCP_TOOLS_ALLOW_MARKER):
        return None
    suffix = comment[len(_MCP_TOOLS_ALLOW_MARKER):]
    if not suffix or suffix[0] not in " :":
        return None
    reason = suffix.lstrip(" :").strip()
    return reason or None


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


def agent_can_invoke_shell(frontmatter: str) -> bool:
    """Whether an agent's declared tool surface can run shell/PowerShell.

    Unrestricted tools (`None`) always can. A restricted `tools:` allow-list
    must name `execute` (or a wildcard) explicitly -- the materialized-CLI-
    fallback recipe shells out to a `.ps1`/`.cmd` stub and is unusable
    otherwise, no matter how thoroughly the agent body documents it.
    """
    tools = frontmatter_tool_names(frontmatter)
    return tools is None or bool({"*", SHELL_EXECUTION_TOOL} & tools)


def mcp_server_tool_entries(frontmatter: str) -> list[tuple[str, str]]:
    """Return ``(server_name, raw tools value)`` for each ``mcp-servers.<name>.tools``.

    Walks the ``mcp-servers:`` block by indentation (this scanner stays
    regex/indentation-based rather than taking a YAML dependency, matching
    every other parser in this module): a line at the block's own indent
    that is just ``<name>:`` opens a server entry. Within a server entry,
    only a line at that server's own **direct-field** indent (the indent of
    its first field, e.g. ``type``/``command``) is considered for a
    ``tools:`` match -- a more deeply indented line (a nested ``env:``
    mapping's own children, for example) is skipped, so a coincidentally
    named nested key can never be mistaken for the server's own ``tools``
    field. Both an inline value (``tools: ["*"]``) and a block sequence
    (``tools:`` followed by indented ``- name`` items) are captured; a
    folded/multi-line scalar is not, since every shipped example writes this
    field as one of those two short forms.
    """
    lines = frontmatter.splitlines()
    out: list[tuple[str, str]] = []
    in_mcp_servers = False
    mcp_indent = 0
    server_name: str | None = None
    server_indent: int | None = None
    field_indent: int | None = None
    pending_indent: int | None = None
    pending_items: list[str] = []
    pending_comment = ""

    def flush_pending() -> None:
        nonlocal pending_indent, pending_items, pending_comment
        if server_name is not None and pending_items:
            joined = " ".join(pending_items)
            if pending_comment:
                joined += "  " + pending_comment
            out.append((server_name, joined))
        pending_indent = None
        pending_items = []
        pending_comment = ""

    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if pending_indent is not None:
            if stripped.startswith("#") and indent > pending_indent:
                continue  # a comment inside the pending sequence block
            seq_match = re.match(r"^-\s*(.+)$", stripped)
            if seq_match and indent > pending_indent:
                pending_items.append(seq_match.group(1).strip())
                continue
            flush_pending()

        if indent == 0 and re.match(r"(?i)^mcp-servers\s*:\s*(?:#.*)?$", stripped):
            in_mcp_servers = True
            mcp_indent = indent
            server_name = None
            server_indent = None
            field_indent = None
            continue
        if not in_mcp_servers:
            continue
        if indent <= mcp_indent:
            in_mcp_servers = False
            server_name = None
            server_indent = None
            field_indent = None
            continue

        server_match = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(?:#.*)?$", stripped)
        if server_match and (server_indent is None or indent <= server_indent):
            server_name = server_match.group(1)
            server_indent = indent
            field_indent = None
            continue
        if server_name is None:
            continue
        if field_indent is None:
            field_indent = indent
        if indent != field_indent:
            continue  # more deeply nested than the server's own fields

        tools_match = re.match(r"(?i)^tools\s*:\s*(.*)$", stripped)
        if not tools_match:
            continue
        value = tools_match.group(1).strip()
        if value and not value.startswith("#"):
            out.append((server_name, value))
        else:
            pending_indent = indent
            pending_items = []
            pending_comment = value if value.startswith("#") else ""
    flush_pending()
    return out


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
    checked_agent_manifest_plugins: set[Path] = set()
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

        plugin_root = plugin_root_for_agent(root, af, source)
        if plugin_root is not None:
            plugin_key = plugin_root.resolve()
            if plugin_key not in checked_agent_manifest_plugins:
                checked_agent_manifest_plugins.add(plugin_key)
                if not _plugin_declares_agents(plugin_root):
                    add(
                        "agent-manifest-declaration",
                        "plugin ships agents/*.agent.md but its manifest "
                        f"({_plugin_manifest_path(plugin_root)}) does not "
                        'declare a truthy top-level `agents` field (e.g. '
                        '`"agents": "agents/"`) -- the runtime currently '
                        "falls back to `plugin_root/agents` when this is "
                        "absent, but declare it explicitly anyway: it matches "
                        "every shipped example and is more robust than "
                        "relying on an implicit default",
                    )

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
        if uses_agent_mcp and has_disabled_mcp_fallback_marker(readiness):
            add(
                "mcp-fallback-disabled",
                "carries the obsolete 'materialized cli fallback: disabled "
                "by the authorization/conditional gate' marker -- an "
                "agent-mcp bridge's own decorators (filter/transform/gate) "
                "run inside agent-mcp's bridge runtime and are enforced "
                "identically no matter which surface calls them (the native "
                "attached catalog, `agent-mcp call`, or a materialized "
                "stub), so disabling the fallback buys no additional safety "
                "-- it only leaves the agent with zero recourse when the "
                "native catalog fails to register in-session (a Copilot "
                "CLI-side extension/session-registration gap with no "
                "in-session repair). Replace it with the enabled "
                "materialize/call recipe from `defining-subagents`'s MCP "
                "Readiness section.",
            )
        elif uses_agent_mcp and not has_mcp_fallback(readiness):
            add(
                "mcp-fallback",
                "uses agent-mcp but has no equivalent materialized CLI "
                "fallback over the same bridge config",
            )

        if (
            uses_agent_mcp
            and has_mcp_fallback(readiness)
            and not agent_can_invoke_shell(frontmatter)
        ):
            add(
                "mcp-fallback-needs-shell-tool",
                "documents a materialized CLI fallback, but the frontmatter's "
                f"restricted `tools:` list has no `{SHELL_EXECUTION_TOOL}` (or "
                "`*`) entry -- the fallback shells out to a `.ps1`/`.cmd` "
                "stub and is unusable without shell/PowerShell execution, no "
                "matter how thoroughly the body documents it",
            )

        for server_name, raw_tools in mcp_server_tool_entries(frontmatter):
            without_comments = raw_tools.split("#", 1)[0]
            tokens = {
                token.lower()
                for token in re.findall(r"[A-Za-z*][A-Za-z0-9_.*:/-]*", without_comments)
            }
            if "*" in tokens or _mcp_tools_allow_reason(raw_tools):
                continue
            rendered = ", ".join(sorted(tokens)) if tokens else "(none)"
            add(
                "mcp-server-tools-allowlist",
                f"mcp-servers.{server_name}.tools is a hand-enumerated "
                f"list ({rendered}) instead of [\"*\"] "
                "-- the upstream server's own tool catalog can add, "
                "rename, or retire tools independent of this repo's "
                "release cycle, and a stale/misspelled entry can make "
                "the whole allow-list reject every name in it, silently "
                "denying the agent that server's tools entirely (a real "
                "regression seen in the wild: two agents hand-"
                "enumerating one MCP server's tools drifted out of sync "
                "with its catalog and broke MCP session startup for "
                "both). Use [\"*\"] unless withholding one specific tool "
                "for a documented reason (add a trailing "
                "`# mcp-tools-allowlist: allow <reason>` comment to "
                "suppress this finding).",
            )
