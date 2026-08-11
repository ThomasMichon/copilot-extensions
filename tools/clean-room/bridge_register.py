#!/usr/bin/env python3
"""Register/unregister a clean-room container as an agent-bridge agent.

Stdlib-only, no copilot-extensions imports -- keeps the clean-room tool
self-contained (it validates the harness; it must not depend on it). Talks to
the local agent-bridge daemon's provider API exactly like agent-codespaces /
agent-containers do: a ``command``-type agent whose ``spawn_command`` runs
``copilot --acp --stdio`` inside the container over ``docker exec``.

Why the provider API and not a static file: agent-bridge's ``acp-agents.json``
override (a topology's ``agents_config:``) is parsed by ``parse_agent_registry``,
which does NOT read a raw ``spawn_command`` -- it only supports host/ssh and
``copilot_path`` agents. A ``docker exec`` transport therefore has to come
through the runtime provider API (TTL-scoped), which is what this does.

The in-container Copilot authenticates via the ``COPILOT_GITHUB_TOKEN`` the
runner already injected, so no token is embedded in the spawn command.

Usage:
    python bridge_register.py register   --container cr-base --name cleanroom-base [--ttl 3600]
    python bridge_register.py unregister --name cleanroom-base
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _bridge_dir() -> Path:
    return Path(os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")).expanduser()


def _resolve_url() -> str:
    """Live daemon URL: $AGENT_BRIDGE_BASE_URL > active.json > :9280 fallback."""
    explicit = os.environ.get("AGENT_BRIDGE_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    try:
        data = json.loads((_bridge_dir() / "active.json").read_text())
        active = data.get("active") or {}
        port = int(active.get("port") or 0)
        if port > 0:
            bind = active.get("bind") or "127.0.0.1"
            if bind in ("0.0.0.0", "", None):
                bind = "127.0.0.1"
            elif bind == "::":
                bind = "::1"
            return f"http://{bind}:{port}"
    except Exception:
        pass
    return "http://127.0.0.1:9280"


def _resolve_token() -> str | None:
    """Bridge bearer token from auth.yaml (token: ...) or auth_token."""
    auth_yaml = _bridge_dir() / "auth.yaml"
    if auth_yaml.exists():
        text = auth_yaml.read_text()
        m = re.search(r'(?m)^\s*token\s*:\s*"?([^"\s]+)"?\s*$', text)
        if m:
            return m.group(1).strip()
    token_file = _bridge_dir() / "auth_token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None


def _request(url: str, token: str, method: str, payload: dict | None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if method == "DELETE" and exc.code == 404:
            return {"status": "not_registered"}
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"agent-bridge {method} failed (HTTP {exc.code}): {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach agent-bridge at {url}: {exc.reason}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--container", required=True, help="docker container name (e.g. cr-base)")
    r.add_argument("--name", required=True, help="agent/provider name (e.g. cleanroom-base)")
    r.add_argument("--acp-command", default="copilot --acp --stdio --allow-all-tools")
    r.add_argument("--ttl", type=float, default=3600.0)
    u = sub.add_parser("unregister")
    u.add_argument("--name", required=True)
    args = ap.parse_args()

    token = _resolve_token()
    if not token:
        raise SystemExit(f"agent-bridge auth token not found under {_bridge_dir()} -- is agent-bridge installed?")
    base = _resolve_url()

    if args.cmd == "register":
        spawn = ["docker", "exec", "-i", args.container, "bash", "-lc", args.acp_command]
        payload = {
            "agents": [{
                "name": args.name,
                "display_name": f"Clean-room {args.container}",
                "description": "Clean-room validation container (manual bridge agent)",
                "icon": "container",
                "spawn_command": spawn,
            }],
            "ttl": args.ttl,
        }
        out = _request(f"{base}/api/v1/providers/{args.name}", token, "POST", payload)
        print(json.dumps(out))
        print(f"registered agent '{args.name}' -> {' '.join(spawn)}", file=sys.stderr)
        print(f"dispatch with:  agent-bridge send {args.name} \"<prompt>\"", file=sys.stderr)
    else:
        out = _request(f"{base}/api/v1/providers/{args.name}", token, "DELETE", None)
        print(json.dumps(out))
        print(f"unregistered agent '{args.name}'", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
