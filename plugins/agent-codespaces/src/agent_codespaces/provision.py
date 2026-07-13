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

from .config import DOTFILES_DIR, ProvisionConfig, ProvisionFile

log = logging.getLogger("agent-codespaces")

# ``DOTFILES_DIR`` is defined canonically in :mod:`agent_codespaces.config` (the
# layer the request-folder resolver shares) and re-exported here for back-compat
# with existing ``from .provision import DOTFILES_DIR`` importers. The harness
# analogue ``HARNESS_DIR`` is passed in as ``harness_dir`` (see
# ``build_harness_command``), so it is not imported here.


def build_dotfiles_command(dotfiles_repo: str, relay_port: int) -> str:
    """Build an idempotent bash command that ensures the dotfiles repo is
    present and current on a CodeSpace.

    This is the **universal** dotfiles bootstrap, run for every CodeSpace when
    ``defaults.dotfiles_repo`` is set -- it is built-in plugin behavior, not a
    per-repo ``on_create`` hook (those are reserved for genuine extras, e.g.
    cloning an *additional* repo). Behavior:

    - **Absent** (``$df/.git`` missing): clone the repo and run its ``install.sh``.
    - **Present, on the default branch, clean**: ``fetch`` + ``--ff-only`` and
      re-run ``install.sh`` *only* when the fast-forward moved ``HEAD`` (fast
      no-op otherwise).
    - **On a feature branch or dirty**: **never touched** -- the command prints a
      directive instead, so a human/agent can sync the parked work deliberately.

    Auth for the clone/fetch rides the credential relay, matching the env the
    create-time hook used (``LC_GIT_CREDENTIAL_RELAY`` + non-interactive git).
    ``install.sh`` is the dotfiles repo's own idempotent installer.
    """
    url = shlex.quote(f"https://github.com/{dotfiles_repo}")
    df = shlex.quote(DOTFILES_DIR)
    port = int(relay_port)
    return f"""\
export LC_GIT_CREDENTIAL_RELAY={port} GIT_TERMINAL_PROMPT=0
df={df}
if [ ! -d "$df/.git" ]; then
  # A non-git directory here is a broken/partial native dotfiles clone (e.g. the
  # post-create clone was interrupted). git refuses to clone into a non-empty
  # dir, so clear it first.
  if [ -e "$df" ]; then
    echo "[dotfiles] removing partial non-git dir at $df"
    rm -rf "$df"
  fi
  echo "[dotfiles] cloning {dotfiles_repo}"
  if git clone --depth 1 {url} "$df"; then
    bash "$df/install.sh" || {{ echo "[dotfiles] install FAILED" >&2; exit 1; }}
  else
    echo "[dotfiles] clone FAILED" >&2
    exit 1
  fi
else
  br=$(git -C "$df" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
  def=$(git -C "$df" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
  [ -n "$def" ] || def=main
  if [ "$br" != "$def" ] || [ -n "$(git -C "$df" status --porcelain 2>/dev/null)" ]; then
    echo "[dotfiles] $df is on '$br' (default '$def') or has local changes -- NOT syncing. Sync it yourself if you parked work here."
  else
    before=$(git -C "$df" rev-parse HEAD 2>/dev/null)
    git -C "$df" fetch --quiet origin "$def" 2>/dev/null && git -C "$df" merge --ff-only --quiet "origin/$def" 2>/dev/null
    after=$(git -C "$df" rev-parse HEAD 2>/dev/null)
    if [ "$before" != "$after" ]; then
      echo "[dotfiles] synced to $after -- reinstalling"
      bash "$df/install.sh" || {{ echo "[dotfiles] install FAILED" >&2; exit 1; }}
    else
      echo "[dotfiles] up to date"
    fi
  fi
fi"""


def build_harness_command(
    harness_repo: str, harness_dir: str, relay_port: int
) -> str:
    """Idempotent bash ensuring the control-plane *harness* checkout is present
    and current on a venue at ``harness_dir``.

    The harness analogue of :func:`build_dotfiles_command`, with two deliberate
    differences:

    - **No ``install.sh``.** The harness is a checkout referenced *in place* for
      effort / vision state -- it is not an installer, so nothing is run after
      clone/sync.
    - **Distinct path + labels.** Materialized at ``harness_dir``
      (a generic ``/workspaces/harness`` by default), kept separate from the
      class-D dotfiles shim at ``DOTFILES_DIR``.

    Same safety as the dotfiles bootstrap: clone-if-absent; on the default
    branch and clean, ``fetch`` + ``--ff-only``; on a feature branch or dirty,
    **never touched** (prints a directive so parked work is synced deliberately).
    Auth for the clone/fetch rides the credential relay. This is only invoked
    when ``defaults.harness_repo`` is set (opt-in); by default no harness is
    placed on the venue.
    """
    url = shlex.quote(f"https://github.com/{harness_repo}")
    hd = shlex.quote(harness_dir)
    port = int(relay_port)
    return f"""\
export LC_GIT_CREDENTIAL_RELAY={port} GIT_TERMINAL_PROMPT=0
hd={hd}
if [ ! -d "$hd/.git" ]; then
  # A non-git directory here is a broken/partial clone; git refuses to clone
  # into a non-empty dir, so clear it first.
  if [ -e "$hd" ]; then
    echo "[harness] removing partial non-git dir at $hd"
    rm -rf "$hd"
  fi
  echo "[harness] cloning {harness_repo}"
  git clone --depth 1 {url} "$hd" || {{ echo "[harness] clone FAILED" >&2; exit 1; }}
else
  br=$(git -C "$hd" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
  def=$(git -C "$hd" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
  [ -n "$def" ] || def=main
  if [ "$br" != "$def" ] || [ -n "$(git -C "$hd" status --porcelain 2>/dev/null)" ]; then
    echo "[harness] $hd is on '$br' (default '$def') or has local changes -- NOT syncing. Sync it yourself if you parked work here."
  else
    git -C "$hd" fetch --quiet origin "$def" 2>/dev/null && git -C "$hd" merge --ff-only --quiet "origin/$def" 2>/dev/null
    echo "[harness] up to date"
  fi
fi"""


def _resolve_src(pf: ProvisionFile) -> Path | None:
    """Resolve a provision file's ``src`` relative to its repo dir."""
    src = Path(pf.src)
    if not src.is_absolute() and pf.repo_dir is not None:
        src = pf.repo_dir / src
    if not src.is_file():
        log.warning("Provision src not found: %s", src)
        return None
    return src


def build_provision_command(
    provision: ProvisionConfig, *, include_on_create: bool = False,
) -> str | None:
    """Build an idempotent bash command for a repo's provision hooks.

    Deploys each declared file (base64-encoded for safe transport) to its
    remote ``dest``, then runs any ``on_connect`` commands. When
    ``include_on_create`` is set, ``on_create`` commands run last (used
    once, right after creation). Returns None if there is nothing to do.

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
        # Expand a leading ~ to $HOME so the path resolves inside the
        # double quotes below (bash does not expand ~ when quoted).
        dest = pf.dest
        if dest == "~" or dest.startswith("~/"):
            dest = "$HOME" + dest[1:]
        q_dest = dest.replace('"', '\\"')
        parts.append(f'mkdir -p "$(dirname "{q_dest}")"')
        parts.append(f'printf %s {payload} | base64 -d > "{q_dest}"')
        parts.append(f'chmod {shlex.quote(pf.mode)} "{q_dest}"')
        deployed += 1

    for cmd in provision.on_connect:
        parts.append(cmd)

    on_create = provision.on_create if include_on_create else []
    for cmd in on_create:
        parts.append(cmd)

    if deployed == 0 and not provision.on_connect and not on_create:
        return None

    return "; ".join(parts)
