"""Provider-facing support preparation over Bridge's existing process boundary."""

from __future__ import annotations

import base64
import json
import sys
from urllib.parse import urlsplit


def _owner() -> tuple[int, str]:
    from .__main__ import _get_client
    from .protocol import PORTABLE_MUX_CAPABILITY, PORTABLE_INBOX_CAPABILITY, READONLY_PLUGIN_CONTROLS_CAPABILITY

    client = _get_client(ensure=False)
    health = client.health()
    capabilities = health.get("capabilities")
    if (
        health.get("ready") is not True or health.get("draining") is not False
        or not isinstance(capabilities, dict)
        or any(type(capabilities.get(name)) is not int or capabilities[name] != 1 for name in (
            PORTABLE_MUX_CAPABILITY, PORTABLE_INBOX_CAPABILITY, READONLY_PLUGIN_CONTROLS_CAPABILITY,
        ))
    ):
        raise RuntimeError("The running Bridge does not support portable mux v1")
    endpoint = urlsplit(client._base)
    if endpoint.hostname != "127.0.0.1" or endpoint.scheme != "http" or endpoint.port is None:
        raise RuntimeError("Portable mux registration requires the selected loopback Bridge")
    return endpoint.port, client._token


def register(subparsers) -> None:
    """Declare optional support separately from installed package versions."""
    parser = subparsers.add_parser(
        "receiving-support", help="Prepare selected portable receiving support without starting a daemon",
    )
    commands = parser.add_subparsers(dest="receiving_command", required=True)
    capabilities = commands.add_parser("capabilities")
    capabilities.add_argument("--owner", action="store_true", help="Also qualify the running Bridge, without starting it")
    capabilities.set_defaults(func=_capabilities)
    mux = commands.add_parser("mux-command", help="Read one launch request on stdin")
    mux.set_defaults(func=_mux_command)


def _capabilities(args) -> None:
    from .portable_policy import selected_context

    owner = {}
    if args.owner:
        port, _token = _owner()
        owner = {"owner_mux": 1, "daemon_port": port}
    print(json.dumps({
        "schema": "agent-bridge.receiving-support", "version": 1,
        "mux": {"version": 1, "platform": "posix", "transport": "command-stdin",
                "ensure_mux": 1, "registration": 1, "driven_by": 1},
        "policy_context": selected_context(),
        **owner,
    }, separators=(",", ":")))


def _mux_command(_args) -> None:
    from .receiving_bundle import build_mux_launch

    raw = sys.stdin.buffer.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("Mux launch request exceeds the provider input bound")
    request = json.loads(raw)
    required = {
        "cwd", "state_root", "scope_id", "attempt", "session_id", "copilot_args",
    }
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {"ensure_mux", "registration", "driven_by"}:
        raise ValueError("Unsupported mux launch provider request")
    if "registration" in request:
        port, token = _owner()
        if request["registration"] != {"port": port}:
            raise ValueError("Mux registration differs from the selected Bridge route")
        request["registration"] = {"port": port, "token": token}
    command, payload = build_mux_launch(**request)
    print(json.dumps({
        "schema": "agent-bridge.receiving-command", "version": 1,
        "command": command, "stdin_base64": base64.b64encode(payload).decode("ascii"),
    }, separators=(",", ":")))
