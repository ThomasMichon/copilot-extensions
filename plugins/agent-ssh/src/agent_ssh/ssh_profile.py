"""agent-ssh :: core :: SSH profile emitter (transport-agnostic).

The generic framework every transport conforms to. Given (1) a transport
**module spec** (`module.yaml`, validated against contract/module.schema.json)
and (2) a normalized machine registry (contract/registry-record.schema.json),
it renders a coexistence-safe managed drop-in fragment of `Host <name>` blocks.

The core owns the MECHANISM: Host-block rendering, deterministic option
ordering, the `~/.ssh/config.d` managed-`Include` coexistence layout, atomic
fragment writes, and reachability probing. A TRANSPORT owns only the RECIPE: a
`proxy_command` template (how to dial the host) contributed via its module.yaml.
`cloudflared access ssh ...`, `dev-tunnel ...`, or nothing at all (direct) are
all just recipes; none are baked in here.

Coexistence contract (load-bearing): a single client may run many transports at
once, dispatched per machine by the registry `transport:` key. Each transport
writes ONLY its own fragment `~/.ssh/config.d/50-agent-ssh-<module>.conf` and a
single managed `Include` line; it never rewrites the whole config and never
touches a peer's fragment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT_INCLUDE = "Include ~/.ssh/config.d/*"

_OPTION_ORDER = (
    "HostName",
    "Port",
    "User",
    "IdentityFile",
    "IdentitiesOnly",
    "ProxyJump",
    "ProxyCommand",
    "StrictHostKeyChecking",
    "RemoteForward",
    "MACs",
)


def _ssh_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".ssh"


def load_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(text)
    except ModuleNotFoundError:
        return json.loads(text)


def fragment_name(module: str) -> str:
    """Per-transport drop-in filename. The 50- prefix orders transports; the
    module name namespaces them so no two transports collide."""
    return f"50-agent-ssh-{module}.conf"


def _render_proxy_command(template: str, *, hostname: str, machine: dict[str, Any],
                          proxy_binary: str) -> str:
    """Fill a transport's proxy_command template. Available placeholders:
    {hostname} {name} {user} {port} {proxy_binary}."""
    return template.format(
        hostname=hostname,
        name=machine.get("name", ""),
        user=machine.get("user", ""),
        port=machine.get("port", ""),
        proxy_binary=proxy_binary,
    )


def _emit_options(lines: list[str], opts: dict[str, Any]) -> None:
    remaining = dict(opts)
    for key in _OPTION_ORDER:
        if key in remaining and remaining[key] not in (None, ""):
            lines.append(f"    {key} {remaining.pop(key)}")
    for key in sorted(remaining):
        if remaining[key] not in (None, ""):
            lines.append(f"    {key} {remaining[key]}")


def render_gate_block(gate: dict[str, Any], module: dict[str, Any], proxy_binary: str) -> str:
    template = module.get("proxy_command")
    opts: dict[str, Any] = {
        "HostName": gate.get("hostname"),
        "User": gate.get("user"),
        "IdentityFile": gate.get("identity_file"),
        "StrictHostKeyChecking": gate.get("strict_host_key_checking", "accept-new"),
    }
    if template:
        opts["ProxyCommand"] = _render_proxy_command(
            template, hostname="%h", machine=gate, proxy_binary=proxy_binary
        )
    opts.update(gate.get("options", {}))
    lines = [f"Host {gate['name']}"]
    _emit_options(lines, opts)
    return "\n".join(lines)


def render_host_block(machine: dict[str, Any], module: dict[str, Any],
                      cfg: dict[str, Any]) -> str:
    """Render one `Host <name>` block, applying the transport's recipe."""
    proxy_binary = cfg.get("proxy_command_binary") or module.get("proxy_binary_default", "")
    template = module.get("proxy_command")
    via = machine.get("via", "direct")

    opts: dict[str, Any] = {
        "HostName": machine.get("hostname"),
        "Port": machine.get("port"),
        "User": machine.get("user"),
        "IdentityFile": machine.get("identity_file"),
    }

    if via == "jumpbox":
        gate = cfg.get("gate") or {}
        if not gate.get("name"):
            raise ValueError(
                f"machine '{machine['name']}' uses via=jumpbox but no top-level 'gate' is configured"
            )
        opts["ProxyJump"] = gate["name"]
    elif template:  # direct + a transport recipe -> dial via ProxyCommand
        host = machine.get("hostname") or "%h"
        opts["ProxyCommand"] = _render_proxy_command(
            template, hostname=host, machine=machine, proxy_binary=proxy_binary
        )
    # else: direct with no recipe (e.g. the `direct` transport) -> plain SSH.

    opts.update(machine.get("options", {}))
    opts.setdefault("StrictHostKeyChecking", "accept-new")

    lines = [f"Host {machine['name']}"]
    _emit_options(lines, opts)
    return "\n".join(lines)


def render_fragment(cfg: dict[str, Any], module: dict[str, Any]) -> str:
    name = module["module"]
    header = (
        f"# agent-ssh :: transport={name}\n"
        "# Managed drop-in -- generated from the machine registry; do not edit by hand.\n"
        f"# Owns ONLY the Host blocks for machines whose registry transport is '{name}'.\n"
    )
    blocks: list[str] = []
    if cfg.get("topology") == "jumpbox" and cfg.get("gate"):
        proxy_binary = cfg.get("proxy_command_binary") or module.get("proxy_binary_default", "")
        blocks.append(render_gate_block(cfg["gate"], module, proxy_binary))
    for machine in cfg.get("machines", []):
        blocks.append(render_host_block(machine, module, cfg))
    return f"{header}\n" + "\n\n".join(blocks) + "\n"


def ensure_root_include(ssh_config: Path | None = None) -> bool:
    ssh_config = ssh_config or (_ssh_dir() / "config")
    ssh_config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = ssh_config.read_text(encoding="utf-8") if ssh_config.exists() else ""
    if any(line.strip() == ROOT_INCLUDE for line in existing.splitlines()):
        return False
    ssh_config.write_text(f"{ROOT_INCLUDE}\n\n{existing}".rstrip() + "\n", encoding="utf-8")
    _chmod(ssh_config, 0o600)
    return True


def write_fragment(
    cfg: dict[str, Any],
    module: dict[str, Any],
    config_d: Path | None = None,
    ssh_config: Path | None = None,
) -> Path:
    config_d = config_d or (_ssh_dir() / "config.d")
    config_d.mkdir(mode=0o700, parents=True, exist_ok=True)
    _chmod(config_d, 0o700)  # mkdir(mode=) is a no-op for ACLs on Windows
    frag = config_d / fragment_name(module["module"])
    tmp = frag.with_suffix(".conf.tmp")
    tmp.write_text(render_fragment(cfg, module), encoding="utf-8")
    _chmod(tmp, 0o600)
    os.replace(tmp, frag)
    ensure_root_include(ssh_config)
    return frag


def _chmod(path: Path, mode: int) -> None:
    """Harden *path* to owner-only.

    On POSIX this is ``os.chmod(mode)``. On Windows ``os.chmod`` does NOT touch
    ACLs -- the file keeps an inherited ``OWNER RIGHTS`` (S-1-3-4) ACE that
    Windows OpenSSH rejects with *"Bad owner or permissions"*, so it refuses the
    ``Include`` and every ``ssh <machine>`` using this fragment fails. Reset
    inheritance and grant only the current user via ``icacls`` instead.
    """
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if not user:
            return
        dom = os.environ.get("USERDOMAIN")
        principal = f"{dom}\\{user}" if dom else user
        # Directories need (OI)(CI) so children inherit the user-only ACL.
        grant = f"{principal}:(OI)(CI)F" if path.is_dir() else f"{principal}:F"
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="emit-profile",
        description="Emit an agent-ssh managed SSH fragment from a transport module + machine registry.",
    )
    ap.add_argument("config", type=Path, help="Normalized machine registry (YAML/JSON).")
    ap.add_argument("--module", type=Path, required=True, help="Transport module.yaml (the recipe).")
    ap.add_argument("--config-d", type=Path, default=None, help="Override ~/.ssh/config.d.")
    ap.add_argument("--ssh-config", type=Path, default=None, help="Override ~/.ssh/config.")
    ap.add_argument("--print", action="store_true", help="Print the fragment; do not write.")
    args = ap.parse_args(argv)

    cfg = load_file(args.config)
    module = load_file(args.module)
    if "module" not in module or not isinstance(module.get("module"), str):
        print("[FAIL] module.yaml missing required 'module' name", file=sys.stderr)
        return 2

    if args.print:
        sys.stdout.write(render_fragment(cfg, module))
        return 0

    frag = write_fragment(cfg, module, config_d=args.config_d, ssh_config=args.ssh_config)
    print(f"[OK] wrote {len(cfg.get('machines', []))} host block(s) to {frag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
