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

Design (mirrors agent-containers' ``container:`` resolver): a single immutable
manifest for the ``cleanroom:`` namespace points back here, while each
registered container has its own launch-settings record. The provider
dynamically enumerates registered, running clean-room containers, so concurrent
evaluations retain their own ACP command and cwd.

The resolved spawn is ``docker exec -i <container> bash -lc "<acp-command>"``.
The container already carries ``COPILOT_GITHUB_TOKEN`` in its env (injected at
``docker run`` by the runner), so the exec'd Copilot is authenticated with **no
token in argv or logs**. The per-container ``--acp-command`` is where the driven
agent's plugins are loaded -- the runner passes ``--plugin-dir <dir>`` for each
of the scenario's declared plugin dirs, so the driven reviewer actually has its
skills (a bare ``copilot --acp`` would be too narrow; enabled-plugins alone are
not guaranteed to load headless).
``--acp-cwd`` is also exposed as the provider's ``workspace_folder`` so
agent-bridge sends the same path in ACP ``session/new``; changing only the child
shell cwd leaves the protocol cwd at ``/`` and prevents cwd-scoped sessionStart
hooks from binding the session.

Usage:
    # register the cleanroom: provider (idempotent) + drive a box:
    python bridge_register.py --acp-command "<acp>" --acp-cwd /workspace register --container cr-base --name cleanroom-base
    agent-bridge create cleanroom:cr-base --prompt-file p.txt --expand all
    python bridge_register.py unregister --name cleanroom-base --container cr-base --container-id <id-from-register>

    # provider seam (invoked by the agent-bridge daemon, not by humans):
    python bridge_register.py namespace-list
    python bridge_register.py namespace-resolve cr-base
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
_CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


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


def _container_name(value: str) -> str:
    if not _CONTAINER_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a Docker-safe container name")
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


def _registrations_dir() -> Path:
    return _providers_dir().parent / f"{NAMESPACE}.d"


def _registration_path(container: str) -> Path:
    if not _CONTAINER_NAME.fullmatch(container):
        raise ValueError("invalid Docker container name")
    return _registrations_dir() / f"{container}.json"


@contextlib.contextmanager
def _registration_lock(container: str):
    lock_path = _registrations_dir() / ".locks" / f"{container}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _provider_script_path() -> Path:
    return _providers_dir().parent / "providers" / "cleanroom-provider.py"


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _manifest() -> dict[str, object]:
    return {
        "namespace": NAMESPACE,
        "command": [sys.executable, str(_provider_script_path())],
        "restricted": True,
        "description": "Clean-room validation containers (agent-driven Tier-E eval)",
    }


def _write_provider_script() -> None:
    source = Path(__file__).resolve()
    destination = _provider_script_path()
    data = source.read_bytes()
    if destination.is_file() and destination.read_bytes() == data:
        return
    _write_bytes_atomic(destination, data)


def _write_manifest() -> None:
    _write_json_atomic(_manifest_path(), _manifest())


def _registered_containers() -> list[str]:
    directory = _registrations_dir()
    if not directory.is_dir():
        return []
    return sorted(
        path.stem
        for path in directory.glob("*.json")
        if _CONTAINER_NAME.fullmatch(path.stem)
    )


def _load_registration(container: str) -> dict[str, str] | None:
    try:
        value = json.loads(_registration_path(container).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    acp_command = value.get("acp_command")
    acp_cwd = value.get("acp_cwd", "")
    container_id = value.get("container_id")
    if not isinstance(acp_command, str) or not acp_command:
        return None
    if not isinstance(acp_cwd, str):
        return None
    if not isinstance(container_id, str) or not container_id:
        return None
    return {
        "acp_command": acp_command,
        "acp_cwd": acp_cwd,
        "container_id": container_id,
    }


def _docker(args: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    command = ["docker"]
    configured = os.environ.get("CLEAN_ROOM_DOCKER_COMMAND_JSON")
    if configured is not None:
        try:
            parsed = json.loads(configured)
        except json.JSONDecodeError:
            return -1, "", "CLEAN_ROOM_DOCKER_COMMAND_JSON is malformed"
        if (
            not isinstance(parsed, list)
            or not parsed
            or not all(isinstance(part, str) and part for part in parsed)
        ):
            return -1, "", "CLEAN_ROOM_DOCKER_COMMAND_JSON is invalid"
        command = parsed
    try:
        r = subprocess.run(
            [*command, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)
    return r.returncode, r.stdout, r.stderr


def _container_state(container: str) -> tuple[str, bool, str]:
    rc, out, error = _docker(
        ["inspect", "-f", "{{.Id}} {{.State.Running}}", container]
    )
    if rc != 0:
        normalized_error = error.lower()
        if "no such object" in normalized_error or "no such container" in normalized_error:
            return "", False, "missing"
        return "", False, "error"
    parts = out.strip().split()
    if len(parts) != 2:
        return "", False, "error"
    return parts[0], parts[1] == "true", "present"


def _is_registered_container(
    container: str,
    registration: dict[str, str] | None = None,
) -> bool:
    registration = registration or _load_registration(container)
    container_id, running, state = _container_state(container)
    return bool(
        registration
        and state == "present"
        and running
        and container_id == registration["container_id"]
        and _registration_cwd_exists(registration)
    )


def _registration_cwd_exists(registration: dict[str, str]) -> bool:
    acp_cwd = registration["acp_cwd"]
    if not acp_cwd:
        return True
    rc, _, _ = _docker(
        [
            "exec",
            registration["container_id"],
            "test",
            "-d",
            acp_cwd,
        ]
    )
    return rc == 0


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
        for c in _registered_containers()
        if _is_registered_container(c)
    ]
    print(json.dumps(agents))
    return 0


def cmd_namespace_resolve(args) -> int:
    """Print a JSON {type,spawn_command,user} for a clean-room container.

    Not-found (container absent/stopped) -> exit 3, which agent-bridge maps back
    to ``KeyError`` across the process boundary.
    """
    registration = _load_registration(args.name)
    if not _is_registered_container(args.name, registration):
        print(
            f"clean-room container '{args.name}' is not registered and running "
            f"(available: {[c for c in _registered_containers() if _is_registered_container(c)]})",
            file=sys.stderr,
        )
        return _NS_NOT_FOUND_EXIT
    spec = {
        "type": "command",
        "spawn_command": _spawn_command(
            registration["container_id"],
            registration["acp_command"],
        ),
        "user": None,
    }
    if registration["acp_cwd"]:
        spec["workspace_folder"] = registration["acp_cwd"]
    print(json.dumps(spec))
    return 0


def cmd_namespace_target_repo(args) -> int:
    print("")  # clean-room boxes drive no related-repo plugin injection
    return 0


def cmd_namespace_ensure_ready(args) -> int:
    return 0 if _is_registered_container(args.name) else 1


# --- manifest lifecycle (register / unregister) ----------------------------
def cmd_register(args) -> int:
    """Drop (idempotently) the declarative ``cleanroom:`` provider manifest.

    The manifest command is immutable. Per-container launch settings live in a
    separate record so concurrent evaluations cannot overwrite each other's ACP
    command or workspace folder.
    """
    if not args.container.startswith(args.name_filter):
        print(
            f"clean-room container '{args.container}' does not match "
            f"name filter '{args.name_filter}'",
            file=sys.stderr,
        )
        return 2
    with _registration_lock(args.container):
        container_id, running, state = _container_state(args.container)
        if state != "present" or not container_id or not running:
            print(
                f"clean-room container '{args.container}' is not running",
                file=sys.stderr,
            )
            return 2
        if args.acp_cwd:
            rc, _, _ = _docker(
                ["exec", container_id, "test", "-d", args.acp_cwd]
            )
            if rc != 0:
                print(
                    f"ACP cwd '{args.acp_cwd}' is not a directory in "
                    f"container '{args.container}'",
                    file=sys.stderr,
                )
                return 2
        _write_provider_script()
        _write_json_atomic(
            _registration_path(args.container),
            {
                "container": args.container,
                "container_id": container_id,
                "acp_command": args.acp_command,
                "acp_cwd": args.acp_cwd,
            },
        )
        _write_manifest()
    print(json.dumps({
        "status": "registered", "namespace": NAMESPACE,
        "manifest": str(_manifest_path()),
        "registration": str(_registration_path(args.container)),
        "container_id": container_id,
    }))
    print(f"registered provider '{NAMESPACE}:' (manifest {_manifest_path()})", file=sys.stderr)
    if args.container:
        print(f"dispatch with:  agent-bridge create {NAMESPACE}:{args.container} \"<prompt>\"", file=sys.stderr)
    return 0


def cmd_unregister(args) -> int:
    """Remove one container record and retire the manifest after the last one."""
    mp = _manifest_path()
    with _registration_lock(args.container):
        registration = _load_registration(args.container)
        if registration is not None:
            if args.stale:
                recorded_id, _, state = _container_state(
                    registration["container_id"]
                )
                if state == "error":
                    print(
                        f"could not verify whether container "
                        f"'{registration['container_id']}' still exists",
                        file=sys.stderr,
                    )
                    return 2
                if state == "present" and recorded_id:
                    print(
                        f"registration for '{args.container}' still belongs to "
                        "an existing container instance",
                        file=sys.stderr,
                    )
                    return 2
            elif args.container_id != registration["container_id"]:
                print(
                    f"registration for '{args.container}' belongs to a different "
                    "container instance",
                    file=sys.stderr,
                )
                return 2
        try:
            _registration_path(args.container).unlink()
        except FileNotFoundError:
            pass
    others = _registered_containers()
    if others:
        print(json.dumps({
            "status": "kept", "reason": "other clean-room containers registered",
            "remaining": others,
        }))
        return 0
    try:
        mp.unlink()
        status = "unregistered"
    except FileNotFoundError:
        status = "not_registered"
    if _registered_containers():
        _write_manifest()
        status = "kept"
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
    r.add_argument(
        "--container",
        required=True,
        type=_container_name,
        help="the box whose launch settings are being registered",
    )
    r.add_argument("--name", default="", help="legacy agent name (informational)")

    u = sub.add_parser("unregister", help="remove the manifest when no box remains")
    u.add_argument("--name", default="", help="legacy agent name (informational)")
    u.add_argument(
        "--container",
        required=True,
        type=_container_name,
        help="the box being torn down",
    )
    ownership = u.add_mutually_exclusive_group(required=True)
    ownership.add_argument(
        "--container-id",
        default="",
        help="expected immutable Docker ID for compare-and-delete",
    )
    ownership.add_argument(
        "--stale",
        action="store_true",
        help="remove only when the recorded immutable Docker ID no longer exists",
    )

    sub.add_parser("namespace-list", help="[provider seam] JSON list of live boxes")

    nr = sub.add_parser("namespace-resolve", help="[provider seam] JSON spawn spec (exit 3 if absent)")
    nr.add_argument("name", type=_container_name, help="container name")
    # agent-bridge may pass these on the full-capability path; clean-room boxes
    # ignore them (plugins come via the baked --acp-command --plugin-dir).
    nr.add_argument("--repo", default=None)
    nr.add_argument("--repo-remote", default=None)
    nr.add_argument("--stage-plugin", action="append", default=[])

    nt = sub.add_parser("namespace-target-repo", help="[provider seam] always empty")
    nt.add_argument("name", type=_container_name, help="container name")

    ne = sub.add_parser("namespace-ensure-ready", help="[provider seam] exit 0 if running")
    ne.add_argument("name", type=_container_name, help="container name")

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
