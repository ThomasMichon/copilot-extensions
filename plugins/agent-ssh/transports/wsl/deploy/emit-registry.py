#!/usr/bin/env python3
"""Emit an agent-ssh normalized **wsl** registry for THIS machine's own WSL.

The `wsl` transport is local: it reaches the Windows machine's own WSL sshd
through the `wsl.exe` interop stdio pipe (GSA-safe), so the registry it needs is
tiny -- a single record for the local machine's WSL target. This script reads the
harness `machines.yaml`, finds the local machine's `ssh.environments` entry named
`wsl`, and emits a normalized registry the core `emit-profile` renders into
`~/.ssh/config.d/50-agent-ssh-wsl.conf`.

Unlike dtssh there is no live state to resolve: WSL distro/user/port are static.
Port (2200) and distro (Ubuntu) default here and can be overridden per machine
via flags (machines.yaml carries them only as prose today).
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any


def _load_machines(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except ModuleNotFoundError:  # pragma: no cover - yaml is a package dep
        import json
        data = json.loads(text)
    return data or {}


def _local_machine_key(machines: dict[str, Any], override: str | None) -> str | None:
    if override:
        return override if override in machines else None
    host = socket.gethostname().lower()
    if host in machines:
        return host
    # tolerate a machines.yaml key that is a prefix/suffix of the OS hostname
    for key in machines:
        if host == key.lower() or host.startswith(key.lower()) or key.lower().startswith(host):
            return key
    return None


def _wsl_env(machine: dict[str, Any]) -> dict[str, Any] | None:
    for env in (machine.get("ssh", {}) or {}).get("environments", []) or []:
        if env.get("name") == "wsl":
            return env
    return None


def _select_identity_file(machine_key: str, ssh_dir: Path | None = None) -> str | None:
    """Select an existing private key whose sibling public key is present."""
    root = ssh_dir or (Path.home() / ".ssh")
    scoped = "id_ed25519_" + re.sub(r"[^A-Za-z0-9]+", "_", machine_key).strip("_")
    names = [scoped, "id_ed25519"]
    try:
        names.extend(p.name for p in sorted(root.glob("id_ed25519_*")))
        names.extend(p.name for p in sorted(root.glob("id_*")))
    except OSError:
        return None

    seen: set[str] = set()
    for name in names:
        if name in seen or name.endswith(".pub") or "-cert" in name:
            continue
        seen.add(name)
        private = root / name
        public = root / f"{name}.pub"
        if private.is_file() and public.is_file():
            return f"~/.ssh/{name}"
    return None


def _yaml_quote(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text.lower() in {"yes", "no", "true", "false", "on", "off", "null", "~"}:
        return '"' + text + '"'
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,\\-]+", text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_registry(record: dict[str, Any]) -> str:
    lines = [
        "transport: wsl",
        "topology: per-machine",
        "machines:",
        f"  - name: {_yaml_quote(record['name'])}",
        f"    user: {_yaml_quote(record['user'])}",
        f"    port: {_yaml_quote(record['port'])}",
        f"    distro: {_yaml_quote(record['distro'])}",
        f"    identity_file: {_yaml_quote(record['identity_file'])}",
        "    via: direct",
        "    options:",
        "      StrictHostKeyChecking: accept-new",
        "      ServerAliveInterval: 30",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--machines", type=Path, default=Path("machines.yaml"), help="Path to the harness machines.yaml")
    ap.add_argument("--machine", default=None, help="Machine key to emit (default: auto-detect the local machine)")
    ap.add_argument("--port", type=int, default=2200, help="WSL sshd loopback port (default 2200)")
    ap.add_argument("--distro", default="Ubuntu", help="WSL distro name for `wsl.exe -d` (default Ubuntu)")
    ap.add_argument("--user", default=None, help="Override the WSL login user (default: from machines.yaml wsl env)")
    ap.add_argument(
        "--identity-file",
        default=None,
        help="Client identity file for the alias (default: select an existing local keypair)",
    )
    ap.add_argument("--out", type=Path, help="Write registry YAML to this path instead of stdout")
    args = ap.parse_args(argv)

    data = _load_machines(args.machines)
    machines = data.get("machines", data) if isinstance(data, dict) else {}
    key = _local_machine_key(machines, args.machine)
    if not key:
        sys.stderr.write(
            f"Could not resolve the local machine in {args.machines} "
            f"(hostname={socket.gethostname().lower()!r}); pass --machine <key>.\n"
        )
        return 2

    env = _wsl_env(machines[key])
    if not env:
        sys.stderr.write(f"Machine '{key}' has no ssh.environments entry named 'wsl' in {args.machines}.\n")
        return 2

    name = env.get("alias") or f"{key}-wsl"
    user = args.user or env.get("user") or os.environ.get("USERNAME") or "agent"
    identity_file = args.identity_file or _select_identity_file(key)
    if not identity_file:
        sys.stderr.write(
            "No usable local SSH identity found under ~/.ssh "
            "(expected a private key with a sibling .pub); pass --identity-file <path>.\n"
        )
        return 2
    record = {
        "name": name,
        "user": user,
        "port": args.port,
        "distro": args.distro,
        "identity_file": identity_file,
    }
    text = render_registry(record)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
