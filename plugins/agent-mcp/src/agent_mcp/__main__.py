"""CLI entry point for agent-mcp.

Subcommands:
  bridge <name|--config FILE>   Run the stdio MCP bridge from a config file.
  validate <name|FILE>          Parse + schema-check a bridge config (no run).
  status                        Show prerequisites and available bridges.
  call <bridge> <tool> [args]   One-shot: invoke one upstream tool, print result.
  materialize <bridge>          Project the upstream catalog into a CLI stub fleet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
from pathlib import Path

from . import __version__
from . import materialize as _materialize
from .bridge import Bridge
from .client import (
    OneShotSession,
    UpstreamError,
    result_is_error,
    result_structured,
    result_text,
)
from .config import BRIDGES_DIR, BridgeConfig, ConfigError, load_config


def _configure_logging(level: str) -> None:
    # Logs go to stderr -- stdout is the JSON-RPC channel and must stay clean.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
        format="agent-mcp: %(name)s: %(message)s",
    )


def _cmd_bridge(args: argparse.Namespace) -> int:
    target = args.config or args.name
    if not target:
        print("agent-mcp bridge: a bridge name or --config FILE is required", file=sys.stderr)
        return 2
    try:
        cfg = load_config(target)
    except ConfigError as exc:
        print(f"agent-mcp: {exc}", file=sys.stderr)
        return 1
    return asyncio.run(Bridge(cfg).run())


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.name)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    where = cfg.source_path or args.name
    auth_desc = "+".join(a.kind for a in cfg.auths)
    print(f"OK: {where} -- {cfg.server.type} -> "
          f"{cfg.server.launch_desc} (auth: {auth_desc})")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    print(f"agent-mcp {__version__}")
    print("prerequisites:")
    for tool in ("python", "az", "gh", "git"):
        found = shutil.which(tool)
        print(f"  [{'OK ' if found else '-- '}] {tool}: {found or 'not found'}")
    print(f"bridges dir: {BRIDGES_DIR}")
    if BRIDGES_DIR.is_dir():
        files = sorted(
            p for p in BRIDGES_DIR.iterdir()
            if p.suffix.lower() in (".yaml", ".yml", ".json")
        )
        if files:
            for p in files:
                print(f"  - {p.stem} ({p.name})")
        else:
            print("  (no bridge config files)")
    else:
        print("  (directory does not exist yet)")
    return 0


# ---------------------------------------------------------------------------
# call -- one-shot invoke a single upstream tool
# ---------------------------------------------------------------------------

def _extract_args(obj: object, *, where: str) -> dict:
    """Pull the MCP ``arguments`` object out of a parsed request payload.

    Accepts the bare arguments object, or a wrapper ``{"arguments": {...}}``
    (optionally with a ``tool`` key). Returns the arguments mapping.
    """
    if isinstance(obj, dict):
        inner = obj.get("arguments")
        if isinstance(inner, dict):
            return inner
        return obj
    raise ConfigError(f"{where}: expected a JSON object, got {type(obj).__name__}")


def _resolve_arguments(inline: str | None, request_file: str | None,
                       arguments_flag: str | None) -> dict:
    """Resolve the tool ``arguments`` from --arguments / --request-file / inline /
    stdin, in that precedence. Missing everywhere means an empty object."""
    if arguments_flag is not None:
        return _extract_args(json.loads(arguments_flag), where="--arguments")
    if request_file is not None:
        text = Path(request_file).expanduser().read_text(encoding="utf-8")
        if not text.strip():
            return {}
        return _extract_args(json.loads(text), where=f"--request-file {request_file}")
    if inline is not None:
        return _extract_args(json.loads(inline), where="inline arguments")
    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return _extract_args(json.loads(text), where="stdin")
    return {}


def _resolve_stub(manifest_path: str, stub: str) -> tuple[str, str]:
    """Read a materialize manifest and resolve ``stub`` -> (bridge_ref, tool)."""
    data = json.loads(Path(manifest_path).expanduser().read_text(encoding="utf-8"))
    bridge_ref = data.get("bridge")
    entry = (data.get("tools") or {}).get(stub)
    if not bridge_ref or not isinstance(entry, dict) or "tool" not in entry:
        raise ConfigError(f"manifest {manifest_path}: no stub '{stub}'")
    return str(bridge_ref), str(entry["tool"])


async def _run_call(cfg: BridgeConfig, tool: str, arguments: dict) -> int:
    async with OneShotSession(cfg) as sess:
        result = await sess.call_tool(tool, arguments)
    text = result_text(result)
    if result_is_error(result):
        sys.stderr.write((text or f"tool '{tool}' reported an error") + "\n")
        return 1
    if text:
        sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))
    else:
        structured = result_structured(result)
        if structured is not None:
            sys.stdout.write(json.dumps(structured) + "\n")
    return 0


def _cmd_call(args: argparse.Namespace) -> int:
    try:
        if args.stub:
            if not args.manifest:
                print("agent-mcp call: --stub requires --manifest", file=sys.stderr)
                return 2
            bridge_ref, tool = _resolve_stub(args.manifest, args.stub)
            inline = args.pos[0] if args.pos else None
        else:
            if len(args.pos) < 2:
                print("agent-mcp call: BRIDGE and TOOL are required "
                      "(or use --manifest/--stub)", file=sys.stderr)
                return 2
            bridge_ref, tool = args.pos[0], args.pos[1]
            inline = args.pos[2] if len(args.pos) > 2 else None
        cfg = load_config(bridge_ref)
        arguments = _resolve_arguments(inline, args.request_file, args.arguments)
    except ConfigError as exc:
        print(f"agent-mcp: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"agent-mcp call: invalid JSON arguments: {exc}", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run_call(cfg, tool, arguments))
    except UpstreamError as exc:
        print(f"agent-mcp call: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# materialize -- project the upstream catalog into a CLI stub fleet
# ---------------------------------------------------------------------------

async def _run_materialize(cfg: BridgeConfig) -> list[dict]:
    async with OneShotSession(cfg) as sess:
        return await sess.list_tools()


def _cmd_materialize(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.name)
    except ConfigError as exc:
        print(f"agent-mcp: {exc}", file=sys.stderr)
        return 1
    try:
        tools = asyncio.run(_run_materialize(cfg))
    except UpstreamError as exc:
        print(f"agent-mcp materialize: {exc}", file=sys.stderr)
        return 1

    plan = _materialize.plan_tools(tools)
    if not plan:
        print("agent-mcp materialize: upstream advertised no tools", file=sys.stderr)
        return 1

    server = _materialize.server_name_for(cfg, args.server_name)
    dest = Path(args.dest).expanduser() if args.dest else _materialize.default_dest()
    server_dir = dest / server
    bridge_ref = str(cfg.source_path) if cfg.source_path else args.name
    _materialize.write_farm(
        server_dir, plan, server=server, bridge_ref=bridge_ref,
        version=__version__, windows=args.windows,
    )
    if not args.quiet:
        print(f"materialized {len(plan)} tool(s) -> {server_dir}")
        print(f"  bin/  ({len(plan)} stub(s)) -- add to PATH to invoke by name")
        print(f"  doc/  ({len(plan)} sidecar(s))")
        print("  index.md, manifest.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"agent-mcp {__version__}")
    parser.add_argument("--log-level", default="info",
                        help="logging level (debug/info/warning/error)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bridge = sub.add_parser("bridge", help="run the stdio MCP bridge")
    p_bridge.add_argument("name", nargs="?", help="bridge name under ~/.agent-mcp/bridges/")
    p_bridge.add_argument("--config", help="explicit path to a bridge config file")
    p_bridge.set_defaults(func=_cmd_bridge)

    p_validate = sub.add_parser("validate", help="validate a bridge config")
    p_validate.add_argument("name", help="bridge name or path to a config file")
    p_validate.set_defaults(func=_cmd_validate)

    p_status = sub.add_parser("status", help="show prerequisites and bridges")
    p_status.set_defaults(func=_cmd_status)

    p_call = sub.add_parser(
        "call", help="one-shot: invoke a single upstream tool and print its result")
    p_call.add_argument("pos", nargs="*", metavar="ARG",
                        help="BRIDGE TOOL [INLINE_JSON] (direct form); in --stub "
                             "form, an optional inline JSON arguments object")
    p_call.add_argument("--manifest", help="path to a materialize manifest.json (stub form)")
    p_call.add_argument("--stub", help="stub name to resolve via --manifest")
    p_call.add_argument("--request-file",
                        help="path to a JSON file holding the arguments object")
    p_call.add_argument("--arguments",
                        help="the arguments object as an inline JSON string")
    p_call.set_defaults(func=_cmd_call)

    p_mat = sub.add_parser(
        "materialize", help="project an upstream MCP catalog into a CLI stub fleet")
    p_mat.add_argument("name", help="bridge name or path to a config file")
    p_mat.add_argument("--server-name", help="override the server namespace directory")
    p_mat.add_argument("--dest", help="materialization root (default: "
                                      "$AGENT_MCP_HOME/materialized)")
    p_mat.add_argument("--windows", action="store_true",
                       help="emit the Windows .ps1/.cmd shim farm instead of symlinks")
    p_mat.add_argument("--quiet", action="store_true", help="suppress the summary")
    p_mat.set_defaults(func=_cmd_materialize)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
