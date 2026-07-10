"""Relay launch prelude for the detached Session Host path.

agent-bridge's ``CodeSpaceSpawner`` launches copilot **detached** on the
CodeSpace (``setsid nohup``), not via ``agent-codespaces ssh``, so it must
reproduce the relay env prelude the ssh path injects: neutralize injected static
PATs (#160/#77) so a dispatched agent never relies on a stale token instead of
the credential relay, export ``LC_GIT_CREDENTIAL_RELAY`` + the per-codespace
token, and disable interactive git prompts. This is the **public seam**
agent-bridge calls (guarded import) so the ssh path and the Session-Host path
stay in lockstep.
"""

from __future__ import annotations

# Static PATs a CodeSpace injects that must be neutralized so a dispatched agent
# never relies on a stale/expired token instead of the credential relay.
SCRUB_ENV_VARS: tuple[str, ...] = ("MS_ADO_PAT",)


def build_relay_env(
    relay_port: int, relay_token: str | None, *, use_relay: bool
) -> str:
    """Build the CodeSpace launch-prelude env string.

    ALWAYS prepends the PAT scrub (so it can never be clobbered by the relay
    exports); appends the relay exports when ``use_relay``. ``GIT_TERMINAL_PROMPT=0``
    keeps git from blocking on an interactive prompt when a credential can't be
    resolved.
    """
    env = "".join(f"unset {v}; " for v in SCRUB_ENV_VARS)
    if use_relay:
        env += (
            f"export LC_GIT_CREDENTIAL_RELAY={relay_port}; "
            f"export LC_GIT_CREDENTIAL_RELAY_TOKEN={relay_token}; "
            "export GIT_TERMINAL_PROMPT=0; "
        )
    return env


def build_relay_launch_env(codespace_name: str) -> tuple[str, int]:
    """Return ``(prelude_env, relay_port)`` for a detached CodeSpace launch.

    Mints/reuses the per-codespace relay token and reads the configured relay
    port, so a Session Host launched detached on the CS inherits working ADO/git
    auth over the relay (the ``-R`` reverse-forward that carries it is stood up by
    the caller's persistent forward). Raises if config is unavailable.
    """
    from .config import load_merged_config
    from .relay_token import token_for

    cfg = load_merged_config()
    port = int(cfg.credentials.relay_port)
    token = token_for(codespace_name)
    return build_relay_env(port, token, use_relay=True), port
