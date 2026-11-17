"""Relay launch prelude for the detached Session Host path.

agent-bridge's ``CodeSpaceSpawner`` launches copilot **detached** on the
CodeSpace (``setsid nohup``), not via ``agent-codespaces ssh``, so it must
reproduce the launch prelude the ssh path injects: neutralize injected static
PATs (#160/#77) so a dispatched agent never relies on a stale token instead of
the credential relay, publish the agent-codespaces custom-instructions root,
export ``LC_GIT_CREDENTIAL_RELAY`` + the per-codespace token, and disable
interactive git/GCM prompts. This is the **public seam** agent-bridge calls
(guarded import) so the ssh path and the Session-Host path stay in lockstep.
"""

from __future__ import annotations

import asyncio
import os
import json
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags
from credential_relay.readiness import build_relay_ping_probe_command

# Static PATs a CodeSpace injects that must be neutralized so a dispatched agent
# never relies on a stale/expired token instead of the credential relay.
SCRUB_ENV_VARS: tuple[str, ...] = (
    "MS_ADO_PAT",
    "AZURE_ARTIFACTS_ENV_ACCESS_TOKEN",
    "VSS_NUGET_ACCESSTOKEN",
    "VSS_NUGET_EXTERNAL_FEED_ENDPOINTS",
    "ARTIFACTS_CREDENTIALPROVIDER_FEED_ENDPOINTS",
)

# Directory on the CodeSpace where the launch prelude publishes credential-relay
# port-mapping files (one JSON per live relay port). The ADO auth helpers
# discover a working relay by enumerating these and probing each for a live TCP
# channel -- so a dispatched tool shell that never inherited
# ``LC_GIT_CREDENTIAL_RELAY`` (or inherited a since-torn-down port) can still
# find an active channel back to the caller (dotfiles #489/#187/#19). Kept under
# the same ``~/.agent-bridge`` dir the connect breadcrumb uses.
RELAY_PORTMAP_DIR = "$HOME/.agent-bridge/relay-ports"  # marketplace-isolation: allow registry

# Directory the launch prelude symlinks the RushStack-facing bare
# ``azure-auth-helper`` name into. It sits outside the default PATH and is
# only reachable when THIS launch's prelude exports it inline (see
# ``build_azure_auth_helper_compat_shim``) -- never persisted to a dotfile, so
# it never survives past this one launch's shell.
AZURE_AUTH_HELPER_COMPAT_DIR = "$HOME/.cache/agent-codespaces/compat-path"
_SUBPROCESS_FLAGS = no_window_flags()
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _az_argv(rest: list[str]) -> list[str] | None:
    """Build an argv that can launch the Azure CLI cross-platform."""
    az = shutil.which("az")
    if not az:
        return None
    if sys.platform == "win32" and az.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", az, *rest]
    return [az, *rest]


def current_identity(*, timeout: float = 10.0) -> str | None:
    """Return the current host Azure-login identity string, or ``None``."""
    args = _az_argv(["account", "show", "--output", "json"])
    if args is None:
        return None
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_SUBPROCESS_FLAGS,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return None
    raw = str(user.get("name") or "").strip()
    if not raw or any(ch in raw for ch in "\r\n\0"):
        return None
    if str(user.get("type") or "").strip().casefold() == "user" and "@" in raw:
        alias = raw.split("@", 1)[0].strip()
        if alias:
            return alias
    return raw


def _valid_env_names(var_names: Any) -> list[str]:
    """Return only safe shell env-var names."""
    return [
        name for name in (var_names or ())
        if isinstance(name, str) and _ENV_VAR_NAME_RE.fullmatch(name)
    ]


def build_azure_auth_helper_compat_shim() -> str:
    """POSIX snippet exposing bare ``azure-auth-helper`` on PATH for one launch.

    RushStack's ``@rushstack/rush-azure-storage-build-cache-plugin``
    ``AdoCodespacesAuthCredential`` hard-codes
    ``Executable.spawnSync("azure-auth-helper", ...)`` with no override, so it
    resolves the bare name through the current process's ``PATH``. #404
    correctly stopped *persistently* installing a same-named shadow at
    ``~/azure-auth-helper`` / ``~/.local/bin/azure-auth-helper`` -- that shadow
    broke Azure CLI's own native ``azure-auth-helper``, whose verbs the ADO
    relay does not implement. A clean headless CodeSpace (no VS Code server)
    then has no executable of that name at all, so ``AdoCodespacesAuth`` fails
    before attempt 1 (#415).

    This maps the bare name to the relay-first ``ado-auth-helper`` wrapper
    (installed by ``codespace_assets.build_provision_command``) ONLY for the
    duration of this one launch's shell: ``PATH`` is exported inline in the
    returned snippet, never written to ``~/.bashrc``, ``~/.profile``, or any
    persisted dotfile, so a fresh interactive VS Code session or a plain
    ``az login`` never inherits it. ``AZURE_AUTH_HELPER_COMPAT_DIR`` itself
    sits outside the default PATH, so it stays unreachable outside a launch
    that includes this prelude.
    """
    d = AZURE_AUTH_HELPER_COMPAT_DIR
    return (
        f'mkdir -p "{d}"; '
        f'ln -sf "{ADO_AUTH_HELPER}" "{d}/azure-auth-helper"; '
        f'export PATH="{d}:$PATH"; '
    )


def relay_listening(port: int, timeout: float = 0.5) -> bool:
    """True if the host credential relay accepts TCP on 127.0.0.1:*port*."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except (OSError, TypeError, ValueError):
        return False


def warn_if_relay_unavailable(
    relay_port: int,
    codespace_name: str,
    *,
    context: str = "CodeSpace connect",
) -> bool:
    """Warn loudly when the host relay is down before an SSH ``-R`` is built."""
    if relay_listening(relay_port):
        return True
    print(
        f"[WARN] Host credential relay is NOT listening on "
        f"127.0.0.1:{relay_port} -- the SSH -R forward will dead-end and "
        f"git auth over the relay (ADO push, GitHub push, headless PR/REST) "
        f"will FAIL on CodeSpace '{codespace_name}' during {context}. The relay "
        f"is owned by the agent-bridge daemon; start/repair it with "
        f"`agent-bridge service restart`, then reconnect. (#560)",
        file=sys.stderr,
    )
    return False


async def ssh_relay_serving(
    manager: Any, codespace_name: str, relay_port: int, *, timeout: float | None = None,
) -> bool:
    """Check the expected far-side listener without requesting credentials."""
    from ._ssh_retry import exec_with_retry

    budget = timeout if timeout is not None and timeout > 0 else 30.0
    command = f"bash -l -c {shlex.quote(build_relay_ping_probe_command(relay_port))}"
    try:
        result = await asyncio.wait_for(
            exec_with_retry(
                manager, codespace_name, command, timeout=budget, attempts=1,
            ),
            timeout=budget,
        )
        return (
            not getattr(result, "timed_out", False)
            and result.exit_code == 0
            and (result.stdout or "").strip() == "OK"
        )
    except Exception:
        return False


def build_relay_portmap_write(relay_port: int) -> str:
    """POSIX snippet that publishes a relay port-mapping file (best-effort).

    Writes ``<RELAY_PORTMAP_DIR>/<port>.json`` =
    ``{"port","token","ado_host","github_account","ts"}`` with a restrictive umask, reading the
    secret from the just-exported ``LC_GIT_CREDENTIAL_RELAY_TOKEN`` (never
    re-interpolated) and non-secret host/account metadata from
    ``LC_GIT_CREDENTIAL_RELAY_ADO_HOST`` / ``LC_GIT_CREDENTIAL_RELAY_GITHUB_ACCOUNT``.
    Never aborts the prelude (``|| true``).
    Keyed by port so repeat launches to the same relay are idempotent; a stale
    file whose channel later dies is pruned by the auth helpers' liveness probe
    (the discovery reader), not here.
    """
    d = RELAY_PORTMAP_DIR
    return (
        f'mkdir -p "{d}" 2>/dev/null; '
        '( umask 177; printf \'{"port":%s,"token":"%s","ado_host":"%s","github_account":"%s","ts":%s}\\n\' '
        f'{relay_port} "$LC_GIT_CREDENTIAL_RELAY_TOKEN" '
        '"${LC_GIT_CREDENTIAL_RELAY_ADO_HOST:-}" '
        '"${LC_GIT_CREDENTIAL_RELAY_GITHUB_ACCOUNT:-}" '
        '"$(date +%s 2>/dev/null || echo 0)" '
        f'> "{d}/{relay_port}.json" ) 2>/dev/null || true; '
    )


# CodeSpace-side ADO auth helper (installed by
# ``codespace_assets.build_provision_command`` and symlinked onto PATH at
# ``~/.local/bin/ado-auth-helper``). Its ``get-access-token`` mints a raw ADO
# bearer over the relay for non-git feed clients (npm/nuget/rush) -- the same
# host identity the git relay uses. We use it to populate env-token feed-auth
# vars, which the git relay alone does not satisfy (dotfiles#1221).
ADO_AUTH_HELPER = "$HOME/.local/bin/ado-auth-helper"


def build_feed_token_exports(var_names: Any) -> str:
    """POSIX snippet exporting each feed-token env var from the ADO auth helper.

    The credential relay only wires **git** auth. A tool that authenticates to an
    Azure Artifacts feed via a static env token -- e.g. a Rush ``.npmrc`` line
    ``//<feed>/:_authToken=${EXAMPLE_NPM_AUTH_TOKEN}`` -- otherwise stays anonymous
    (E401), because nothing populates that var. For every name in *var_names*
    emit ``export <NAME>="$(<helper> get-access-token 2>/dev/null || true)";`` so
    it resolves to a fresh, relay-minted ADO bearer at launch, bridging the
    working relay to npm/nuget feed auth (dotfiles#1221).

    Best-effort: a missing helper yields an empty value (no worse than today).
    The helper needs ``LC_GIT_CREDENTIAL_RELAY`` (exported just above in the
    prelude), so callers MUST emit this AFTER the relay exports. Minted at
    launch as the initial value; :func:`build_feed_token_refresh_shim` re-mints
    it on each feed-client invocation so a reused session never sends an
    expired bearer.
    """
    out = ""
    for name in _valid_env_names(var_names):
        out += (
            f'export {name}="$({ADO_AUTH_HELPER} get-access-token '
            '2>/dev/null || true)"; '
        )
    return out


FEED_TOKEN_REFRESH_DIR = "$HOME/.cache/agent-codespaces/feed-token-path"
FEED_TOKEN_REFRESH_VARS_ENV = "AGENT_CODESPACES_FEED_TOKEN_VARS"
FEED_TOKEN_REFRESH_MINTED_ENV = "AGENT_CODESPACES_FEED_TOKEN_MINTED_AT"
FEED_TOKEN_REFRESH_INTERVAL_ENV = "AGENT_CODESPACES_FEED_TOKEN_REFRESH_INTERVAL"
FEED_TOKEN_REFRESH_TIMEOUT_ENV = "AGENT_CODESPACES_FEED_TOKEN_REFRESH_TIMEOUT"
FEED_TOKEN_REFRESH_INTERVAL_SECONDS = 300
FEED_TOKEN_REFRESH_TIMEOUT_SECONDS = 10
FEED_TOKEN_REFRESH_TOOLS = (
    "rush", "rushx", "pnpm", "npm", "npx", "yarn", "nuget", "dotnet",
)
# Wrapper bookkeeping uses these reserved names, so configured feed vars must
# not reuse them.
_FEED_TOKEN_RESERVED_PREFIXES = ("__acs_fte_", "AGENT_CODESPACES_FEED_TOKEN_")

# Wrapper installed under FEED_TOKEN_REFRESH_DIR for each feed client. When
# the last successful mint in this process tree is older than the refresh
# interval, it re-mints the bearer under a hard deadline (keeping the previous
# value on failure, or when no deadline can be enforced), then execs the next
# same-named binary on PATH, skipping every launch's wrapper directory.
_FEED_TOKEN_REFRESH_SCRIPT = (
    """#!/usr/bin/env bash
__acs_fte_self="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)"
__acs_fte_root="${__acs_fte_self%/*}"
__acs_fte_name="$(basename "$0")"
if [ -n "${@VARS@:-}" ]; then
  __acs_fte_now="$(date +%s 2>/dev/null)"
  __acs_fte_last="${@MINTED@:-0}"
  __acs_fte_every="${@INTERVAL@:-@INTERVAL_DEFAULT@}"
  __acs_fte_wait="${@TIMEOUT@:-@TIMEOUT_DEFAULT@}"
  case "$__acs_fte_now" in ''|*[!0-9]*) __acs_fte_now=0 ;; esac
  case "$__acs_fte_last" in ''|*[!0-9]*) __acs_fte_last=0 ;; esac
  case "$__acs_fte_every" in ''|*[!0-9]*) __acs_fte_every=@INTERVAL_DEFAULT@ ;; esac
  case "$__acs_fte_wait" in ''|*[!0-9]*) __acs_fte_wait=@TIMEOUT_DEFAULT@ ;; esac
  # A zero deadline would disable timeout, so treat it as the default.
  [ "$__acs_fte_wait" -gt 0 ] 2>/dev/null || __acs_fte_wait=@TIMEOUT_DEFAULT@
  if [ "$__acs_fte_now" -eq 0 ] \\
      || [ $(( __acs_fte_now - __acs_fte_last )) -ge "$__acs_fte_every" ] \\
      || [ "$__acs_fte_now" -lt "$__acs_fte_last" ]; then
    # Without timeout the deadline can't be enforced, so keep the previous value.
    __acs_fte_token=""
    if command -v timeout >/dev/null 2>&1; then
      # Only a successful mint replaces the previous value.
      __acs_fte_token="$(timeout -k 2 "$__acs_fte_wait" "@HELPER@" get-access-token 2>/dev/null)" \\
        || __acs_fte_token=""
    fi
    if [ -n "$__acs_fte_token" ]; then
      read -r -a __acs_fte_vars <<< "${@VARS@}"
      for __acs_fte_var in "${__acs_fte_vars[@]}"; do
        case "$__acs_fte_var" in
          ''|[0-9]*|*[!A-Za-z0-9_]*|__acs_fte_*|AGENT_CODESPACES_FEED_TOKEN_*) ;;
          *) export "$__acs_fte_var=$__acs_fte_token" ;;
        esac
      done
      export @MINTED@="$__acs_fte_now"
    fi
    unset __acs_fte_token
  fi
fi
__acs_fte_real=""
__acs_fte_rest="$PATH:"
while [ -n "$__acs_fte_rest" ]; do
  __acs_fte_dir="${__acs_fte_rest%%:*}"
  __acs_fte_rest="${__acs_fte_rest#*:}"
  [ -n "$__acs_fte_dir" ] || __acs_fte_dir=.
  __acs_fte_phys="$(cd "$__acs_fte_dir" 2>/dev/null && pwd -P)"
  # Skip every launch's wrapper directory (and the legacy shared one), so an
  # inherited earlier wrapper never dispatches back into this one.
  if [ -n "$__acs_fte_root" ]; then
    [ "$__acs_fte_phys" = "$__acs_fte_root" ] && continue
    [ "${__acs_fte_phys%/*}" = "$__acs_fte_root" ] && continue
  fi
  [ "$__acs_fte_phys" = "$__acs_fte_self" ] && continue
  if [ -x "$__acs_fte_dir/$__acs_fte_name" ] && [ ! -d "$__acs_fte_dir/$__acs_fte_name" ]; then
    __acs_fte_real="$__acs_fte_dir/$__acs_fte_name"
    break
  fi
done
if [ -z "$__acs_fte_real" ]; then
  echo "$__acs_fte_name: command not found" >&2
  exit 127
fi
exec "$__acs_fte_real" "$@"
"""
    .replace("@VARS@", FEED_TOKEN_REFRESH_VARS_ENV)
    .replace("@MINTED@", FEED_TOKEN_REFRESH_MINTED_ENV)
    .replace("@INTERVAL@", FEED_TOKEN_REFRESH_INTERVAL_ENV)
    .replace("@TIMEOUT@", FEED_TOKEN_REFRESH_TIMEOUT_ENV)
    .replace("@INTERVAL_DEFAULT@", str(FEED_TOKEN_REFRESH_INTERVAL_SECONDS))
    .replace("@TIMEOUT_DEFAULT@", str(FEED_TOKEN_REFRESH_TIMEOUT_SECONDS))
    .replace("@HELPER@", ADO_AUTH_HELPER)
)


def build_feed_token_refresh_shim(var_names: Any) -> str:
    """POSIX snippet that re-mints feed tokens on feed-client invocations.

    :func:`build_feed_token_exports` mints the bearer once, at launch. A
    reused or resumed session outlives that short-lived token, so a later
    ``rush install`` sends an expired bearer and the feed answers 401. This
    prepends a launch-scoped PATH directory with wrappers for the common feed
    clients that resolve at launch; each wrapper mints a fresh bearer through
    the relay-backed ``ado-auth-helper`` (under a short deadline, at most once
    per refresh interval in a process tree) and then execs the real binary.
    Tools absent at launch get no wrapper, so ``command -v`` still reports
    them missing. Each launch owns a unique subdirectory, so a later launch
    with a different PATH never removes an earlier live session's wrappers.
    Like the ``azure-auth-helper`` compat shim, PATH is exported only for this
    launch and never written to a dotfile.
    """
    names = [
        name for name in _valid_env_names(var_names)
        if not name.startswith(_FEED_TOKEN_RESERVED_PREFIXES)
    ]
    if not names:
        return ""
    d = "$__acs_fte_dir"
    script = shlex.quote(_FEED_TOKEN_REFRESH_SCRIPT)
    tools = " ".join(FEED_TOKEN_REFRESH_TOOLS)
    return (
        f"export {FEED_TOKEN_REFRESH_VARS_ENV}={shlex.quote(' '.join(names))}; "
        f'mkdir -p "{FEED_TOKEN_REFRESH_DIR}" 2>/dev/null && '
        f'__acs_fte_dir="$(mktemp -d "{FEED_TOKEN_REFRESH_DIR}/launch.XXXXXXXXXX" 2>/dev/null)" && '
        f'printf %s {script} > "{d}/.feed-token-exec.$$" 2>/dev/null && '
        f'chmod 755 "{d}/.feed-token-exec.$$" 2>/dev/null && '
        f'mv -f "{d}/.feed-token-exec.$$" "{d}/feed-token-exec" 2>/dev/null && '
        "{ "
        f"for __acs_fte_tool in {tools}; do "
        f'rm -f "{d}/$__acs_fte_tool"; '
        'if command -v "$__acs_fte_tool" >/dev/null 2>&1; then '
        f'ln -sf feed-token-exec "{d}/$__acs_fte_tool"; '
        "fi; "
        "done; "
        "unset __acs_fte_tool; "
        f'export PATH="{d}:$PATH"; '
        "} || true; "
        "unset __acs_fte_dir; "
    )


def build_identity_env_exports(var_names: Any) -> str:
    """POSIX snippet exporting each identity env var from the host Azure login.

    Unlike feed-token exports, these values are resolved on the HOST while the
    launch prelude is being built, so they do not depend on
    ``LC_GIT_CREDENTIAL_RELAY`` and can be emitted for any launch shape. The
    resolved value comes from the same host Azure CLI session the relay mints
    Azure bearers from; ordinary user principals export a short login-like
    alias, while other principal types keep the reported identity string.
    """
    names = _valid_env_names(var_names)
    if not names:
        return ""
    identity = current_identity()
    if not identity:
        return ""
    out = ""
    quoted = shlex.quote(identity)
    for name in names:
        out += f"export {name}={quoted}; "
    return out


def build_relay_portmap_publish(
    relay_port: int,
    relay_token: str | None,
    *,
    ado_host: str | None = None,
    github_account: str | None = None,
) -> str:
    """Standalone command that publishes only the relay port-mapping file.

    For a connection whose remote shell never runs the launch prelude (an
    interactive ``gh codespace ssh`` after skipped warm-up/provisioning): ssh
    does not carry the local ``LC_*`` relay env across, so the auth helpers
    must discover the token and GitHub account from this file instead.
    """
    env = f"export LC_GIT_CREDENTIAL_RELAY_TOKEN={shlex.quote(relay_token or '')}; "
    if ado_host:
        env += f"export LC_GIT_CREDENTIAL_RELAY_ADO_HOST={shlex.quote(ado_host)}; "
    if github_account:
        env += f"export LC_GIT_CREDENTIAL_RELAY_GITHUB_ACCOUNT={shlex.quote(github_account)}; "
    return env + build_relay_portmap_write(relay_port)


def build_relay_env(
    relay_port: int,
    relay_token: str | None,
    *,
    use_relay: bool,
    ado_host: str | None = None,
    github_account: str | None = None,
    feed_token_env: list[str] | None = None,
    identity_env: list[str] | None = None,
) -> str:
    """Build the CodeSpace launch-prelude env string.

    ALWAYS prepends the PAT scrub (so it can never be clobbered by the relay
    exports), then deploys/exports the agent-codespaces custom-instructions root,
    and appends the relay exports when ``use_relay``. ``GIT_TERMINAL_PROMPT=0``
    keeps git from blocking on an interactive prompt when a credential can't be
    resolved; ``GCM_INTERACTIVE=never`` makes Git Credential Manager fail fast
    rather than starting an interactive broker in a headless ACP session. Any
    ``identity_env`` vars are resolved on the host at prelude-build time and
    exported regardless of ``use_relay`` because they do not depend on
    ``LC_GIT_CREDENTIAL_RELAY``. When ``use_relay``, also publishes a
    port-mapping file so the auth helpers can rediscover this relay channel by
    liveness probe even if the env is not inherited by a later tool shell (see
    :func:`build_relay_portmap_write`), exposes the bare
    ``azure-auth-helper`` name RushStack's ``AdoCodespacesAuthCredential``
    requires -- session/process-scoped only, never a persisted shadow (see
    :func:`build_azure_auth_helper_compat_shim`, #415) -- and -- for any
    ``feed_token_env`` var names -- exports a feed-auth token minted from the
    ADO auth helper so env-token feed auth (npm/nuget/rush) works over the
    relay (dotfiles#1221). The feed-token exports come LAST so they can use the
    just-exported ``LC_GIT_CREDENTIAL_RELAY``; the refresh shim that follows
    re-mints them per feed-client invocation (:func:`build_feed_token_refresh_shim`).
    """
    from .codespace_assets import build_auth_error_policy_command

    env = "".join(f"unset {v}; " for v in SCRUB_ENV_VARS)
    env += build_auth_error_policy_command()
    env += build_identity_env_exports(identity_env)
    if use_relay:
        env += (
            f"export LC_GIT_CREDENTIAL_RELAY={relay_port}; "
            f"export LC_GIT_CREDENTIAL_RELAY_TOKEN={relay_token}; "
            "export GIT_TERMINAL_PROMPT=0; "
            "export GCM_INTERACTIVE=never; "
        )
        if ado_host:
            env += (
                "export LC_GIT_CREDENTIAL_RELAY_ADO_HOST="
                f"{shlex.quote(ado_host)}; "
            )
        if github_account:
            env += (
                "export LC_GIT_CREDENTIAL_RELAY_GITHUB_ACCOUNT="
                f"{shlex.quote(github_account)}; "
            )
        env += build_relay_portmap_write(relay_port)
        env += build_azure_auth_helper_compat_shim()
        env += build_feed_token_exports(feed_token_env)
        env += build_feed_token_refresh_shim(feed_token_env)
    return env


def relay_azure_resources(cfg: Any) -> list[str]:
    """The Azure resources a codespace's relay token may mint tokens for.

    The default ADO REST + Storage resources, plus any the adopting repo's
    ``az-login`` source config adds. No product-specific values here -- they
    come from the merged config a harness plugin supplies by convention.
    """
    from .relay_provider import DEFAULT_AZURE_RESOURCES

    resources = list(DEFAULT_AZURE_RESOURCES)
    az_cfg = getattr(getattr(cfg, "credentials", None), "sources", {}).get("az-login")
    if az_cfg and az_cfg.enabled:
        resources.extend(az_cfg.allowed_resources)
    return resources


def scoped_relay_token(codespace_name: str, cfg: Any) -> str:
    """Mint/reuse the codespace's relay token with its configured Azure scope.

    Every connect path must mint through this: a token minted without a scope
    records ``allowed_resources: []``, which the relay authorizer reads as
    "no Azure resources" -- the CodeSpace's ``azure-auth-helper`` then fails,
    and RushStack's cloud-cache login falls through to an interactive browser
    flow that waits forever in an unattended session.
    """
    from .relay_token import token_for

    return token_for(codespace_name, allowed_resources=relay_azure_resources(cfg))


def build_relay_launch_env(
    codespace_name: str, relay_port: int | None = None
) -> tuple[str, int]:
    """Return ``(prelude_env, relay_port)`` for a detached CodeSpace launch.

    Mints/reuses the per-codespace relay token and resolves the relay port, so a
    Session Host launched detached on the CS inherits working ADO/git auth over
    the relay (the ``-R`` reverse-forward that carries it is stood up by the
    caller's persistent forward). Raises if config is unavailable.

    ``relay_port`` lets an in-daemon caller (agent-bridge) inject the relay's
    *actually-bound* port from ``relay_state.get_live_relay_port`` so the CS env
    + ``-R`` follow the live relay rather than the statically declared config
    port (dotfiles #489/#540 pt3). When ``None`` (e.g. the standalone
    ``agent-codespaces`` path, which cannot see the daemon's process-local live
    port) it first tries the port the daemon **publishes** to its config dir
    (``relay_state``'s cross-daemon rendezvous, discovered here without importing
    ``agent_bridge``), so an ephemeral/dynamic bind is honored on this path too;
    only if that is absent does it fall back to the configured
    ``credentials.relay_port``.
    """
    from .config import load_merged_config

    cfg = load_merged_config(include_cwd=False)
    try:
        from .gh_account import fast_credential_account_for_codespace

        github_account = fast_credential_account_for_codespace(codespace_name)
    except Exception:
        github_account = None
    if relay_port is not None:
        port = int(relay_port)
    else:
        published = _published_live_relay_port()
        if published is not None:
            port = published
        else:
            port = int(cfg.credentials.relay_port)
    # Record the per-token Azure scope the relay authorizer enforces (see
    # :func:`relay_azure_resources`).
    token = scoped_relay_token(codespace_name, cfg)
    warn_if_relay_unavailable(
        port, codespace_name, context="Session Host dispatch",
    )
    return (
        build_relay_env(
            port,
            token,
            use_relay=True,
            ado_host=getattr(cfg.credentials, "ado_host", None),
            github_account=github_account,
            feed_token_env=getattr(cfg.credentials, "feed_token_env", None),
            identity_env=getattr(cfg.credentials, "identity_env", None),
        ),
        port,
    )


def _published_live_relay_port() -> int | None:
    """Best-effort read of the live relay port the agent-bridge daemon publishes.

    Mirrors agent-bridge ``relay_state``'s cross-daemon publish
    (``<primary-config-dir>/relay-port``) **without importing ``agent_bridge``** --
    the standalone agent-codespaces venv does not contain it. Honors
    ``AGENT_BRIDGE_CONFIG_DIR`` (default ``~/.agent-bridge``) and resolves an
    ``/elevated`` sub-daemon dir to its primary parent, so an ephemeral/dynamic
    relay port is discovered even on the standalone path (#540 pt3). Returns
    ``None`` when the file is absent/unparseable.
    """
    _default_dir = "~/.agent-bridge"  # marketplace-isolation: allow registry
    base = Path(
        os.environ.get("AGENT_BRIDGE_CONFIG_DIR", _default_dir)
    ).expanduser()
    if base.name == "elevated":
        base = base.parent
    try:
        txt = (base / "relay-port").read_text(encoding="utf-8").strip()
        return int(txt) if txt else None
    except (OSError, ValueError):
        return None


# Last-resort relay port when neither a live (published) nor a configured port
# is available -- the retired fixed default, kept only as a backstop (#694).
LEGACY_RELAY_PORT = 9857


def effective_relay_port(config: Any) -> int:
    """Resolve the relay port for a directly-constructed SSH ``-R`` forward + env.

    The relay now binds a **dynamic** port by default (``credentials.relay_port``
    defaults to 0 -> the daemon binds an OS-assigned ephemeral port). The paths
    that build the ``-R`` reverse-forward straight from config -- the interactive
    ``ssh`` connect and ``provision`` -- must therefore follow the daemon's
    **live** port, not the (now 0) static config, exactly like
    :func:`build_relay_launch_env` does for the detached path. Resolution:

    1. the port the agent-bridge daemon **publishes** (``relay_state`` rendezvous,
       read without importing ``agent_bridge``) -- honors an ephemeral/dynamic bind;
    2. else a **positive** configured ``credentials.relay_port`` (an explicit pin);
    3. else :data:`LEGACY_RELAY_PORT` (9857) as a last-resort backstop.
    """
    published = _published_live_relay_port()
    if published:
        return published
    try:
        configured = int(getattr(config.credentials, "relay_port", 0) or 0)
    except (TypeError, ValueError):
        configured = 0
    return configured or LEGACY_RELAY_PORT
