"""Materialize an upstream MCP as a fleet of CLI stubs + plated sidecars.

``agent-mcp materialize <bridge>`` introspects an upstream MCP (via
:class:`~agent_mcp.client.OneShotSession`) and projects its ``tools/list`` into a
**hierarchical, discoverable, pipeable** command fleet on disk::

    <dest>/<server>/
      bin/
        _amcp-dispatch          (POSIX: one dispatcher; stubs are symlinks to it)
        create-issue -> _amcp-dispatch
        create-issue.ps1        (Windows: two-template shim farm)
        create-issue.cmd
        ...
      doc/
        create-issue.md         (plated sidecar: description + raw inputSchema)
        ...
      index.md                  (the server's tool table)
      manifest.json             (stub -> tool + bridge reference; read by `call`)

Everything here is emitted **mechanically** from the tool definitions -- no LLM
is involved in generation. Sidecars *plate* the raw MCP ``description`` and
``inputSchema``; stubs accept the raw ``arguments`` JSON (no ``--flag``
synthesis) and pass the upstream result through verbatim. A model appears only
at call time, when an agent reads the plated schema and builds the arguments.

The whole tree is rebuilt in a temp directory and swapped into place, so a
re-materialize is atomic and drift-safe (no partial-write window).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .config import BridgeConfig
from .decorators._catalog import render_tools_interface

# One dispatcher script per server; every POSIX stub symlinks to it and it
# dispatches on ``argv[0]`` (busybox / git-multicall style).
DISPATCHER_NAME = "_amcp-dispatch"
MANIFEST_NAME = "manifest.json"

# Characters allowed in an on-disk stub name; anything else becomes ``-``.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def server_name_for(cfg: BridgeConfig, override: str | None = None) -> str:
    """Pick the server namespace: an explicit override, the bridge name, else a
    slug of the upstream URL/command."""
    if override:
        return sanitize_stub(override)
    if cfg.name:
        return sanitize_stub(cfg.name)
    if cfg.server.url:
        host = re.sub(r"^https?://", "", cfg.server.url).split("/")[0]
        return sanitize_stub(host) or "server"
    if cfg.server.npm:
        return sanitize_stub(Path(cfg.server.npm).stem) or "server"
    if cfg.server.command:
        return sanitize_stub(Path(cfg.server.command[0]).stem) or "server"
    return "server"


def sanitize_stub(name: str) -> str:
    """A filesystem/PATH-safe stub name (collapse unsafe runs to ``-``)."""
    cleaned = _UNSAFE.sub("-", name.strip()).strip("-.")
    return cleaned


@dataclass
class MaterializedTool:
    """One tool projected to a stub: its on-disk name and the upstream tool."""

    stub: str
    tool_name: str
    definition: dict


def plan_tools(tools: list[dict]) -> list[MaterializedTool]:
    """Map raw upstream tools to stubs, resolving on-disk name collisions.

    Two upstream tools whose sanitized names collide get a numeric suffix so the
    farm stays a 1:1 file<->tool mapping.
    """
    out: list[MaterializedTool] = []
    used: set[str] = set()
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not isinstance(name, str) or not name:
            continue
        base = sanitize_stub(name) or "tool"
        stub = base
        n = 2
        while stub in used:
            stub = f"{base}-{n}"
            n += 1
        used.add(stub)
        out.append(MaterializedTool(stub=stub, tool_name=name, definition=t))
    return out


# ---------------------------------------------------------------------------
# Rendering (all mechanical -- no LLM)
# ---------------------------------------------------------------------------

def render_sidecar(mt: MaterializedTool, *, server: str, bridge_ref: str) -> str:
    """Render a tool's plated sidecar: description verbatim + raw inputSchema."""
    t = mt.definition
    desc = (t.get("description") or "").strip()
    schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
    out_schema = t.get("outputSchema") if isinstance(t.get("outputSchema"), dict) else None

    lines: list[str] = [f"# {mt.tool_name}", ""]
    if desc:
        lines += [desc, ""]
    lines += [
        f"- **Server:** `{server}`",
        f"- **Stub:** `{mt.stub}`",
        f"- **Bridge:** `{bridge_ref}`",
        f"- **Upstream tool:** `{mt.tool_name}`",
        "",
        "## Input -- raw MCP `arguments` (no flag synthesis)",
        "",
        "Supply the tool's `arguments` object as JSON. Three equivalent forms:",
        "",
        "```sh",
        f"echo '{{ ... }}' | {mt.stub}                 # stdin",
        f"{mt.stub} '{{ ... }}'                         # inline JSON arg",
        f"{mt.stub} --request-file req.json           # a file path (CMD-safe)",
        "```",
        "",
        "A request file holds either the bare `arguments` object or "
        "`{\"arguments\": { ... }}`. Prefer it for nested/multiline args; on "
        "Windows CMD, use it always (a path is the only quoting-proof token).",
        "",
        "### inputSchema",
        "",
        "```json",
        json.dumps(schema, indent=2, sort_keys=False),
        "```",
        "",
        "## Output",
        "",
    ]
    if out_schema is not None:
        lines += [
            "The upstream advertises a structured output schema; the stub emits "
            "the structured content as JSON when present.",
            "",
            "```json",
            json.dumps(out_schema, indent=2, sort_keys=False),
            "```",
            "",
        ]
    else:
        lines += [
            "Raw text passthrough -- the upstream's text content is written to "
            "stdout verbatim. A tool error yields a non-zero exit and a stderr "
            "message; no synthetic JSON envelope is imposed.",
            "",
        ]
    ts = render_tools_interface([t], interface="Tool").strip()
    lines += ["## TypeScript signature", "", "```typescript", ts, "```", ""]
    return "\n".join(lines)


def render_index(server: str, plan: list[MaterializedTool], *, bridge_ref: str) -> str:
    """Render the per-server index: a tool table with one-line descriptions."""
    lines = [
        f"# Materialized MCP: `{server}`",
        "",
        f"Generated by `agent-mcp materialize` from bridge `{bridge_ref}`. "
        f"{len(plan)} tool(s). Each stub accepts the raw MCP `arguments` JSON "
        "and passes the result through verbatim; see the sidecar in `doc/` for "
        "the exact input schema.",
        "",
        "| Stub | Tool | Description |",
        "|------|------|-------------|",
    ]
    for mt in plan:
        desc = (mt.definition.get("description") or "").strip().splitlines()
        first = desc[0] if desc else ""
        first = first.replace("|", "\\|")
        lines.append(f"| `{mt.stub}` | `{mt.tool_name}` | {first} |")
    lines.append("")
    return "\n".join(lines)


def build_manifest(server: str, plan: list[MaterializedTool], *, bridge_ref: str,
                   version: str) -> dict:
    """The stub->tool map + bridge reference that ``agent-mcp call`` reads."""
    return {
        "schema": 1,
        "server": server,
        "bridge": bridge_ref,
        "generated_by": f"agent-mcp {version}",
        "tools": {mt.stub: {"tool": mt.tool_name} for mt in plan},
    }


# ---------------------------------------------------------------------------
# Stub templates
# ---------------------------------------------------------------------------

def dispatcher_script() -> str:
    """The POSIX multi-call dispatcher; every stub symlinks to it."""
    return (
        "#!/usr/bin/env bash\n"
        "# Auto-generated by `agent-mcp materialize`. Do not edit.\n"
        "# One dispatcher, N names: stubs symlink here and we dispatch on argv[0].\n"
        "set -euo pipefail\n"
        'stub="$(basename "$0")"\n'
        'here="$(cd "$(dirname "$0")" && pwd)"\n'
        'exec agent-mcp call --manifest "$here/../manifest.json" '
        '--stub "$stub" "$@"\n'
    )


def ps1_shim() -> str:
    """The Windows PowerShell shim template (one per stub; name via $0)."""
    return (
        "#Requires -Version 7.0\n"
        "# Auto-generated by `agent-mcp materialize`. Do not edit.\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$stub = $MyInvocation.MyCommand.Name -replace '\\.ps1$',''\n"
        "$manifest = Join-Path $PSScriptRoot '..' | Join-Path -ChildPath 'manifest.json'\n"
        "& agent-mcp call --manifest $manifest --stub $stub @args\n"
        "exit $LASTEXITCODE\n"
    )


def cmd_shim() -> str:
    """The Windows CMD shim template (last resort; prefer --request-file)."""
    return (
        "@echo off\r\n"
        "REM Auto-generated by agent-mcp materialize. Do not edit.\r\n"
        "REM CMD mangles JSON argv -- pass a request file: <stub> --request-file req.json\r\n"
        'agent-mcp call --manifest "%~dp0..\\manifest.json" --stub "%~n0" %*\r\n'
    )


# ---------------------------------------------------------------------------
# Writing the farm (atomic swap)
# ---------------------------------------------------------------------------

def default_dest() -> Path:
    """The default materialization root: ``$AGENT_MCP_HOME/materialized``."""
    home = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp"))
    return home / "materialized"


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_farm(server_dir: Path, plan: list[MaterializedTool], *, server: str,
               bridge_ref: str, version: str, windows: bool = False) -> None:
    """Build ``<server_dir>`` (bin/ + doc/ + index.md + manifest.json) atomically.

    On POSIX a symlink farm points at one dispatcher; on Windows a two-template
    ``.ps1``/``.cmd`` shim farm is written instead (``windows=True`` forces it,
    e.g. for tests). The tree is assembled in a sibling temp dir and swapped in.
    """
    server_dir = server_dir.resolve()
    parent = server_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".{server_dir.name}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        _build_and_swap(tmp, server_dir, parent, plan, server=server,
                        bridge_ref=bridge_ref, version=version, windows=windows)
    finally:
        # On success ``tmp`` was renamed into place (gone); on any failure this
        # removes the half-built tree so temp dirs never accumulate.
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _build_and_swap(tmp: Path, server_dir: Path, parent: Path,
                    plan: list[MaterializedTool], *, server: str, bridge_ref: str,
                    version: str, windows: bool) -> None:
    bin_dir = tmp / "bin"
    doc_dir = tmp / "doc"
    bin_dir.mkdir(parents=True)
    doc_dir.mkdir(parents=True)

    (tmp / MANIFEST_NAME).write_text(
        json.dumps(build_manifest(server, plan, bridge_ref=bridge_ref, version=version),
                   indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp / "index.md").write_text(
        render_index(server, plan, bridge_ref=bridge_ref), encoding="utf-8"
    )

    use_windows = windows or os.name == "nt"
    if not use_windows:
        dispatcher = bin_dir / DISPATCHER_NAME
        dispatcher.write_text(dispatcher_script(), encoding="utf-8")
        _make_executable(dispatcher)

    for mt in plan:
        (doc_dir / f"{mt.stub}.md").write_text(
            render_sidecar(mt, server=server, bridge_ref=bridge_ref), encoding="utf-8"
        )
        if use_windows:
            ps1 = bin_dir / f"{mt.stub}.ps1"
            ps1.write_text(ps1_shim(), encoding="utf-8")
            cmd = bin_dir / f"{mt.stub}.cmd"
            cmd.write_text(cmd_shim(), encoding="utf-8")
        else:
            link = bin_dir / mt.stub
            # Relative symlink so the farm survives a moved parent dir.
            link.symlink_to(DISPATCHER_NAME)

    # Atomic swap: move any existing tree aside, rename temp in, drop the old.
    trash: Path | None = None
    if server_dir.exists():
        trash = parent / f".{server_dir.name}.old-{os.getpid()}"
        os.replace(server_dir, trash)
    try:
        os.replace(tmp, server_dir)
    except OSError:
        if trash is not None:
            os.replace(trash, server_dir)  # restore on failure
        raise
    finally:
        if trash is not None and trash.exists():
            shutil.rmtree(trash, ignore_errors=True)
