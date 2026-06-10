"""Repo provisioning hooks -- deploy repo-declared files on SSH connect.

An adopting repo declares a ``provision`` block in its ``codespaces.yaml``
to deploy its own files (e.g. shell env snippets) and run setup commands
into a CodeSpace on every ``agent-codespaces ssh`` connect. This replaces
bespoke per-repo SSH wrappers: the repo-specific extras become data the
plugin applies by convention.

Generic relay setup (ado-auth-helper-relay + wrapper) is handled
separately in :mod:`agent_codespaces.codespace_assets`.
"""

from __future__ import annotations

import base64
import logging
import shlex
from pathlib import Path

from .config import ProvisionConfig, ProvisionFile

log = logging.getLogger("agent-codespaces")


def _resolve_src(pf: ProvisionFile) -> Path | None:
    """Resolve a provision file's ``src`` relative to its repo dir."""
    src = Path(pf.src)
    if not src.is_absolute() and pf.repo_dir is not None:
        src = pf.repo_dir / src
    if not src.is_file():
        log.warning("Provision src not found: %s", src)
        return None
    return src


def build_provision_command(provision: ProvisionConfig) -> str | None:
    """Build an idempotent bash command for a repo's provision hooks.

    Deploys each declared file (base64-encoded for safe transport) to its
    remote ``dest``, then runs any ``on_connect`` commands. Returns None
    if there is nothing to do.

    ``dest`` may start with ``~`` or ``$HOME``; parent directories are
    created. Missing source files are skipped with a warning.
    """
    parts: list[str] = ["set -e"]
    deployed = 0

    for pf in provision.files:
        src = _resolve_src(pf)
        if src is None:
            continue
        # Normalize CRLF -> LF: these are shell scripts deployed to Linux,
        # and the repo may be checked out on Windows with CRLF endings.
        raw = src.read_bytes().replace(b"\r\n", b"\n")
        payload = base64.b64encode(raw).decode("ascii")
        dest = pf.dest
        # Quote dest for the shell but leave ~ / $HOME expandable
        q_dest = dest.replace('"', '\\"')
        parts.append(f'mkdir -p "$(dirname "{q_dest}")"')
        parts.append(f'printf %s {payload} | base64 -d > "{q_dest}"')
        parts.append(f'chmod {shlex.quote(pf.mode)} "{q_dest}"')
        deployed += 1

    for cmd in provision.on_connect:
        parts.append(cmd)

    if deployed == 0 and not provision.on_connect:
        return None

    return "; ".join(parts)
