"""Request-scoped routing under the single shared decorator pipeline."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import itertools
import json
import os
from dataclasses import asdict, replace

from .config import BridgeConfig, ToolFilter
from .decorators import BridgeContext, build_decorators
from .pipeline import Pipeline, error_response, is_request, result_response
from .routing_errors import CompatibilityError as CompatibilityError, RoutingError


def isolated_config(cfg: BridgeConfig) -> BridgeConfig:
    """Qualify even explicit cache keys by endpoint and complete auth identity."""
    cfg = copy.deepcopy(cfg)
    endpoint = asdict(cfg.server)
    endpoint["url_secret_env_values"] = {
        key: os.environ.get(secret.source_env) for key, secret in cfg.server.url_secrets.items()
        if secret.source_env
    }
    for auth in (*cfg.auths, *cfg.server.url_secrets.values()):
        identity = {"endpoint": endpoint, "headers": cfg.headers, "auth": asdict(auth)}
        if auth.source_env:
            identity["source_env_value"] = os.environ.get(auth.source_env)
        auth.cache.key = "route-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:48]
    return replace(cfg, tools=ToolFilter(), decorators=[], routed_target=True)


class RoutedCore:
    """Lazy isolated upstream sessions; no mutable 'current target' context."""

    def __init__(self, cfg: BridgeConfig) -> None:
        assert cfg.routing is not None
        self.cfg = cfg
        self.spec = cfg.routing
        self._sessions = {}
        self._session_configs: dict[str, BridgeConfig] = {}
        self._locks = {key: asyncio.Lock() for key in self.spec.targets}
        self._request_locks = {key: asyncio.Lock() for key in self.spec.targets}
        self._catalog_lock = asyncio.Lock()
        self._contract: list[dict] | None = None
        self._verified: set[str] = set()
        self._ids = itertools.count(1)
        ctx = BridgeContext(new_id=self.new_id, emit_to_client=lambda _m: None)
        self._policies = {
            key: Pipeline(build_decorators(target, ctx), self._execute)
            for key, target in self.spec.targets.items()
        }

    def new_id(self) -> str:
        return f"route-{next(self._ids)}"

    async def _session(self, target: str):
        from .client import OneShotSession
        from .auth.identity import resolve_entra_identity

        async with self._locks[target]:
            cfg = copy.deepcopy(self.spec.targets[target])
            for auth in (*cfg.auths, *cfg.server.url_secrets.values()):
                if auth.normalized_kind == "entra":
                    auth.identity_context = await resolve_entra_identity(
                        auth, timeout=cfg.timeout)
            cfg = isolated_config(cfg)
            if target in self._sessions and self._session_configs[target] != cfg:
                session = self._sessions.pop(target)
                self._session_configs.pop(target, None)
                self._verified.discard(target)
                await session.__aexit__(None, None, None)
            if target not in self._sessions:
                session = OneShotSession(cfg)
                await session.__aenter__()
                self._sessions[target] = session
                self._session_configs[target] = cfg
            return self._sessions[target]

    @staticmethod
    def _normalized(tools: list[dict]) -> list[dict]:
        if any(not isinstance(t.get("name"), str) or not t["name"] for t in tools):
            raise CompatibilityError("routing compatibility error: invalid tool name")
        names = [t["name"] for t in tools]
        if len(names) != len(set(names)):
            raise CompatibilityError("routing compatibility error: duplicate tool name")
        return sorted(tools, key=lambda t: t["name"])

    async def catalog(self) -> list[dict]:
        async with self._catalog_lock:
            return await self._catalog_locked()

    async def _catalog_locked(self) -> list[dict]:
        if self._contract is None:
            async with self._request_locks[self.spec.catalog]:
                session = await self._session(self.spec.catalog)
                tools = self._normalized(await session.list_tools_checked())
            for tool in tools:
                schema = tool.get("inputSchema")
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise CompatibilityError(
                        "routing compatibility error: tools require object input schemas")
                if not isinstance(schema.get("properties", {}), dict) or not isinstance(
                    schema.get("required", []), list
                ) or any(not isinstance(k, str) for k in schema.get("required", [])):
                    raise CompatibilityError(
                        "routing compatibility error: malformed object input schema")
                if self.spec.parameter in schema.get("properties", {}):
                    raise CompatibilityError(
                        "routing compatibility error: selector collides with upstream argument")
            self._contract = tools
            self._verified.add(self.spec.catalog)
        tools = copy.deepcopy(self._contract)
        for tool in tools:
            schema = tool["inputSchema"]
            schema.setdefault("properties", {})[self.spec.parameter] = {
                "type": "string", "enum": list(self.spec.targets),
                "description": "Required execution target; never inferred.",
            }
            schema["required"] = list(dict.fromkeys(
                [*schema.get("required", []), self.spec.parameter]))
        return tools

    async def _execute(self, msg: dict) -> dict | None:
        target = msg["_routing_target"]
        await self.catalog()
        if msg["params"]["name"] not in {t["name"] for t in self._contract}:
            return error_response(msg, "tool is not part of the routing contract", -32601)
        # Identity transitions cannot close a connection while another call on
        # the same target is using it. Distinct targets remain concurrent.
        async with self._request_locks[target]:
            session = await self._session(target)
            if target not in self._verified:
                tools = self._normalized(await session.list_tools_checked())
                if tools != self._contract:
                    raise CompatibilityError(
                        f"routing compatibility error: target '{target}' differs from catalog")
                self._verified.add(target)
            upstream = {k: v for k, v in msg.items() if k != "_routing_target"}
            # Correlation IDs belong to each isolated connection, not to callers.
            upstream["id"] = session._need_client().new_id()
            response = await session._pipeline_core(upstream)
        if not is_request(msg):
            return None
        if response is not None:
            response = {**response, "id": msg["id"]}
        return response

    async def handle(self, msg: dict) -> dict | None:
        from .client import UpstreamError, tool_visible
        from . import protocol

        method = msg.get("method")
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return error_response(msg, "routing params must be an object", -32602) \
                if is_request(msg) else None
        if not is_request(msg) and method in {
            "tools/list", "initialize", "server/discover", "ping",
            "notifications/initialized",
        }:
            return None
        try:
            if method == "tools/call":
                args = params.get("arguments")
                target = args.get(self.spec.parameter) if isinstance(args, dict) else None
                if not isinstance(target, str) or target not in self.spec.targets:
                    return error_response(msg, f"required routing selector '{self.spec.parameter}' "
                                          "must name a configured target", -32602) \
                        if is_request(msg) else None
                name = params.get("name")
                if not isinstance(name, str) or not tool_visible(
                    name, self.cfg.tools
                ) or not tool_visible(name, self.spec.targets[target].tools):
                    return error_response(msg, "tool denied by routing target authorization",
                                          code=-32601) \
                        if is_request(msg) else None
                stripped = {k: v for k, v in args.items() if k != self.spec.parameter}
                routed = {**msg, "_routing_target": target,
                          "params": {**params, "arguments": stripped}}
                return await self._policies[target].handle(routed)
            if method == "tools/list":
                if params.get("cursor"):
                    return error_response(msg, "routed catalog has no continuation cursor", -32602)
                tools = await self.catalog()
                return result_response(msg, {"tools": [
                    t for t in tools if tool_visible(t["name"], self.cfg.tools)]})
            if method == "initialize":
                version = protocol.negotiate(
                    params.get("protocolVersion"),
                    protocol.SUPPORTED_VERSIONS,
                ) or protocol.LEGACY
                return result_response(msg, {
                    "protocolVersion": version,
                    "serverInfo": {"name": "agent-mcp-routing", "version": "1"},
                    "capabilities": {"tools": {}},
                })
            if method == "server/discover":
                return result_response(msg, protocol.discover_result(
                    {"name": "agent-mcp-routing", "version": "1"}, {"tools": {}}))
            if method == "ping":
                return result_response(msg, {})
            if method == "notifications/initialized":
                return None
            if is_request(msg):
                return error_response(msg, "routing supports tools only; no execution "
                                      f"context for '{method}'", -32601)
            raise RuntimeError(f"routing cannot safely forward '{method}' without a target")
        except (RoutingError, UpstreamError) as exc:
            if not is_request(msg):
                return None
            response = error_response(
                msg, str(exc), code=exc.code if exc.code is not None else -32603)
            if exc.data is not None:
                response["error"]["data"] = exc.data
            return response

    async def aclose(self) -> None:
        cleanup = asyncio.create_task(self._close_all())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # Caller cancellation cannot skip teardown of owned targets.
            await asyncio.gather(cleanup, return_exceptions=True)
            raise

    async def _close_all(self) -> None:
        errors = await asyncio.gather(
            *(policy.aclose() for policy in self._policies.values()),
            return_exceptions=True,
        )
        try:
            errors.extend(await asyncio.gather(
                *(session.__aexit__(None, None, None)
                  for session in self._sessions.values()),
                return_exceptions=True,
            ))
        finally:
            self._sessions.clear()
            self._session_configs.clear()
        for error in errors:
            if isinstance(error, BaseException):
                raise error
