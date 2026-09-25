"""Staged connectivity diagnosis for one bridge.

``agent-mcp diagnose <bridge>`` answers "which layer is actually broken" for
an MCP connectivity failure -- config, auth, transport, protocol handshake, or
the upstream catalog itself -- instead of one opaque top-level error. It
drives the exact same code path ``call``/``materialize`` use
(:mod:`agent_mcp.config` -> :mod:`agent_mcp.auth` ->
:mod:`agent_mcp.transports` -> :class:`agent_mcp.client.OneShotSession`), so a
clean diagnosis is a genuine guarantee the bridge itself will work -- it never
re-implements or approximates the connection.

Layer notes: for a **stdio** bridge, ``transport-connect`` (subprocess spawn +
child-env credential injection) and ``handshake`` (the MCP
``initialize``/``discover`` exchange) are genuinely separate steps. For an
**http**/**sse** bridge the transport is stateless (``Transport.start()`` is a
no-op) -- auth headers and the network round-trip both happen on the first
request, inside ``handshake`` -- so a connectivity or auth failure for an HTTP
bridge is reported *at* the handshake stage, with the upstream HTTP status
(if any) preserved in the failure detail rather than invented as a separate
stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .client import OneShotSession, UpstreamError
from .config import ConfigError, load_config

# One-line remediation pointer per stage, shown under a failed stage.
_HINTS: dict[str, str] = {
    "config": "Run `agent-mcp validate <bridge>` for the exact schema error.",
    "auth": "The auth injector itself raised before any request was made -- "
            "check the bridge config's `auth:` block (command path, "
            "az/gh login state) directly.",
    "transport-connect": "The upstream process could not be spawned, or an "
                          "explicit connect step failed -- check the "
                          "`server.command`/`server.url` in the bridge config "
                          "and that any required binary is on PATH.",
    "handshake": "The transport connected but no valid MCP response came "
                 "back before timeout -- for http/sse this covers both "
                 "network reachability (refused/timed out/5xx) and auth "
                 "(401/403): check the upstream service is running and "
                 "reachable, and that the resolved credential is valid.",
    "catalog": "The handshake succeeded but `tools/list` failed -- an "
               "upstream-side error on an otherwise healthy connection.",
}

_STAGE_ORDER = ("config", "auth", "transport-connect", "handshake", "catalog")


@dataclass
class StageResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DiagnoseReport:
    bridge: str
    stages: list[StageResult] = field(default_factory=list)
    tool_count: int | None = None

    @property
    def ok(self) -> bool:
        return bool(self.stages) and all(s.ok for s in self.stages)

    @property
    def failed_stage(self) -> StageResult | None:
        for s in self.stages:
            if not s.ok:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "bridge": self.bridge,
            "ok": self.ok,
            "tool_count": self.tool_count,
            "stages": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in self.stages],
        }


async def diagnose(
    name_or_path: str,
    *,
    list_tools: bool = True,
    printer: Callable[[str], None] = print,
) -> DiagnoseReport:
    """Walk every connectivity layer for one bridge, reporting progress as it
    goes and stopping at the first genuine failure.

    ``printer`` is called once per stage transition -- pass a no-op (e.g.
    ``lambda _line: None``) to collect a :class:`DiagnoseReport` silently
    (for example for ``--json`` output).
    """
    report = DiagnoseReport(bridge=name_or_path)
    total = len(_STAGE_ORDER)

    def step(idx: int, name: str) -> str:
        return f"[{idx}/{total}] {name}"

    printer(f"{step(1, 'config')}: loading {name_or_path} ...")
    try:
        cfg = load_config(name_or_path)
    except ConfigError as exc:
        printer(f"{step(1, 'config')}: FAILED -- {exc}")
        report.stages.append(StageResult("config", False, str(exc)))
        return report
    except (OSError, UnicodeDecodeError) as exc:
        # A config path that exists but can't be read (permissions, a broken
        # symlink) or isn't valid UTF-8 -- a config-stage failure just like a
        # schema/parse error, not an unhandled traceback (``_read_file``'s
        # ``path.read_text()`` raises these unwrapped, outside ``ConfigError``).
        printer(f"{step(1, 'config')}: FAILED -- {exc}")
        report.stages.append(StageResult("config", False, str(exc)))
        return report
    except (ValueError, TypeError) as exc:
        # A parseable-but-malformed scalar (e.g. ``timeout: not-a-number``) --
        # ``parse_config`` converts several fields (``timeout``, ``retries``,
        # cache ``ttl``/``skew``, decorator options) via bare ``float()``/
        # ``int()`` calls that raise unwrapped, outside ``ConfigError`` too.
        printer(f"{step(1, 'config')}: FAILED -- {exc}")
        report.stages.append(StageResult("config", False, str(exc)))
        return report
    where = str(cfg.source_path) if cfg.source_path else name_or_path
    printer(f"{step(1, 'config')}: OK -- {where} -> {cfg.server.type} {cfg.server.launch_desc}")
    report.stages.append(StageResult("config", True, where))

    stage_idx = {"auth": 2, "transport-connect": 3, "handshake": 4}
    announced: set[str] = set()

    def on_stage(stage: str) -> None:
        idx = stage_idx.get(stage)
        if idx is None or stage in announced:
            return
        announced.add(stage)
        printer(f"{step(idx, stage)}: ...")

    session = OneShotSession(cfg, on_stage=on_stage)
    started = time.monotonic()
    try:
        await session.__aenter__()
    except Exception as exc:  # report every failure mode -- never crash the CLI
        failed_stage = session.stage if session.stage in stage_idx else "handshake"
        idx = stage_idx.get(failed_stage, 4)
        printer(f"{step(idx, failed_stage)}: FAILED -- {exc}")
        printer(f"  hint: {_HINTS.get(failed_stage, '')}")
        report.stages.append(StageResult(failed_stage, False, str(exc)))
        return report

    try:
        elapsed = time.monotonic() - started
        # Every stage on the way to a successful connect gets a retroactive OK --
        # __aenter__ only reaches "ready" after auth, transport-connect, and
        # handshake all succeeded.
        for name in ("auth", "transport-connect", "handshake"):
            report.stages.append(StageResult(name, True))
        server_name = session.server_info.get("name", "?")
        era = "modern" if session.is_modern else "legacy"
        printer(f"{step(2, 'auth')}: OK")
        printer(f"{step(3, 'transport-connect')}: OK")
        printer(f"{step(4, 'handshake')}: OK -- {session.protocol_version} ({era}) "
                f"server={server_name} ({elapsed:.2f}s)")

        if list_tools:
            printer(f"{step(5, 'catalog')}: listing tools ...")
            try:
                tools = await session.list_tools_checked()
            except UpstreamError as exc:
                printer(f"{step(5, 'catalog')}: FAILED -- {exc}")
                printer(f"  hint: {_HINTS['catalog']}")
                report.stages.append(StageResult("catalog", False, str(exc)))
                return report
            report.tool_count = len(tools)
            printer(f"{step(5, 'catalog')}: OK -- {len(tools)} tool(s)")
            report.stages.append(StageResult("catalog", True, str(len(tools))))
    finally:
        await session.__aexit__(None, None, None)

    return report
