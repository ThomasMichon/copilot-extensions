"""CodeSpace-side relay helper assets.

These shell scripts are deployed *into* a CodeSpace by ``agent-codespaces
ssh`` so that ADO authentication works over the SSH credential-relay
tunnel:

- ``ado-auth-helper-relay`` -- the relay client. Proxies git-credential
  ``get`` and ``get-access-token`` requests over the SSH RemoteForward
  tunnel to the host's credential relay (and on to Git Credential
  Manager). Installed to ``~/.local/bin/ado-auth-helper-relay``.

- ``ado-auth-helper-wrapper`` -- a smart **Node** shim installed as both
  ``~/ado-auth-helper`` and ``~/azure-auth-helper``. When
  ``LC_GIT_CREDENTIAL_RELAY`` is set (or the tunnel port is reachable) it
  delegates to ``ado-auth-helper-relay``; otherwise it ``require()``s the
  REAL VS Code extension ``auth-helper.js`` (discovered at runtime), mirroring
  the extension's own shim so VS Code auth keeps working after an SSH
  disconnect -- rather than exec'ing a static backup that goes stale on
  extension updates.

The generic git-credential proxy that used to live alongside these is no
longer needed: the CodeSpace's native Git Credential Helper stack already
includes ``ado-auth-helper``, so git credentials resolve through it.

The host-side relay server lives in the ``credential_relay`` lib (run by
agent-bridge; sources injected by agent-codespaces ``relay_provider``).
"""

from __future__ import annotations

import base64
from importlib import resources

__all__ = ["asset_text", "build_provision_command"]

# Asset filename -> remote install path (relative to $HOME)
_RELAY_CLIENT = "ado-auth-helper-relay"
_WRAPPER = "ado-auth-helper-wrapper"


def asset_text(name: str) -> str:
    """Return the text of a packaged CodeSpace asset."""
    return (resources.files(__package__) / name).read_text(encoding="utf-8")


def _b64(name: str) -> str:
    """Base64-encode a packaged asset for safe transport over SSH."""
    raw = asset_text(name).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def build_provision_command() -> str:
    """Build an idempotent bash command that installs the relay helpers.

    The returned command is safe to run on every SSH connect:

    - writes ``~/.local/bin/ado-auth-helper-relay`` (the relay client)
    - installs the smart Node wrapper as BOTH ``~/ado-auth-helper`` and
      ``~/azure-auth-helper``, backing up each native helper to
      ``~/.<name>-vscode`` the first time (never backing up our own wrapper)
    - writes the wrapper with the **extension's own node shebang** (taken from
      the backed-up native shim) so it runs under the same node the extension
      used; falls back to ``/usr/bin/env node``.

    The wrapper is relay-first and, when no relay is active, ``require()``s the
    REAL extension ``auth-helper.js`` discovered at runtime -- so VS Code auth
    keeps working after an SSH disconnect (no stale static backup).

    Assets are transported base64-encoded so arbitrary script content
    survives the SSH command line intact.
    """
    relay_b64 = _b64(_RELAY_CLIENT)
    wrapper_b64 = _b64(_WRAPPER)
    return (
        "set -e; "
        'mkdir -p "$HOME/.local/bin"; '
        # Relay client
        f"printf %s {relay_b64} | base64 -d > \"$HOME/.local/bin/ado-auth-helper-relay\"; "
        'chmod +x "$HOME/.local/bin/ado-auth-helper-relay"; '
        # Decode the smart wrapper once to a staging file
        f"printf %s {wrapper_b64} | base64 -d > \"$HOME/.agent-codespaces-auth-wrapper\"; "
        # Install for both ado-auth-helper and azure-auth-helper
        'for _n in ado-auth-helper azure-auth-helper; do '
        # Back up the native helper once (skip if it is already our wrapper)
        'if [ -f "$HOME/$_n" ] && '
        '! grep -q ado-auth-helper-relay "$HOME/$_n" 2>/dev/null; then '
        'cp -f "$HOME/$_n" "$HOME/.$_n-vscode"; fi; '
        # Preserve the extension's node shebang if we can detect one
        '_sb=$(head -1 "$HOME/.$_n-vscode" 2>/dev/null || true); '
        'case "$_sb" in "#!"*node*) : ;; *) _sb="#!/usr/bin/env node" ;; esac; '
        '{ printf "%s\\n" "$_sb"; tail -n +2 "$HOME/.agent-codespaces-auth-wrapper"; } '
        '> "$HOME/$_n"; '
        'chmod +x "$HOME/$_n"; '
        # Expose the bare name on PATH (~/.local/bin) so official bare-name
        # consumers (rush AdoCodespacesAuth, git, npm/nuget) resolve to our
        # shim. Headless the extension never runs, so HOME is not on PATH and
        # ~/<name> alone is unreachable by `Executable.spawnSync('<name>')`.
        'ln -sf "$HOME/$_n" "$HOME/.local/bin/$_n"; '
        'done; '
        'rm -f "$HOME/.agent-codespaces-auth-wrapper"'
    )
