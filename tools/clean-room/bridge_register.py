#!/usr/bin/env python3
"""Expose clean-room containers to agent-bridge as a namespace provider.

Stdlib-only, no copilot-extensions imports -- keeps the clean-room tool
self-contained (it validates the harness; it must not depend on it). It drives
the current **declarative** agent-bridge provider model: instead of POSTing to a
runtime provider API (retired -- agent-bridge >= 0.4.0-dev307 moved provider
registration to ``~/.agent-bridge/providers.d/`` namespace-provider manifests,
ce#582), ``register`` drops a manifest and this same script *is* the provider
CLI the daemon shells out to (``namespace-list`` / ``namespace-resolve`` /
``namespace-target-repo`` / ``namespace-ensure-ready``).

Design (mirrors agent-containers' ``container:`` resolver): a single static
manifest for the ``cleanroom:`` namespace whose command points back here; the
provider **dynamically enumerates running clean-room containers** (``docker ps``)
so any ``cr-*`` box is addressable as ``cleanroom:<container>`` with no
per-box (re)registration -- concurrency-safe and self-cleaning (a lingering
manifest with no boxes simply lists nothing).

The resolved spawn is ``docker exec -i <container> bash -lc "<acp-command>"``.
The container already carries ``COPILOT_GITHUB_TOKEN`` in its env (injected at
``docker run`` by the runner), so the exec'd Copilot is authenticated with **no
token in argv or logs**. The ``--acp-command`` (baked into the manifest at
register time) is where the driven agent's plugins are loaded -- the runner
passes ``--plugin-dir <dir>`` for each of the scenario's declared plugin dirs, so
the driven reviewer actually has its skills (a bare ``copilot --acp`` would be
too narrow; enabled-plugins alone are not guaranteed to load headless).
``--acp-cwd`` is also exposed as the provider's ``workspace_folder`` so
agent-bridge sends the same path in ACP ``session/new``; changing only the child
shell cwd leaves the protocol cwd at ``/`` and prevents cwd-scoped sessionStart
hooks from binding the session.

Usage:
    # register the cleanroom: provider (idempotent) + drive a box:
    python bridge_register.py --acp-command "<acp>" --acp-cwd /workspace register --container cr-base --name cleanroom-base
    agent-bridge create cleanroom:cr-base --prompt-file p.txt --expand all
    python bridge_register.py unregister --name cleanroom-base --container cr-base

    # provider seam (invoked by the agent-bridge daemon, not by humans):
    python bridge_register.py --acp-command "<acp>" namespace-list
    python bridge_register.py --acp-command "<acp>" namespace-resolve cr-base
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

#: The agent-bridge namespace this provider serves (agents are ``cleanroom:<c>``).
NAMESPACE = "cleanroom"

#: Only containers whose name starts with this prefix are clean-room boxes.
DEFAULT_NAME_FILTER = "cr-"

#: The in-container Copilot ACP command. The runner overrides this with the
#: scenario's ``--plugin-dir`` flags so the driven agent loads its plugins.
DEFAULT_ACP = "copilot --acp --stdio --allow-all-tools"

#: Cross-plugin exit-code contract for ``namespace-resolve`` (agent-bridge #892
#: Inc 3): not-found -> 3 (daemon maps to KeyError). Clean-room boxes have no
#: bad-state (exit 4) distinction.
_NS_NOT_FOUND_EXIT = 3


def _absolute_posix_path(value: str) -> str:
    if not value:
        return value
    if not value.startswith("/") or any(
        character in value for character in "\0\r\n\t"
    ):
        raise argparse.ArgumentTypeError(
            "must be an absolute in-container POSIX path without control characters"
        )
    return value


def _bridge_dir() -> Path:
    return Path(os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")).expanduser()


def _providers_dir() -> Path:
    override = os.environ.get("AGENT_BRIDGE_PROVIDERS_DIR")
    if override:
        return Path(override).expanduser()
    return _bridge_dir() / "providers.d"


def _manifest_path() -> Path:
    return _providers_dir() / f"{NAMESPACE}.json"


def _docker(args: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return 1, ""
    return r.returncode, r.stdout


def _running_containers(name_filter: str) -> list[str]:
    rc, out = _docker(["ps", "--format", "{{.Names}}"])
    if rc != 0:
        return []
    return [
        n.strip() for n in out.splitlines()
        if n.strip() and n.strip().startswith(name_filter)
    ]


def _is_running(container: str) -> bool:
    rc, out = _docker(["inspect", "-f", "{{.State.Running}}", container])
    return rc == 0 and out.strip() == "true"


def _spawn_command(container: str, acp_command: str) -> list[str]:
    # The container carries COPILOT_GITHUB_TOKEN in its env (docker-run -e), so
    # the exec'd Copilot is authenticated with no token on the command line.
    return ["docker", "exec", "-i", container, "bash", "-lc", acp_command]


# --- provider protocol (the agent-bridge namespace-* CLI seam) -------------
def cmd_namespace_list(args) -> int:
    """Print a JSON array of NamespaceAgentInfo dicts for live clean-room boxes."""
    agents = [
        {
            "name": c,
            "display_name": f"Clean-room {c}",
            "description": "Clean-room validation container (Tier-E eval)",
            "state": "available",
        }
        for c in _running_containers(args.name_filter)
    ]
    print(json.dumps(agents))
    return 0


def cmd_namespace_resolve(args) -> int:
    """Print a JSON {type,spawn_command,user} for a clean-room container.

    Not-found (container absent/stopped) -> exit 3, which agent-bridge maps back
    to ``KeyError`` across the process boundary.
    """
    if not _is_running(args.name):
        print(
            f"clean-room container '{args.name}' is not running "
            f"(live: {_running_containers(args.name_filter)})",
            file=sys.stderr,
        )
        return _NS_NOT_FOUND_EXIT
    spec = {
        "type": "command",
        "spawn_command": _spawn_command(args.name, args.acp_command),
        "user": None,
    }
    if args.acp_cwd:
        spec["workspace_folder"] = args.acp_cwd
    print(json.dumps(spec))
    return 0


def cmd_namespace_target_repo(args) -> int:
    print("")  # clean-room boxes drive no related-repo plugin injection
    return 0


def cmd_namespace_ensure_ready(args) -> int:
    return 0 if _is_running(args.name) else 1


# --- manifest lifecycle (register / unregister) ----------------------------
def cmd_register(args) -> int:
    """Drop (idempotently) the declarative ``cleanroom:`` provider manifest.

    The manifest's ``command`` points back at this script with the ``--acp-command``
    (+ ``--name-filter``) baked in, so the daemon shells out to it for
    ``namespace-list``/``-resolve``/... Enumeration is dynamic, so one manifest
    serves every concurrent ``cr-*`` box.
    """
    pdir = _providers_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--acp-command",
        args.acp_command,
    ]
    if args.acp_cwd:
        command.extend(["--acp-cwd", args.acp_cwd])
    command.extend(["--name-filter", args.name_filter])
    manifest = {
        "namespace": NAMESPACE,
        "command": command,
        "restricted": True,
        "description": "Clean-room validation containers (agent-driven Tier-E eval)",
    }
    _manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "registered", "namespace": NAMESPACE,
        "manifest": str(_manifest_path()),
    }))
    print(f"registered provider '{NAMESPACE}:' (manifest {_manifest_path()})", file=sys.stderr)
    if args.container:
        print(f"dispatch with:  agent-bridge create {NAMESPACE}:{args.container} \"<prompt>\"", file=sys.stderr)
    return 0


def cmd_unregister(args) -> int:
    """Remove the shared manifest -- but only when no clean-room box remains.

    Concurrency-safe: another ``cr-*`` box may still be registered against the
    same shared namespace, so we keep the manifest while any live box (other than
    the one being torn down) exists. A stale manifest is harmless (it enumerates
    live containers), so leaving it is always safe.
    """
    mp = _manifest_path()
    others = [c for c in _running_containers(args.name_filter) if c != args.container]
    if others:
        print(json.dumps({
            "status": "kept", "reason": "other clean-room containers running",
            "remaining": others,
        }))
        return 0
    try:
        mp.unlink()
        status = "unregistered"
    except FileNotFoundError:
        status = "not_registered"
    print(json.dumps({"status": status, "namespace": NAMESPACE}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Main-parser options: baked into the manifest command (before the appended
    # namespace-* subcommand) so the daemon's `<command...> namespace-list` works.
    ap.add_argument("--acp-command", default=DEFAULT_ACP,
                    help="in-container Copilot ACP command (with --plugin-dir flags)")
    ap.add_argument("--acp-cwd", default="", type=_absolute_posix_path,
                    help="in-container ACP session/new cwd exposed as workspace_folder")
    ap.add_argument("--name-filter", default=DEFAULT_NAME_FILTER,
                    help="only containers whose name starts with this are clean-room boxes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="drop the cleanroom: provider manifest")
    r.add_argument("--container", default="", help="the box you intend to drive (for the hint)")
    r.add_argument("--name", default="", help="legacy agent name (informational)")

    u = sub.add_parser("unregister", help="remove the manifest when no box remains")
    u.add_argument("--name", default="", help="legacy agent name (informational)")
    u.add_argument("--container", default="", help="the box being torn down")

    sub.add_parser("namespace-list", help="[provider seam] JSON list of live boxes")

    nr = sub.add_parser("namespace-resolve", help="[provider seam] JSON spawn spec (exit 3 if absent)")
    nr.add_argument("name", help="container name")
    # agent-bridge may pass these on the full-capability path; clean-room boxes
    # ignore them (plugins come via the baked --acp-command --plugin-dir).
    nr.add_argument("--repo", default=None)
    nr.add_argument("--repo-remote", default=None)
    nr.add_argument("--stage-plugin", action="append", default=[])

    nt = sub.add_parser("namespace-target-repo", help="[provider seam] always empty")
    nt.add_argument("name", help="container name")

    ne = sub.add_parser("namespace-ensure-ready", help="[provider seam] exit 0 if running")
    ne.add_argument("name", help="container name")

    args = ap.parse_args()
    dispatch = {
        "register": cmd_register,
        "unregister": cmd_unregister,
        "namespace-list": cmd_namespace_list,
        "namespace-resolve": cmd_namespace_resolve,
        "namespace-target-repo": cmd_namespace_target_repo,
        "namespace-ensure-ready": cmd_namespace_ensure_ready,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
