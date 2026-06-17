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

# Headless-boot git hardening (#18). On a cold start-from-stopped, the
# devcontainer's postStart runs before the agent connects (so before the
# credential-relay tunnel exists). A boot step that calls ado-auth-helper for an
# ADO token therefore fails fast and may fall back to a plain-git interactive
# ``Username:`` prompt that HANGS in GitHub's start-waiter path, making every
# cold connect slow. Persisting GIT_TERMINAL_PROMPT=0 for all login shells makes
# that fallback fail fast ("terminal prompts disabled") instead; ADO auth then
# converges once the agent connects and the relay comes up.
#
# Scope note: GIT_TERMINAL_PROMPT=0 only suppresses git's OWN last-resort
# terminal username/password prompt. It does NOT disable credential helpers --
# the codespace's ado-auth-helper / GitHub helper, the VS Code ado-codespaces
# extension's interactive auth, and Git Credential Manager (where present) are
# all invoked first and keep working. So a later interactive VS Code session
# still authenticates normally; only git's legacy raw terminal prompt (a
# fallback that hangs headless) is turned off. We intentionally do NOT set
# GCM_INTERACTIVE here: that could suppress an interactive GCM prompt in a VS
# Code terminal, and it is unnecessary -- GIT_TERMINAL_PROMPT is what fixes the
# hang.
#
# This is deliberately unconditional (all login shells, not headless-only):
# suppressing git's inline prompt is *also* the better behavior in VS Code,
# where that native prompt surfaces as an awkward top-of-window password
# popup. Failing with a 401 and letting the proper credential helper / auth
# flow handle it is cleaner. So do NOT try to scope this to the boot path.
_PROFILE_SNIPPET_PATH = "/etc/profile.d/10-codespaces-noninteractive-git.sh"
_NONINTERACTIVE_GIT_PROFILE = (
    "# Deployed by agent-codespaces (#18): never block headless boot on git's\n"
    "# own interactive terminal prompt when the credential relay tunnel is down.\n"
    "# Credential helpers (ado-auth-helper, the VS Code auth extension, GCM)\n"
    "# still run and do their own interactive auth -- this only disables git's\n"
    "# legacy raw Username:/Password: terminal fallback, which hangs headless.\n"
    "# Auth converges once the agent connects and the relay is available.\n"
    "export GIT_TERMINAL_PROMPT=0\n"
)
# The devcontainer userEnvProbe env (login-interactive shell) is computed once
# at create and is NOT refreshed on restart. Deleting the cache forces
# ``devcontainer up`` to re-probe on the next start so the profile.d export
# above actually reaches postStart's environment.
_ENV_PROBE_CACHE = (
    "/workspaces/.codespaces/.persistedshare/devcontainers-cli/cache/"
    "env-loginInteractiveShell.json"
)


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
    - hardens headless boot against an interactive git prompt hang (#18, see
      :data:`_NONINTERACTIVE_GIT_PROFILE` below).

    The wrapper is relay-first and, when no relay is active, ``require()``s the
    REAL extension ``auth-helper.js`` discovered at runtime -- so VS Code auth
    keeps working after an SSH disconnect (no stale static backup).

    Assets are transported base64-encoded so arbitrary script content
    survives the SSH command line intact.
    """
    relay_b64 = _b64(_RELAY_CLIENT)
    wrapper_b64 = _b64(_WRAPPER)
    profile_b64 = base64.b64encode(
        _NONINTERACTIVE_GIT_PROFILE.encode("utf-8")
    ).decode("ascii")
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
        'rm -f "$HOME/.agent-codespaces-auth-wrapper"; '
        # --- #18: headless-boot git hardening ---------------------------------
        # Persist GIT_TERMINAL_PROMPT=0 for ALL login shells so a cold
        # start-from-stopped boot step (e.g. setup-agency calling
        # ado-auth-helper before the relay tunnel is up) fails fast instead of
        # hanging on an interactive `Username:` prompt in the start-waiter path.
        # Best-effort: sudo may be unavailable on some targets, so never fail
        # the whole provision command if this part can't run.
        "( "
        f"printf %s {profile_b64} | base64 -d "
        f"| sudo tee {_PROFILE_SNIPPET_PATH} >/dev/null "
        f"&& sudo chmod 0644 {_PROFILE_SNIPPET_PATH} "
        # The devcontainer userEnvProbe env is computed once at create and is
        # NOT refreshed on restart, so the export above would never reach
        # postStart without re-probing. Invalidate the cache so the next
        # `devcontainer up` re-probes with the snippet present.
        f"&& rm -f {_ENV_PROBE_CACHE} "
        ") || true"
    )
