"""Default-installation emergency stop for suite-owned preToolUse hooks.

Canonical source: libs/bootstrap-killswitch/pretool_killswitch.py.
Vendored independently; do not import a sibling plugin's runtime.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_STATE_BYTES = 16 * 1024
CONTRACT = {"id": "copilot-extensions.pretool-killswitch", "version": 1,
            "scope": "default-installation", "capabilities": ["pretool-stop-v1"]}


def state_file() -> Path:
    if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        raise ValueError("pretool-killswitch supports the default installation only")
    override = os.environ.get("PRETOOL_KILLSWITCH_STATE_FILE")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("PRETOOL_KILLSWITCH_STATE_FILE must be an absolute path")
        return path
    return Path.home() / ".copilot-extensions" / "killSwitches" / "pretool-killswitch.json"


def read_state(path: Path) -> tuple[dict, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return {"active": False}, None
    except OSError as exc:
        return {"active": False}, str(exc)
    try:
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("state exceeds 16 KiB")
        state = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(state, dict) or type(state.get("active")) is not bool:
            raise ValueError("state must be an object with a boolean active field")
        if "contract" in state:
            contract = state["contract"]
            if (not isinstance(contract, dict) or contract.get("id") != CONTRACT["id"]
                    or type(contract.get("version")) is not int or contract["version"] != 1):
                raise ValueError("unsupported pretool-killswitch state contract")
        return state, None
    except (UnicodeError, ValueError) as exc:
        return {"active": False}, str(exc)


def is_active() -> bool:
    # A default-installation flag must never govern a namespaced cell.
    if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return False
    try:
        path = state_file()
    except ValueError as exc:
        print(f"[pretool-killswitch] invalid configuration; guards remain enabled: {exc}",
              file=sys.stderr)
        return False
    state, error = read_state(path)
    if error:
        print(f"[pretool-killswitch] invalid state; guards remain enabled: {error}",
              file=sys.stderr)
    return state.get("active") is True


def command(args: argparse.Namespace) -> int:
    try:
        path = state_file()
        verb = args.pretool_killswitch_command or "status"
        if verb == "on":
            state = {"contract": CONTRACT, "active": True, "reason": args.reason,
                     "set_by": f"{getpass.getuser()}@{socket.gethostname()}",
                     "set_at": datetime.now(timezone.utc).isoformat()}
            raw = (json.dumps(state, indent=2) + "\n").encode("utf-8")
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError("reason makes state exceed 16 KiB")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        elif verb == "off":
            path.unlink(missing_ok=True)
            state = {"active": False}
        else:
            state, error = read_state(path)
            if error:
                raise ValueError(f"invalid state (guards remain enabled): {error}")
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"contract": CONTRACT, "ok": False, "error": str(exc)}))
        else:
            print(f"[pretool-killswitch] {exc}", file=sys.stderr)
        return 2
    result = {**state, "contract": CONTRACT, "ok": True, "path": str(path)}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("ACTIVE -- stop state set; only updated suite preToolUse guards are paused." if state["active"]
              else "inactive -- suite preToolUse workflow guards are enabled.")
        if state["active"]:
            print(f"  reason: {state.get('reason') or '(none)'}")
            print(f"  set by: {state.get('set_by', '?')}")
            print(f"  set at: {state.get('set_at', '?')}")
            print("Reset with: agent-machines pretool-killswitch off")
        print(f"  state file: {path}")
    return 0


def register_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("pretool-killswitch",
                            help="Pause/resume suite preToolUse workflow guards (default install).")
    verbs = parser.add_subparsers(dest="pretool_killswitch_command")
    for name in ("on", "off", "status"):
        child = verbs.add_parser(name)
        if name == "on":
            child.add_argument("reason", nargs="?", default="")
        child.add_argument("--json", action="store_true")
        child.set_defaults(func=command)
    parser.set_defaults(func=command, json=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest="command", required=True)
    register_parser(verbs)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
