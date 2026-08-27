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
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags

from .file_lock import exclusive_file_lock

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT_INCLUDE = "Include ~/.ssh/config.d/*"
METADATA_PREFIX = "# agent-ssh-metadata: "
_TRANSPORT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_OPTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_RESERVED_OPTIONS = {"host", "include", "match"}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

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


def is_valid_transport(value: str) -> bool:
    """Whether *value* is a canonical transport module name."""
    return bool(_TRANSPORT_RE.fullmatch(value))


def is_valid_alias(value: str) -> bool:
    """Whether *value* is one exact, non-pattern OpenSSH Host alias."""
    return bool(_ALIAS_RE.fullmatch(value)) and not any(char in value for char in "*?!")


def _require_alias(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not is_valid_alias(value):
        raise ValueError(f"{field} must be one exact name using letters, digits, '.', '_' or '-'")
    return value


def _require_option(key: object, value: object) -> tuple[str, Any]:
    if not isinstance(key, str) or not _OPTION_RE.fullmatch(key):
        raise ValueError(f"invalid SSH option name {key!r}")
    if key.casefold() in _RESERVED_OPTIONS:
        raise ValueError(f"SSH option {key!r} is not allowed inside a managed fragment")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"SSH option {key!r} must have a scalar value")
    if isinstance(value, str) and any(char in value for char in "\r\n\0"):
        raise ValueError(f"SSH option {key!r} contains a control character")
    return key, value


def validate_profile_inputs(
    cfg: object,
    module: object,
    *,
    require_transport_match: bool = False,
) -> None:
    """Validate every source field consumed by the renderer."""
    if not isinstance(cfg, dict):
        raise ValueError("registry root must be a mapping")
    if not isinstance(module, dict):
        raise ValueError("module root must be a mapping")
    raw_name = module.get("module")
    if not isinstance(raw_name, str) or not is_valid_transport(raw_name):
        raise ValueError("module must use lowercase letters, digits, and '-'")
    registry_transport = cfg.get("transport")
    if not isinstance(registry_transport, str) or not is_valid_transport(
        registry_transport
    ):
        raise ValueError("registry transport must use lowercase letters, digits, and '-'")
    if require_transport_match and registry_transport != raw_name:
        raise ValueError("registry 'transport' must match module.yaml 'module'")

    template = module.get("proxy_command")
    if template is not None and not isinstance(template, str):
        raise ValueError("module proxy_command must be a string")
    proxy_default = module.get("proxy_binary_default")
    if proxy_default is not None and not isinstance(proxy_default, str):
        raise ValueError("module proxy_binary_default must be a string")
    topology = cfg.get("topology", "per-machine")
    if topology not in {"per-machine", "jumpbox"}:
        raise ValueError("registry topology must be 'per-machine' or 'jumpbox'")
    proxy_override = cfg.get("proxy_command_binary")
    if proxy_override is not None and not isinstance(proxy_override, str):
        raise ValueError("registry proxy_command_binary must be a string")

    if "machines" not in cfg:
        raise ValueError("registry machines is required")
    machines = cfg["machines"]
    if not isinstance(machines, list):
        raise ValueError("registry machines must be a list")
    aliases: set[str] = set()

    def reserve_alias(value: object, *, field: str) -> None:
        alias = _require_alias(value, field=field)
        folded = alias.casefold()
        if folded in aliases:
            raise ValueError(f"{field} duplicates another Host alias")
        aliases.add(folded)

    def optional_string(mapping: dict[str, Any], key: str, *, field: str) -> None:
        value = mapping.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{field} must be a string")

    def optional_port(mapping: dict[str, Any], *, field: str) -> None:
        value = mapping.get("port")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (str, int))
        ):
            raise ValueError(f"{field} must be a string or integer")

    gate = cfg.get("gate")
    if gate is not None:
        if not isinstance(gate, dict):
            raise ValueError("registry gate must be a mapping")
        reserve_alias(gate.get("name"), field="gate.name")
        for key in ("hostname", "user", "identity_file", "strict_host_key_checking"):
            optional_string(gate, key, field=f"gate.{key}")
        options = gate.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("registry gate.options must be a mapping")
        for key, value in options.items():
            _require_option(key, value)

    for index, machine in enumerate(machines):
        if not isinstance(machine, dict):
            raise ValueError(f"registry machines[{index}] must be a mapping")
        reserve_alias(machine.get("name"), field=f"machines[{index}].name")
        for key in ("hostname", "user", "identity_file", "distro"):
            optional_string(machine, key, field=f"machines[{index}].{key}")
        optional_port(machine, field=f"machines[{index}].port")
        via = machine.get("via", "direct")
        if via not in {"direct", "jumpbox"}:
            raise ValueError(f"registry machines[{index}].via is invalid")
        options = machine.get("options", {})
        if not isinstance(options, dict):
            raise ValueError(f"registry machines[{index}].options must be a mapping")
        for key, value in options.items():
            _require_option(key, value)


def fragment_name(module: str) -> str:
    """Per-transport drop-in filename. The 50- prefix orders transports; the
    module name namespaces them so no two transports collide."""
    if not is_valid_transport(module):
        raise ValueError("module must use lowercase letters, digits, and '-'")
    return f"50-agent-ssh-{module}.conf"


def _render_proxy_command(template: str, *, hostname: str, machine: dict[str, Any],
                          proxy_binary: str) -> str:
    """Fill a transport's proxy_command template. Available placeholders:
    {hostname} {name} {user} {port} {distro} {proxy_binary}. ({distro} is used by
    local-machine transports such as `wsl`; other transports simply ignore it.)"""
    return template.format(
        hostname=hostname,
        name=machine.get("name", ""),
        user=machine.get("user", ""),
        port=machine.get("port", ""),
        distro=machine.get("distro", ""),
        proxy_binary=proxy_binary,
    )


def _emit_options(lines: list[str], opts: dict[str, Any]) -> None:
    remaining = dict(_require_option(key, value) for key, value in opts.items())
    for key in _OPTION_ORDER:
        if key in remaining and remaining[key] not in (None, ""):
            value = remaining.pop(key)
            if isinstance(value, bool):
                value = "yes" if value else "no"
            lines.append(f"    {key} {value}")
    for key in sorted(remaining):
        if remaining[key] not in (None, ""):
            value = remaining[key]
            if isinstance(value, bool):
                value = "yes" if value else "no"
            lines.append(f"    {key} {value}")


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
    lines = [f"Host {_require_alias(gate.get('name'), field='gate.name')}"]
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

    lines = [f"Host {_require_alias(machine.get('name'), field='machine.name')}"]
    _emit_options(lines, opts)
    return "\n".join(lines)


def render_fragment(
    cfg: dict[str, Any],
    module: dict[str, Any],
    *,
    registry_path: Path | None = None,
    module_path: Path | None = None,
) -> str:
    validate_profile_inputs(cfg, module)
    raw_name = module["module"]
    name = raw_name
    header = (
        f"# agent-ssh :: transport={name}\n"
    )
    if (registry_path is None) != (module_path is None):
        raise ValueError("registry_path and module_path must be provided together")
    if registry_path is not None and module_path is not None:
        metadata = {
            "module": str(module_path),
            "registry": str(registry_path),
            "schema_version": 1,
            "transport": name,
        }
        header += METADATA_PREFIX + json.dumps(
            metadata,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
    header += (
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


def profile_aliases(cfg: dict[str, Any]) -> tuple[str, ...]:
    """Return the exact aliases a validated registry renders."""
    aliases: list[str] = []
    if cfg.get("topology") == "jumpbox" and cfg.get("gate"):
        aliases.append(str(cfg["gate"]["name"]))
    aliases.extend(str(machine["name"]) for machine in cfg.get("machines", []))
    return tuple(aliases)


def openssh_syntax_error(path: Path, aliases: tuple[str, ...]) -> str | None:
    """Return OpenSSH's parse failure for *path*, without connecting."""
    ssh = shutil.which("ssh")
    if not ssh:
        return "OpenSSH client executable `ssh` is not available"
    targets = aliases or ("agent-ssh-syntax-check",)
    for target in targets:
        proc = subprocess.run(
            [ssh, "-G", "-F", str(path), target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            creationflags=no_window_flags(),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()
            return detail or f"ssh -G exited {proc.returncode}"
    return None


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _root_config_target(path: Path) -> Path:
    """Resolve a regular root config target without replacing its symlink."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path.expanduser().resolve(strict=False)
    if stat.S_ISLNK(info.st_mode):
        try:
            target = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise OSError(f"SSH config symlink target does not exist: {path}") from exc
        target_info = target.lstat()
        if (
            not stat.S_ISREG(target_info.st_mode)
            or stat.S_ISLNK(target_info.st_mode)
            or _is_reparse(target_info)
        ):
            raise OSError(f"SSH config symlink target must be a regular file: {target}")
        return target
    if _is_reparse(info):
        raise OSError(f"SSH config must not be an unsupported reparse point: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"SSH config must be a regular file: {path}")
    return path


def _regular_config_directory(path: Path) -> Path:
    """Return the canonical managed directory, rejecting links/reparse points."""
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        raise OSError(
            f"managed config.d must be a regular non-reparse directory: {path}"
        )
    return path.resolve(strict=True)


def _include_line(config_d: Path | None = None) -> str:
    if config_d is None:
        return ROOT_INCLUDE
    default = (_ssh_dir() / "config.d").resolve()
    resolved = config_d.expanduser().resolve()
    if resolved == default:
        return ROOT_INCLUDE
    text = resolved.as_posix()
    if any(char in text for char in "\r\n\0\""):
        raise ValueError("config.d path cannot be represented safely in an SSH Include")
    return f'Include "{text}/*"'


def ensure_root_include(
    ssh_config: Path | None = None,
    *,
    config_d: Path | None = None,
) -> bool:
    ssh_config = ssh_config or (_ssh_dir() / "config")
    include_line = _include_line(config_d)
    ssh_config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = _root_config_target(ssh_config)
    lock_path = target.parent / ".agent-ssh-locks" / "root-config.lock"
    with exclusive_file_lock(lock_path):
        current_target = _root_config_target(ssh_config)
        if current_target != target:
            raise OSError(
                f"SSH config target changed while waiting for the write lock: {ssh_config}"
            )
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if any(line.strip() == include_line for line in existing.splitlines()):
            return False
        content = f"{include_line}\n\n{existing}".rstrip() + "\n"
        fd, temporary = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=".agent-ssh-root-config-",
            suffix=".tmp",
        )
        tmp = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _chmod(tmp, 0o600)
            os.replace(tmp, target)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return True


def write_fragment(
    cfg: dict[str, Any],
    module: dict[str, Any],
    config_d: Path | None = None,
    ssh_config: Path | None = None,
    registry_path: Path | None = None,
    module_path: Path | None = None,
) -> Path:
    validate_profile_inputs(cfg, module, require_transport_match=True)
    config_d = config_d or (_ssh_dir() / "config.d")
    config_d.mkdir(mode=0o700, parents=True, exist_ok=True)
    canonical_config_d = _regular_config_directory(config_d)
    _chmod(config_d, 0o700)  # mkdir(mode=) is a no-op for ACLs on Windows
    frag = config_d / fragment_name(module["module"])
    target_frag = canonical_config_d / frag.name
    lock_path = config_d.parent / ".agent-ssh-locks" / f"{frag.name}.lock"
    with exclusive_file_lock(lock_path):
        fd, temporary = tempfile.mkstemp(
            dir=str(canonical_config_d.parent),
            prefix=".agent-ssh-fragment-",
            suffix=".tmp",
        )
        tmp = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    render_fragment(
                        cfg,
                        module,
                        registry_path=registry_path,
                        module_path=module_path,
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            _chmod(tmp, 0o600)
            syntax_error = openssh_syntax_error(tmp, profile_aliases(cfg))
            if syntax_error:
                raise ValueError(
                    f"OpenSSH rejected the managed fragment: {syntax_error}"
                )
            os.replace(tmp, target_frag)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    ensure_root_include(ssh_config, config_d=config_d)
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
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            raise OSError("SystemRoot is unavailable; cannot harden Windows ACL")
        system32 = Path(system_root) / "System32"
        identity = subprocess.run(
            [str(system32 / "whoami.exe")],
            capture_output=True,
            text=True,
            check=False,
        )
        principal = identity.stdout.strip() if identity.returncode == 0 else ""
        if not principal:
            user = os.environ.get("USERNAME")
            if not user:
                raise OSError("cannot resolve the current Windows principal")
            domain = os.environ.get("USERDOMAIN")
            principal = f"{domain}\\{user}" if domain else user
        # Directories need (OI)(CI) so children inherit the user-only ACL.
        grant = f"{principal}:(OI)(CI)F" if path.is_dir() else f"{principal}:F"
        result = subprocess.run(
            [
                str(system32 / "icacls.exe"),
                str(path),
                "/inheritance:r",
                "/grant:r",
                grant,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if not detail:
                detail = f"icacls exited {result.returncode}"
            raise OSError(f"failed to harden Windows ACL for {path}: {detail}")
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
    try:
        validate_profile_inputs(cfg, module, require_transport_match=True)
    except ValueError as exc:
        print(f"[FAIL] invalid profile sources: {exc}", file=sys.stderr)
        return 2
    assert isinstance(cfg, dict)
    assert isinstance(module, dict)

    try:
        if args.print:
            sys.stdout.write(
                render_fragment(
                    cfg,
                    module,
                    registry_path=args.config.resolve(strict=True),
                    module_path=args.module.resolve(strict=True),
                )
            )
            return 0

        frag = write_fragment(
            cfg,
            module,
            config_d=args.config_d,
            ssh_config=args.ssh_config,
            registry_path=args.config.resolve(strict=True),
            module_path=args.module.resolve(strict=True),
        )
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot emit managed SSH fragment: {exc}", file=sys.stderr)
        return 2
    print(f"[OK] wrote {len(cfg.get('machines', []))} host block(s) to {frag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
