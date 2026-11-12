"""Optional physical receiver; only pre-application ineligibility may fall back."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from .execution_context import SharingUnavailable
from .owner_bootstrap import ENVIRONMENT_KEYS, receiver


def launch_environment() -> dict:
    """Describe only the wrapper-owned path/interpreter delta, never credentials."""
    return {key: os.environ.get(key) for key in ENVIRONMENT_KEYS}


def _qualify(request):
    from . import custody_lifetime, execution_context, forward, incoming, sockio
    from .__main__ import build_parser
    from .config import load_config
    from .cutover import _require_selected

    entry = request["receiver"]
    if (
        not sys.platform.startswith("linux")
        or str(Path(sys.executable).absolute()) != entry["python"]
        or str(Path(sys.prefix).resolve()) != entry["prefix"]
        or str(Path(__file__).resolve()) != entry["module"]
    ):
        raise SharingUnavailable("optional owner receiver differs from its physical declaration")
    layout = incoming.require_selected()
    if layout is None or request["selection"] != {
        "generation": layout[0], "root": str(Path(layout[2]).resolve()),
        "prefix": str(Path(sys.prefix).resolve()), "python": str(Path(sys.executable).resolve()),
    }:
        raise SharingUnavailable("optional owner receiver selection changed")
    if not forward._multiplex_enabled() or not forward._ensure_serve_enabled():
        raise SharingUnavailable("optional owner receiver startup disabled")
    if os.getcwd() != str(sockio.default_home_dir().resolve()):
        raise SharingUnavailable("optional owner receiver cannot preserve cold custody cwd")
    args = build_parser().parse_args(request["arguments"])
    target = args.config or args.name
    if not target:
        raise SharingUnavailable("optional owner receiver requires an ordinary target")
    execution_context._identity(load_config(target, repository_root=Path.cwd()), "0" * 64)
    with custody_lifetime.SessionOwner(request["pid"], request["birth"]):
        _require_selected(layout)
    args.session_owner_pid, args.session_owner_birth = request["pid"], request["birth"]
    return args


def run(request) -> int:
    """Select optional custody before startup; never replay an accepted outcome."""
    from .owner_bootstrap import ENTRY_CAPABILITY

    declared = receiver({
        "supported": True, "invocation_allowed": True, "entry": request.get("receiver"),
        "contract": {"name": "agent-mcp.session-owner-handoff", "minimum": 1,
                     "maximum": 1, "capabilities": [ENTRY_CAPABILITY]},
    })
    if declared is None:
        raise ValueError("Invalid physical owner entry declaration")
    environment = os.environ.copy()
    try:
        for key, value in declared["environment"].items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            args = _qualify(request)
        except (OSError, ValueError):
            os.environ.clear()
            os.environ.update(environment)
            original = request["original"]
            os.execvpe(original[0], original, environment)
            raise RuntimeError("Ordinary MCP exec unexpectedly returned")
        # No fallback beyond this boundary: backend startup or intake may occur.
        return args.func(args)
    finally:
        os.environ.clear()
        os.environ.update(environment)


def command(args) -> int:
    try:
        return run(json.loads(args.request))
    except (OSError, ValueError) as error:
        print(f"agent-mcp: optional owner entry refused: {error}", file=sys.stderr)
        return 1
