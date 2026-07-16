"""CLI->MCP tool sidecars: parse, gate by scope, and bind params to an argv.

The ``cli`` transport (:mod:`agent_mcp.transports.cli`) exposes a curated set of
**native CLIs** as MCP tools -- the inverse of ``materialize`` (which projects an
upstream *MCP* into CLI stubs). Each tool is declared by a **sidecar** Markdown
file whose YAML frontmatter carries an ``mcp:`` block:

    ---
    mcp:
      name: vei_search
      description: Semantic search across the monorepo, logs, and Gitea via VEI.
      scope: shared                 # optional execution-policy tag
      inputSchema:                  # raw MCP inputSchema (same shape materialize plates)
        type: object
        properties:
          query: { type: string, description: Search text }
          limit: { type: integer, default: 10 }
        required: [query]
      invoke:                       # how to turn params into an argv (never a shell string)
        command: vei-search
        args:
          - "{query}"                       # required positional (bare string)
          - { flag: "--limit", value: "{limit}", when: limit }   # optional flag
    ---
    # vei-search  (human doc body -- ignored by the bridge)

The MCP face (``name``/``description``/``inputSchema``) mirrors a materialized
sidecar so an agent sees the same contract either way. The **exec face** is the
one thing a native CLI needs beyond a materialized stub: an explicit
``invoke``/argv template. Binding produces an **argv array** and the transport
spawns it with no shell, so a param value can never inject a command.

Argv binding rules (deliberately small and unambiguous):

* A **bare string** entry is a required token (positional or literal). ``{name}``
  placeholders are substituted from the arguments; a referenced param that is
  absent is an error (use the mapping form with ``when`` for optional args).
* A **mapping** entry ``{flag?, value?, when?, repeat?}``:
  - ``when``: skip the whole entry unless that param is present and non-null.
  - ``repeat``: the named param is a list; emit ``flag``+value once per item.
  - ``flag`` with no ``value``: boolean-as-presence -- emit the flag alone.
  - ``flag``+``value`` or ``value`` alone: emit them (flag first) with ``value``
    substituted. Each substituted value is a single argv token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CliToolError(ValueError):
    """A sidecar is malformed, or a call cannot be bound to an argv."""


@dataclass
class CliTool:
    """One native CLI exposed as an MCP tool, parsed from a sidecar."""

    name: str
    description: str
    input_schema: dict[str, Any]
    command: str
    args: list[Any]
    scope: str | None = None
    source: Path | None = None
    output_schema: dict[str, Any] | None = None

    def mcp_dict(self) -> dict[str, Any]:
        """The ``tools/list`` entry for this tool (name/description/inputSchema)."""
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema or {"type": "object"},
        }
        if self.output_schema:
            out["outputSchema"] = self.output_schema
        return out


# ---------------------------------------------------------------------------
# Sidecar parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract the leading YAML frontmatter block (between ``---`` fences).

    Returns the parsed mapping, or ``{}`` when there is no frontmatter.
    """
    if not text.startswith("---"):
        return {}
    # Split on the closing fence: lines[0] is the opening ``---``.
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise CliToolError(f"invalid frontmatter YAML: {exc}") from exc
    return data if isinstance(data, dict) else {}


def parse_sidecar(text: str, *, source: Path | None = None) -> CliTool:
    """Build a :class:`CliTool` from one sidecar file's contents."""
    fm = parse_frontmatter(text)
    mcp = fm.get("mcp")
    where = f" ({source})" if source else ""
    if not isinstance(mcp, dict):
        raise CliToolError(f"sidecar has no 'mcp:' frontmatter block{where}")

    name = mcp.get("name")
    if not isinstance(name, str) or not name:
        raise CliToolError(f"mcp.name is required{where}")

    invoke = mcp.get("invoke")
    if not isinstance(invoke, dict):
        raise CliToolError(f"mcp.invoke is required{where}")
    command = invoke.get("command")
    if not isinstance(command, str) or not command:
        raise CliToolError(f"mcp.invoke.command is required{where}")
    raw_args = invoke.get("args", [])
    if not isinstance(raw_args, list):
        raise CliToolError(f"mcp.invoke.args must be a list{where}")

    schema = mcp.get("inputSchema")
    if schema is not None and not isinstance(schema, dict):
        raise CliToolError(f"mcp.inputSchema must be a mapping{where}")
    out_schema = mcp.get("outputSchema")
    if out_schema is not None and not isinstance(out_schema, dict):
        raise CliToolError(f"mcp.outputSchema must be a mapping{where}")

    scope = mcp.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise CliToolError(f"mcp.scope must be a string{where}")

    return CliTool(
        name=name,
        description=str(mcp.get("description", "")).strip(),
        input_schema=schema or {"type": "object"},
        command=command,
        args=list(raw_args),
        scope=scope,
        source=source,
        output_schema=out_schema,
    )


def load_cli_tools(paths: list[str], *, base_dir: Path | None = None) -> list[CliTool]:
    """Load and parse each sidecar path (relative paths resolve against base_dir).

    Names must be unique across the set; a duplicate is a configuration error.
    """
    tools: list[CliTool] = []
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.is_absolute() and base_dir is not None:
            p = base_dir / p
        if not p.exists():
            raise CliToolError(f"sidecar not found: {p}")
        tool = parse_sidecar(p.read_text(encoding="utf-8"), source=p)
        if tool.name in seen:
            raise CliToolError(f"duplicate tool name '{tool.name}' ({p})")
        seen.add(tool.name)
        tools.append(tool)
    return tools


# ---------------------------------------------------------------------------
# Scope gating (generic execution policy)
# ---------------------------------------------------------------------------

def tool_in_scope(tool: CliTool, scopes: list[str]) -> bool:
    """Whether ``tool`` may run given the host's allowed ``scopes``.

    An untagged tool (``scope`` unset) is always allowed. When ``scopes`` is
    empty there is no gating and every tool is allowed. Otherwise the tool's
    ``scope`` must appear in ``scopes``.
    """
    if not scopes or tool.scope is None:
        return True
    return tool.scope in scopes


# ---------------------------------------------------------------------------
# Param -> argv binding
# ---------------------------------------------------------------------------

def _subst(template: str, arguments: dict[str, Any]) -> str:
    """Substitute ``{name}`` placeholders; a missing referenced param is an error."""
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in arguments or arguments[key] is None:
            raise CliToolError(f"missing value for '{{{key}}}'")
        return str(arguments[key])

    return _PLACEHOLDER.sub(repl, template)


def build_argv(tool: CliTool, arguments: dict[str, Any]) -> list[str]:
    """Bind ``arguments`` to a concrete argv for ``tool`` (argv[0] = command)."""
    if arguments is None:
        arguments = {}
    argv: list[str] = [tool.command]
    for entry in tool.args:
        if isinstance(entry, str):
            argv.append(_subst(entry, arguments))
            continue
        if not isinstance(entry, dict):
            raise CliToolError(f"invalid invoke arg entry: {entry!r}")

        when = entry.get("when")
        if when is not None and (when not in arguments or arguments[when] is None):
            continue

        flag = entry.get("flag")
        repeat = entry.get("repeat")
        value = entry.get("value")

        if repeat is not None:
            items = arguments.get(repeat)
            if items is None:
                continue
            if not isinstance(items, (list, tuple)):
                raise CliToolError(f"'{repeat}' must be a list for repeat")
            for item in items:
                if flag is not None:
                    argv.append(str(flag))
                argv.append(str(item))
            continue

        if value is not None:
            rendered = _subst(str(value), arguments)
            if flag is not None:
                argv.append(str(flag))
            argv.append(rendered)
        elif flag is not None:
            argv.append(str(flag))
        else:
            raise CliToolError(f"invoke arg entry needs 'flag', 'value', or 'repeat': {entry!r}")
    return argv
