"""Shared venue-side CLI-mode ``copilot`` launch orchestration (vendored).

Provider-agnostic core for ``agent-codespaces copilot <name>`` / ``agent-
containers copilot <name>`` (agent-bridge-cli-mode-sessions Phase 4): reserve a
worktree's CLI-mode Session Host slot via the host ``agent-bridge`` daemon,
build the remote ``agent-worktrees copilot`` command that ensures/attaches the
muxed session *inside* the venue, hand off to a provider-supplied ``connect``
callback that actually opens the interactive channel (each provider's
transport differs -- OpenSSH via ``ssh-manager`` for CodeSpaces, OpenSSH into a
trusted container, or a restricted-fleet ``docker exec`` for containers), and
always release the reservation afterward.

Only ``connect`` is provider-specific; reserve/build-command/release is
identical across venues, hence this shared lib (vendored the same way as
``ssh-manager``/``credential-relay``: byte-identical ``src/`` per
``tools/check-vendored-libs-sync.py``).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

DEFAULT_TTL_SECONDS = 300.0

# The daemon's own config dir, matching agent-bridge's ``effective_config_dir()``
# default -- overridable the same way, via ``AGENT_BRIDGE_CONFIG_DIR``.
_DEFAULT_BRIDGE_CONFIG_DIR = "~/.agent-bridge"

#: Env vars scrubbed before spawning the ``agent-bridge`` CLI as a plain
#: sibling binstub -- mirrors ``agent_dispatch.procutil``'s
#: ``_AGENT_WORKTREES_ENV_SCRUB`` precedent (itself citing agent-bridge's
#: ``worktree_head.py``): a caller running inside its own uv-managed venv
#: (e.g. this process itself, or a test harness invoking it) leaks
#: ``PYTHONHOME``/``VIRTUAL_ENV``/``__PYVENV_LAUNCHER__``/``PYTHONPATH`` into
#: the child by default (``subprocess`` inherits ``os.environ`` verbatim when
#: no ``env=`` is given), which then forces the sibling binstub's OWN
#: re-exec'd interpreter to resolve the WRONG stdlib/native-extension
#: location -- confirmed live (agent-bridge-cli-mode-sessions Phase 4
#: validation): an inherited ``PYTHONHOME`` pointed at a different
#: interpreter's tree, and the spawned ``agent-bridge`` died importing
#: ``socket`` with ``ImportError: DLL load failed ... not a valid Win32
#: application`` -- the same ``_sre``-mismatch bug class this scrub already
#: guards against elsewhere, just manifesting in a different stdlib module.
_ENV_SCRUB = frozenset({
    "PYTHONHOME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "__PYVENV_LAUNCHER__",
})


class VenueCopilotError(RuntimeError):
    """Raised when reservation/release plumbing fails.

    Raised only from :func:`reserve_cli_mode` -- a failure there means
    ``connect`` is never entered, so no interactive session is left dangling.
    :func:`release_cli_mode` never raises: a failed release must not mask the
    real outcome of an interactive session that already ran.
    """


def _run_bridge(
    argv: list[str], *, run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    # A bare binstub name (e.g. "agent-bridge") is only resolved by a real
    # shell's PATHEXT search; a direct (list-argv, ``shell=False``) subprocess
    # spawn on Windows does not try ``.cmd``/``.ps1`` and fails with
    # ``FileNotFoundError: [WinError 2]`` -- confirmed live against a real
    # CodeSpace (agent-bridge-cli-mode-sessions Phase 4 validation). Resolve
    # via PATH first so the same argv works cross-platform; fall back to the
    # bare name (e.g. a caller-supplied absolute path, or so a genuinely
    # missing binstub still raises the caller's own natural error).
    resolved = shutil.which(argv[0]) or argv[0]
    argv = [resolved, *argv[1:]]
    env = {k: v for k, v in os.environ.items() if k not in _ENV_SCRUB}
    result = run(argv, capture_output=True, text=True, env=env)
    stdout = getattr(result, "stdout", None) or ""
    returncode = getattr(result, "returncode", 0)
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
    if returncode != 0 and "error" not in parsed:
        stderr = (getattr(result, "stderr", "") or "").strip()
        raise VenueCopilotError(
            f"`{' '.join(argv[:5])}` failed (exit {returncode}): "
            f"{stderr or 'no error output'}"
        )
    return parsed


def reserve_cli_mode(
    worktree_id: str,
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Reserve ``worktree_id``'s next CLI-mode Session Host slot on the host
    daemon (``agent-bridge --json live-sessions cli-mode reserve``).

    Raises :class:`VenueCopilotError` (never a provider-specific exception) on
    any failure -- including an already-active reservation (HTTP 409) -- so a
    caller can uniformly refuse to open an interactive channel that would only
    fail to register once inside the venue.
    """
    argv = [
        bridge_bin, "--json", "live-sessions", "cli-mode", "reserve",
        "--worktree-id", worktree_id, "--ttl-seconds", str(ttl_seconds),
    ]
    reservation = _run_bridge(argv, run=run)
    if reservation.get("error"):
        raise VenueCopilotError(
            f"could not reserve a CLI-mode session for {worktree_id!r}: "
            f"{reservation['error']}"
        )
    return reservation


def release_cli_mode(
    worktree_id: str,
    *,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> int:
    """Best-effort release of ``worktree_id``'s CLI-mode reservation.

    Never raises: called from a ``finally`` after the interactive session
    already ran, so a release failure must only be swallowed (the reservation
    still self-expires via its TTL), never surfaced as this command's outcome.
    """
    try:
        argv = [
            bridge_bin, "--json", "live-sessions", "cli-mode", "release",
            "--worktree-id", worktree_id,
        ]
        result = _run_bridge(argv, run=run)
        return int(result.get("removed", 0) or 0)
    except VenueCopilotError:
        return 0


def build_copilot_remote_command(
    worktree_id: str,
    *,
    driver: str | None = None,
    seed: str | None = None,
    ensure_mux: bool = True,
    embody_bin: str = "agent-worktrees",
) -> str:
    """The remote shell command a venue runs to deliver a TTY Copilot session.

    Mirrors ``agent-worktrees copilot``'s own CLI contract (PR #3126) exactly
    -- reserve/connect/release wraps that same local verb dispatched remotely,
    rather than reimplementing attach logic a third time. The venue must
    already carry a *full* ``agent-worktrees`` install (not just its lean
    self-provisioned tools) for this to resolve.

    Wrapped in ``bash -lc`` (a login shell): confirmed live against a real
    disposable trusted-container venue (agent-bridge-cli-mode-sessions Phase
    4 validation) that OpenSSH's non-interactive remote-command exec never
    sources ``~/.profile``/``~/.bashrc`` -- exactly where
    ``agent-worktrees``'s own install flow appends ``~/.local/bin`` to PATH
    (its own getting-started doc's "``~/.local/bin`` is on PATH" check is a
    login-shell-only guarantee). Without this, a fully, correctly installed
    remote ``agent-worktrees`` binstub still resolves to
    ``agent-worktrees: command not found`` (exit 127) -- a distinct bug from,
    and layered underneath, the already-tracked "venue lacks a full install"
    gap.
    """
    argv = [embody_bin, "copilot", "--worktree-id", worktree_id]
    if driver:
        argv += ["--driver", driver]
    if seed:
        argv += ["--seed", seed]
    if ensure_mux:
        argv.append("--ensure-mux")
    inner = " ".join(shlex.quote(part) for part in argv)
    return f"bash -lc {shlex.quote(inner)}"


def run_venue_copilot(
    worktree_id: str,
    *,
    connect: Callable[[str], int],
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    driver: str | None = "cli-mode",
    seed: str | None = None,
    ensure_mux: bool = True,
    bridge_bin: str = "agent-bridge",
    embody_bin: str = "agent-worktrees",
    run: Callable[..., Any] = subprocess.run,
) -> int:
    """Reserve -> ``connect`` (provider-specific interactive channel) -> release.

    ``connect`` receives the fully-built remote command string and returns the
    interactive session's exit code (or raises); it owns the actual transport
    (the OpenSSH/docker invocation, any provider-specific tenancy/heartbeat
    kept alive while attached, and layering the daemon-port reverse forward
    onto its own transport). The reservation is released in a ``finally``
    regardless of how ``connect`` returns, so a crashed or killed interactive
    session never leaks a reservation past its own TTL only.
    """
    reserve_cli_mode(
        worktree_id, ttl_seconds=ttl_seconds, bridge_bin=bridge_bin, run=run,
    )
    remote_command = build_copilot_remote_command(
        worktree_id, driver=driver, seed=seed, ensure_mux=ensure_mux,
        embody_bin=embody_bin,
    )
    try:
        return connect(remote_command)
    finally:
        release_cli_mode(worktree_id, bridge_bin=bridge_bin, run=run)


def resolve_daemon_port(config_dir: str | None = None) -> int | None:
    """The host ``agent-bridge`` daemon's own live API port, or ``None``.

    Reads the routing table (``<config_dir>/active.json``) the daemon already
    publishes for its own dynamic-port discovery -- the same mechanism
    ``agent_bridge.__main__._service_port()`` uses in-process -- via the
    vendored ``zdd.routing`` reader, so a provider plugin's standalone venv
    (which does not contain ``agent_bridge``) can resolve it too. Mirrors
    ``relay_launch._published_live_relay_port``'s no-import constraint for the
    *relay* port; this is the daemon's own API port, needed for the venue
    `copilot` verb's daemon-port reverse forward (so a remote CLI-mode session
    can register back to the host daemon at all -- see the effort's Phase 4
    grounding). ``verify_listener=False`` so a mid-startup port is still
    reported; a caller that needs liveness should probe the forward itself.
    Returns ``None`` on any missing/unparseable table (degrade-safe: the
    caller then skips the daemon-port forward rather than failing outright).
    """
    from zdd.routing import read_active_endpoint

    base = os.path.expanduser(config_dir or os.environ.get(
        "AGENT_BRIDGE_CONFIG_DIR", _DEFAULT_BRIDGE_CONFIG_DIR,
    ))
    try:
        endpoint = read_active_endpoint(base, verify_listener=False)
    except Exception:
        return None
    return int(endpoint.port) if endpoint is not None and endpoint.port else None


def daemon_port_reverse_forward(port: int) -> str:
    """The ``-R`` spec string carrying the daemon's own port into the venue.

    Same loopback-to-loopback shape the credential-relay forward already
    uses (``reverse_forwards`` on ``CodeSpaceTransport``/``ContainerTransport``)
    -- additive alongside it, not a replacement.
    """
    return f"{port}:127.0.0.1:{port}"
