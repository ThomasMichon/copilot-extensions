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
longer needed: ``build_provision_command`` pins git's per-host
``credential.<host>.helper`` for the ADO hosts and github.com to the relay-first
``~/ado-auth-helper`` wrapper, so headless ``git push`` resolves credentials
through the relay (the native config points ADO at the VS Code broker -- empty
headless -- and GitHub at a codespace-scoped token, #133/#112/#159).

The host-side relay server lives in the ``credential_relay`` lib (run by
agent-bridge; sources injected by agent-codespaces ``relay_provider``).
"""

from __future__ import annotations

import base64
import gzip
import shlex
from importlib import resources

__all__ = [
    "AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT",
    "AUTH_ERROR_POLICY_REMOTE_PATH",
    "asset_text",
    "build_auth_error_policy_command",
    "build_provision_command",
]

# Asset filename -> remote install path (relative to $HOME)
_RELAY_CLIENT = "ado-auth-helper-relay"
_WRAPPER = "ado-auth-helper-wrapper"
_AUTH_ERROR_POLICY = "auth-error-policy.instructions.md"
AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT = "$HOME/.agent-codespaces/custom-instructions"
AUTH_ERROR_POLICY_REMOTE_PATH = (
    f"{AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT}/.github/instructions/{_AUTH_ERROR_POLICY}"
)
_B64_CHUNK_SIZE = 6000
_GZIP_DECODE_PY = (
    "import gzip,sys; "
    "sys.stdout.buffer.write(gzip.decompress(sys.stdin.buffer.read()))"
)

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

# User-level npm config in a CodeSpace may contain a literal Azure Artifacts
# token baked during create/start. Remove only those token lines on connect so
# package tooling re-borrows through ado-auth-helper instead of trusting a stale
# file after resume. Registry/feed declarations stay intact.
_STALE_NPM_TOKEN_SCRUB = r"""
from pathlib import Path

p = Path.home() / ".npmrc"
try:
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
except OSError:
    raise SystemExit(0)

hosts = ("pkgs.dev.azure.com", ".visualstudio.com")
kept = []
changed = False
for line in lines:
    low = line.lower()
    if "_authtoken" in low and any(h in low for h in hosts):
        changed = True
        continue
    kept.append(line)

if changed:
    p.write_text("".join(kept), encoding="utf-8")
"""


def asset_text(name: str) -> str:
    """Return the text of a packaged CodeSpace asset."""
    return (resources.files(__package__) / name).read_text(encoding="utf-8")


def _b64(name: str) -> str:
    """Base64-encode a packaged asset for safe transport over SSH."""
    raw = asset_text(name).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _compressed_b64(text: str) -> str:
    raw = gzip.compress(text.encode("utf-8"))
    return base64.b64encode(raw).decode("ascii")


def _chunked_payload_pipeline(payload_b64: str, tmp_name: str, sink: str) -> str:
    """Build a shell snippet that reconstructs a compressed base64 payload.

    Keep each literal chunk well below Windows' command-line/token limits. The
    overall command stays compact because the transported payload is gzip'd
    before base64 encoding.
    """
    lines = [f'_f="$HOME/{tmp_name}.$$"; : > "$_f"']
    for i in range(0, len(payload_b64), _B64_CHUNK_SIZE):
        chunk = payload_b64[i : i + _B64_CHUNK_SIZE]
        lines.append(f"printf %s '{chunk}' >> \"$_f\"")
    lines.append(
        'base64 -d "$_f" '
        f"| python3 -c {shlex.quote(_GZIP_DECODE_PY)} {sink}; "
        'rm -f "$_f"'
    )
    return ";\n".join(lines)


def build_auth_error_policy_command() -> str:
    """Build a best-effort command that deploys the CodeSpace auth policy.

    Copilot CLI auto-loads marked ``*.instructions.md`` files from
    ``.github/instructions`` under each path in ``COPILOT_CUSTOM_INSTRUCTIONS_DIRS``.
    The dispatch launch prelude exports a stable agent-codespaces-owned root and
    writes the policy underneath it, so both ``agent-codespaces ssh --stdio`` and
    agent-bridge's detached Session Host path see the same behavior-mod.

    The command is idempotent (overwrite with identical content on every launch)
    and best-effort (deployment failures are swallowed so connect is never
    blocked by an instruction-file write).
    """
    policy_b64 = _compressed_b64(asset_text(_AUTH_ERROR_POLICY))
    deploy = _chunked_payload_pipeline(
        policy_b64,
        ".agent-codespaces-auth-error-policy.b64",
        f'> "{AUTH_ERROR_POLICY_REMOTE_PATH}"',
    )
    return (
        "( "
        f'mkdir -p "$(dirname "{AUTH_ERROR_POLICY_REMOTE_PATH}")";\n'
        f"{deploy};\n"
        f'chmod 0644 "{AUTH_ERROR_POLICY_REMOTE_PATH}" '
        ") || true; "
        'case ":${COPILOT_CUSTOM_INSTRUCTIONS_DIRS:-}:" in '
        f'*":{AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT}:"*) ;; '
        '*) export COPILOT_CUSTOM_INSTRUCTIONS_DIRS='
        '"${COPILOT_CUSTOM_INSTRUCTIONS_DIRS:+${COPILOT_CUSTOM_INSTRUCTIONS_DIRS}:}'
        f'{AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT}" ;; '
        "esac; "
    )


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

    Assets are transported as gzip-compressed base64 chunks so arbitrary script
    content survives the SSH command line without one oversized argv token.
    """
    relay_b64 = _compressed_b64(asset_text(_RELAY_CLIENT))
    wrapper_b64 = _compressed_b64(asset_text(_WRAPPER))
    profile_b64 = _compressed_b64(_NONINTERACTIVE_GIT_PROFILE)
    npm_scrub_b64 = _compressed_b64(_STALE_NPM_TOKEN_SCRUB)
    parts = [
        "set -e",
        'mkdir -p "$HOME/.local/bin"',
        # Relay client
        _chunked_payload_pipeline(
            relay_b64,
            ".agent-codespaces-relay-client.b64",
            '> "$HOME/.local/bin/ado-auth-helper-relay"',
        ),
        'chmod +x "$HOME/.local/bin/ado-auth-helper-relay"',
        # Remove stale Azure Artifacts npm tokens from the CodeSpace user's
        # config. Best-effort: a missing Python/npmrc must not block connect.
        "(\n"
        + _chunked_payload_pipeline(
            npm_scrub_b64,
            ".agent-codespaces-npm-scrub.b64",
            "| python3 -",
        )
        + "\n) || true",
        # Decode the smart wrapper once to a staging file
        _chunked_payload_pipeline(
            wrapper_b64,
            ".agent-codespaces-auth-wrapper.b64",
            '> "$HOME/.agent-codespaces-auth-wrapper"',
        ),
        # Install for both ado-auth-helper and azure-auth-helper
        'for _n in ado-auth-helper azure-auth-helper; do '
        # Back up the native helper once (skip if it is already our wrapper)
        'if [ -f "$HOME/$_n" ] && '
        '! grep -q ado-auth-helper-relay "$HOME/$_n" 2>/dev/null; then '
        'cp -f "$HOME/$_n" "$HOME/.$_n-vscode"; fi; '
        # Preserve the extension's node shebang only if it still resolves. A VS
        # Code server build rotation deletes the old /vscode/bin/<hash>/node, so
        # the backed-up shim's shebang can dangle -- and once our wrapper is
        # installed the backup is never refreshed, so a stale shebang would
        # otherwise persist across every redeploy and break `git push` with
        # `bad interpreter` (dotfiles #733). Validate the interpreter exists and
        # fall back to env-node when the pinned node is gone.
        '_sb=$(head -1 "$HOME/.$_n-vscode" 2>/dev/null || true); '
        '_interp=$(printf "%s" "$_sb" | sed -e "s/^#![[:space:]]*//" -e "s/[[:space:]].*$//"); '
        'case "$_sb" in "#!"*node*) [ -x "$_interp" ] || _sb="#!/usr/bin/env node" ;; '
        '*) _sb="#!/usr/bin/env node" ;; esac; '
        '{ printf "%s\\n" "$_sb"; tail -n +2 "$HOME/.agent-codespaces-auth-wrapper"; } '
        '> "$HOME/$_n"; '
        'chmod +x "$HOME/$_n"; '
        # Expose the bare name on PATH (~/.local/bin) so official bare-name
        # consumers (rush AdoCodespacesAuth, git, npm/nuget) resolve to our
        # shim. Headless the extension never runs, so HOME is not on PATH and
        # ~/<name> alone is unreachable by `Executable.spawnSync('<name>')`.
        'ln -sf "$HOME/$_n" "$HOME/.local/bin/$_n"; '
        'done; '
        'rm -f "$HOME/.agent-codespaces-auth-wrapper"',
        # --- #133/#112/#159: pin the relay-first git credential helper --------
        # The native git config points ADO (your-org.visualstudio.com /
        # dev.azure.com) at the VS Code broker (`external-git ado-helper`), which
        # returns EMPTY over headless SSH -> `git push` fails with "could not
        # read Username"; and GitHub at the codespace-scoped
        # `gitcredential_github.sh` -- a valid token, but scoped to the
        # CodeSpaces repo, so pushing to another GitHub repo (e.g. the
        # dotfiles/harness repo) 403s. Both fail headless even though the relay
        # itself serves working creds. Point these hosts at the relay-first
        # ~/ado-auth-helper wrapper (host identity over the relay): the leading
        # empty value resets any lower-priority helper so ours is authoritative,
        # and the wrapper falls back to the real VS Code helper when no relay is
        # active, so interactive VS Code auth is unaffected. Best-effort.
        "( "
        'for _h in "https://your-org.visualstudio.com" '
        '"https://dev.azure.com" "https://github.com"; do '
        'git config --global --unset-all "credential.${_h}.helper" 2>/dev/null || true; '
        'git config --global --add "credential.${_h}.helper" ""; '
        'git config --global --add "credential.${_h}.helper" "$HOME/ado-auth-helper"; '
        "done "
        ") || true",
        # --- #18: headless-boot git hardening ---------------------------------
        # Persist GIT_TERMINAL_PROMPT=0 for ALL login shells so a cold
        # start-from-stopped boot step (e.g. setup-agency calling
        # ado-auth-helper before the relay tunnel is up) fails fast instead of
        # hanging on an interactive `Username:` prompt in the start-waiter path.
        # Best-effort: sudo may be unavailable on some targets, so never fail
        # the whole provision command if this part can't run.
        "(\n"
        + _chunked_payload_pipeline(
            profile_b64,
            ".agent-codespaces-git-profile.b64",
            f"| sudo tee {_PROFILE_SNIPPET_PATH} >/dev/null "
            f"&& sudo chmod 0644 {_PROFILE_SNIPPET_PATH} "
            f"&& rm -f {_ENV_PROBE_CACHE}",
        )
        + "\n) || true",
    ]
    return ";\n".join(parts)
