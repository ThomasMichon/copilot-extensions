"""CLI entry point for agent-mcp.

Subcommands:
  bridge <name|--config FILE>   Run the stdio MCP bridge from a config file.
  validate <name|FILE>          Parse + schema-check a bridge config (no run).
  status                        Show prerequisites and available bridges.
  call <bridge> <tool> [args]   One-shot: invoke one upstream tool, print result.
  materialize <bridge>          Project the upstream catalog into a CLI stub fleet.
  serve                         Resident warmth daemon: keep upstreams warm over a socket.
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
from . import serve as _serve
from .bridge import Bridge
from .client import (
    OneShotSession,
    UpstreamError,
    result_is_error,
    result_structured,
    result_text,
)
from .config import (
    BRIDGES_DIR,
    BridgeConfig,
    ConfigError,
    discover_plugin_bridges,
    load_config,
)


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
    plugin_bridges = discover_plugin_bridges()
    if plugin_bridges:
        print("plugin-shipped bridges:")
        for name, path in sorted(plugin_bridges.items()):
            print(f"  - {name} ({path})")
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


def _emit_call_output(text: str, structured: object | None, is_error: bool,
                      *, tool: str) -> int:
    """Shared stdout/stderr/exit handling for a tool result (cold or serve path)."""
    if is_error:
        sys.stderr.write((text or f"tool '{tool}' reported an error") + "\n")
        return 1
    if text:
        sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))
    elif structured is not None:
        sys.stdout.write(json.dumps(structured) + "\n")
    return 0


async def _run_call(cfg: BridgeConfig, tool: str, arguments: dict) -> int:
    async with OneShotSession(cfg) as sess:
        result = await sess.call_tool(tool, arguments)
    return _emit_call_output(result_text(result), result_structured(result),
                             result_is_error(result), tool=tool)


def _try_serve_call(bridge_ref: str, tool: str, arguments: dict,
                    *, no_serve: bool) -> int | None:
    """Attempt the call via a running ``agent-mcp serve`` daemon.

    Returns an exit code when the daemon handled the request (success *or* a
    real upstream/config error), or ``None`` when the daemon is unavailable so
    the caller falls back to the stateless one-shot cold path.
    """
    socket = None if no_serve else _serve.serve_socket_if_available()
    if socket is None:
        return None
    # The daemon's CWD may differ from ours: send an absolute bridge path so it
    # resolves the same config (a bridge *name* passes through unchanged).
    ref = bridge_ref
    try:
        p = Path(bridge_ref)
        if p.exists():
            ref = str(p.resolve())
    except OSError:
        pass
    try:
        resp = asyncio.run(_serve.call_via_socket(socket, ref, tool, arguments))
    except OSError:
        return None  # socket vanished/refused -> fall back to cold path
    if not resp.get("ok"):
        print(f"agent-mcp call: {resp.get('error', 'serve error')}", file=sys.stderr)
        return 1
    return _emit_call_output(resp.get("content") or "", resp.get("structured"),
                             bool(resp.get("isError")), tool=tool)


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
        arguments = _resolve_arguments(inline, args.request_file, args.arguments)
    except ConfigError as exc:
        print(f"agent-mcp: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"agent-mcp call: invalid JSON arguments: {exc}", file=sys.stderr)
        return 1

    # Fast path: a running serve daemon holds the upstream warm. Falls through
    # to the cold one-shot path when the daemon is absent.
    served = _try_serve_call(bridge_ref, tool, arguments, no_serve=args.no_serve)
    if served is not None:
        return served

    try:
        cfg = load_config(bridge_ref)
    except ConfigError as exc:
        print(f"agent-mcp: {exc}", file=sys.stderr)
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


# ---------------------------------------------------------------------------
# serve -- resident warmth daemon
# ---------------------------------------------------------------------------

def _cmd_serve(args: argparse.Namespace) -> int:
    socket_path = args.socket or str(_serve.default_socket_path())
    server = _serve.Server(socket_path, idle_timeout=args.idle_timeout)
    print(f"agent-mcp serve: listening on {socket_path} "
          f"(idle-timeout {args.idle_timeout:g}s; Ctrl-C to stop)", file=sys.stderr)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        print("agent-mcp serve: stopped", file=sys.stderr)
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
    p_call.add_argument("--no-serve", action="store_true",
                        help="bypass a running 'agent-mcp serve' daemon and use "
                             "the stateless one-shot path directly")
    p_call.set_defaults(func=_cmd_call)

    p_serve = sub.add_parser(
        "serve", help="run the resident warmth daemon (keeps upstreams warm)")
    p_serve.add_argument("--socket", help="unix socket path "
                                          "(default: $AGENT_MCP_HOME/serve.sock)")
    p_serve.add_argument("--idle-timeout", type=float, default=300.0,
                         help="evict a warm session unused this many seconds "
                              "(default: 300)")
    p_serve.set_defaults(func=_cmd_serve)

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
