#!/usr/bin/env python3
"""Emit an agent-ssh normalized dtssh registry.

The static harness registry (machines.yaml) says which canonical machine aliases
participate in dtssh. The live tunnel id is intentionally NOT trusted from that
file: dtssh tunnel ids rotate. This script refreshes dtssh client state via
`dtssh discover` + `dtssh list` (dtssh's live source of truth) and maps aliases
to their current tunnel ids. Because `dtssh discover` also writes inline
`# >>> dtssh <<<` blocks into ~/.ssh/config, and agent-ssh instead owns those
hosts through its config.d fragment, the inline fences discover wrote are cleaned
after the live state is captured (skip the cleanup with --keep-inline; skip
discovery entirely with --skip-discover, though `dtssh list` then reflects only
whatever inline aliases still exist).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _default_dtssh_bin() -> str:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "dtssh" / "bin" / "dtssh.exe"
        if candidate.exists():
            return str(candidate)
    found = shutil.which("dtssh")
    return found or "dtssh"


def _default_client_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "dtssh" / "client"
    return Path.home() / ".local" / "share" / "dtssh" / "client"


def _default_ssh_config() -> Path:
    return Path.home() / ".ssh" / "config"


# dtssh discover writes inline `# >>> dtssh <alias> >>> ... # <<< dtssh <alias> <<<`
# fenced blocks into ~/.ssh/config. Once a machine is adopted onto agent-ssh (its
# hosts owned by the config.d fragment) those inline blocks are redundant and
# resurrect entries the operator retired -- so after capturing live state we strip
# exactly the fences discover (re)wrote (unless --keep-inline / --skip-discover).
_INLINE_DTSSH_FENCE = re.compile(
    r"(?ms)^[ \t]*#[ \t]*>>>[ \t]*dtssh\b.*?^[ \t]*#[ \t]*<<<[ \t]*dtssh\b[^\n]*\n?"
)


def clean_inline_dtssh_blocks(ssh_config: Path) -> int:
    """Strip inline `# >>> dtssh ... <<<` fenced blocks. Returns count removed."""
    if not ssh_config.exists():
        return 0
    original = ssh_config.read_text(encoding="utf-8")
    cleaned, count = _INLINE_DTSSH_FENCE.subn("", original)
    if count and cleaned != original:
        ssh_config.write_text(cleaned, encoding="utf-8")
    return count


def _parse_scalar(value: str) -> str | int:
    value = value.split("#", 1)[0].strip().strip('"').strip("'")
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    return value


def parse_machines_yaml(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_name: str | None = None
    in_machines = False
    in_dtssh = False
    dtssh: dict[str, Any] = {}

    def flush() -> None:
        nonlocal dtssh
        if current_name and dtssh:
            alias = str(dtssh.get("alias") or current_name)
            records.append(
                {
                    "machine_key": current_name,
                    "alias": alias,
                    "static_tunnel": dtssh.get("tunnel"),
                    "port": dtssh.get("port", 2222),
                }
            )
        dtssh = {}

    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if re.match(r"^machines:\s*$", raw):
            flush()
            current_name = None
            in_machines = True
            in_dtssh = False
            continue
        if not in_machines:
            continue
        machine_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$", raw)
        if machine_match:
            flush()
            current_name = machine_match.group(1)
            in_dtssh = False
            continue
        if current_name and re.match(r"^    dtssh:\s*(?:#.*)?$", raw):
            in_dtssh = True
            dtssh = {}
            continue
        if in_dtssh:
            field = re.match(r"^      ([A-Za-z0-9_-]+):\s*(.*?)\s*$", raw)
            if field:
                dtssh[field.group(1)] = _parse_scalar(field.group(2))
                continue
            # Any sibling block ends dtssh parsing for this machine.
            if re.match(r"^    [A-Za-z0-9_-]+:", raw):
                in_dtssh = False
    flush()
    return records


def run_dtssh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        raise SystemExit(proc.returncode)
    return proc


def parse_dtssh_list(text: str) -> dict[str, dict[str, str]]:
    live: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("ALIAS") or set(line) <= {"-", " "}:
            continue
        m = re.match(r"^(?P<alias>\S+)\s+(?P<user>\S+)\s+(?P<tunnel>\S+)\s+(?P<port>\d+)\b", line)
        if m:
            live[m.group("alias")] = m.groupdict()
    return live


def yaml_quote(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    text = str(value)
    # YAML 1.1 parsers (including PyYAML's safe_load) coerce yes/no/on/off to
    # booleans unless quoted; SSH expects the literal tokens.
    if text.lower() in {"yes", "no", "true", "false", "on", "off", "null", "~"}:
        return "\"" + text + "\""
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,\\-]+", text):
        return text
    return "\"" + text.replace("\\", "\\\\").replace('"', '\\"') + "\""


def render_registry(registry: dict[str, Any]) -> str:
    lines = ["transport: dtssh", "topology: per-machine"]
    if registry.get("proxy_command_binary"):
        lines.append(f"proxy_command_binary: {yaml_quote(registry['proxy_command_binary'])}")
    lines.append("machines:")
    for machine in registry["machines"]:
        lines.append(f"  - name: {yaml_quote(machine['name'])}")
        lines.append(f"    hostname: {yaml_quote(machine['hostname'])}")
        lines.append(f"    user: {yaml_quote(machine['user'])}")
        lines.append(f"    port: {yaml_quote(machine['port'])}")
        lines.append(f"    identity_file: {yaml_quote(machine['identity_file'])}")
        lines.append("    via: direct")
        lines.append("    options:")
        for key, value in machine["options"].items():
            lines.append(f"      {key}: {yaml_quote(value)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--machines", type=Path, default=Path("machines.yaml"), help="Path to the harness machines.yaml")
    ap.add_argument("--out", type=Path, help="Write registry YAML to this path instead of stdout")
    ap.add_argument("--dtssh-bin", default=_default_dtssh_bin(), help="dtssh binary to invoke")
    ap.add_argument("--skip-discover", action="store_true", help="Do not run `dtssh discover` (list-only). NOTE: `dtssh list` reads ssh_config managed aliases, so once the inline blocks are retired this yields nothing.")
    ap.add_argument("--keep-inline", action="store_true", help="Do not clean the inline `# >>> dtssh <<<` blocks discover writes into ~/.ssh/config (default: clean them, since agent-ssh owns those hosts via its config.d fragment).")
    ap.add_argument("--ssh-config", type=Path, default=None, help="ssh_config to clean of inline dtssh fences (default: ~/.ssh/config).")
    ap.add_argument("--allow-static-fallback", action="store_true", help="Use machines.yaml dtssh.tunnel only when live dtssh state lacks an alias")
    ap.add_argument("--no-proxy-binary-path", action="store_true", help="Use module.yaml proxy_binary_default ('dtssh') instead of the resolved dtssh path")
    args = ap.parse_args(argv)

    static = parse_machines_yaml(args.machines)
    if not static:
        sys.stderr.write(f"No dtssh blocks found in {args.machines}\n")
        return 2

    if not args.skip_discover:
        discover = run_dtssh([args.dtssh_bin, "discover", "-q"], check=False)
        if discover.returncode != 0:
            # devtunnel prints a one-time "Welcome"/EULA banner on the first
            # invocation of a session that pollutes dtssh discover's JSON parse
            # ("'W' is an invalid start of a value"); a single retry clears it.
            discover = run_dtssh([args.dtssh_bin, "discover", "-q"], check=False)
        if discover.returncode != 0:
            sys.stderr.write(discover.stderr or discover.stdout)
            return discover.returncode

    listed = run_dtssh([args.dtssh_bin, "list"], check=True)
    live = parse_dtssh_list(listed.stdout)
    # `dtssh discover` wrote inline `# >>> dtssh <<<` blocks into ~/.ssh/config
    # (the backing store `dtssh list` reads). Now that the live tunnel state is
    # captured, strip those inline fences: agent-ssh owns these hosts via its
    # config.d fragment, so leaving them resurrects entries retired at adoption.
    if not args.skip_discover and not args.keep_inline:
        removed = clean_inline_dtssh_blocks(args.ssh_config or _default_ssh_config())
        if removed:
            sys.stderr.write(
                f"[emit-registry] cleaned {removed} inline dtssh ssh_config block(s) written by discover\n"
            )
    client_dir = _default_client_dir()
    known_hosts = client_dir / "known_hosts"

    machines: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in static:
        alias = item["alias"]
        live_item = live.get(alias)
        if live_item:
            tunnel = live_item["tunnel"]
            port: str | int = int(live_item["port"])
            user = live_item["user"]
        elif args.allow_static_fallback and item.get("static_tunnel"):
            tunnel = str(item["static_tunnel"])
            port = item.get("port", 2222)
            user = os.environ.get("USERNAME") or os.environ.get("USER") or "<user>"
        else:
            missing.append(alias)
            continue
        machines.append(
            {
                "name": alias,
                "hostname": tunnel,
                "user": user,
                "port": port,
                "identity_file": str(client_dir / f"{alias}.key"),
                "via": "direct",
                "options": {
                    # dtssh pins each host key under the canonical machine name in
                    # its known_hosts, but HostName carries the *rotating* tunnel id
                    # -- so host-key lookup must be pinned to the machine name via
                    # HostKeyAlias, or StrictHostKeyChecking fails on every connect.
                    "HostKeyAlias": alias,
                    "IdentitiesOnly": "yes",
                    "UserKnownHostsFile": str(known_hosts),
                    "StrictHostKeyChecking": "yes",
                    "ServerAliveInterval": 30,
                },
            }
        )

    if missing:
        sys.stderr.write(
            "Missing live dtssh discovery for: "
            + ", ".join(missing)
            + "\nRun `dtssh login` and `dtssh discover`, or pass --allow-static-fallback only for bootstrap diagnostics.\n"
        )
        return 1

    registry: dict[str, Any] = {"transport": "dtssh", "topology": "per-machine", "machines": machines}
    if not args.no_proxy_binary_path:
        registry["proxy_command_binary"] = str(Path(args.dtssh_bin))
    text = render_registry(registry)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
