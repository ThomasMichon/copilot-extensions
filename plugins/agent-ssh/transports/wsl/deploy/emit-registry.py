#!/usr/bin/env python3
"""Emit an agent-ssh normalized **wsl** registry for THIS machine's own WSL.

The `wsl` transport is local: it reaches the Windows machine's own WSL sshd
through the `wsl.exe` interop stdio pipe (GSA-safe), so the registry it needs is
tiny -- a single record for the local machine's WSL target. This script reads the
harness `machines.yaml`, resolves its logical WSL machine through the shared
machine model, and emits a normalized registry the core `emit-profile` renders into
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

from machine_transport import (
    MachineEntry,
    find_machine_entry,
    normalize_machine_entries,
    parse_machines_yaml,
    resolve_ssh_target,
)


def _load_machines(path: Path) -> dict[str, MachineEntry]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except ModuleNotFoundError:  # pragma: no cover - yaml is a package dep
        import json
        data = json.loads(text)
    data = data or {}
    if isinstance(data, dict) and "machines" not in data:
        data = {"machines": data}
    return normalize_machine_entries(parse_machines_yaml(data))


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

    try:
        machines = _load_machines(args.machines)
        machine = find_machine_entry(
            machines, args.machine or socket.gethostname(),
            reject_ambiguous=True, execution_platform="wsl",
        )
    except ValueError as exc:
        sys.stderr.write(f"Invalid machine topology: {exc}\n")
        return 2
    if machine is None or machine.execution_platform != "wsl":
        sys.stderr.write(
            f"Could not resolve the local machine in {args.machines} "
            f"(hostname={socket.gethostname().lower()!r}); pass --machine <key>.\n"
        )
        return 2

    if not machine.ssh_environments:
        sys.stderr.write(f"Machine '{machine.key}' has no WSL SSH profile in {args.machines}.\n")
        return 2

    name, _ = resolve_ssh_target(machine)
    name = name or machine.key
    env = machine.ssh_environments[0]  # shared resolution has refused duplicate profiles
    user = args.user or env.user or os.environ.get("USERNAME") or "agent"
    identity_file = args.identity_file or _select_identity_file(machine.physical_host or machine.key)
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
