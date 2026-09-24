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
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

DEFAULT_TTL_SECONDS = 300.0

# The seed travels inside the remote command line; a Windows host caps a
# process command line at ~32K characters, so refuse clearly well below that.
MAX_SEED_CHARS = 24_000

# `embody` types its seed into the TUI with `tmux send-keys`, which is only safe
# for one line (a newline would submit a partial prompt). A multi-line or long
# task is therefore written to a file on the venue and seeded as a one-line
# pointer; the file also stays re-readable by the session after a resume.
SEED_INLINE_MAX = 400
SEED_DIR = "$HOME/.agent-bridge/seeds"

SESSION_SELECTORS = ("--resume", "-r", "--continue", "--session-id")

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

_TRUST_FOLDER = (
    "python3 -c 'import json,os,sys\n"
    "p=os.path.expanduser(\"~/.copilot/config.json\")\n"
    "head,body=[],[]\n"
    "if os.path.exists(p):\n"
    "    for line in open(p).read().splitlines():\n"
    "        (head if not body and line.lstrip().startswith(\"//\") else body).append(line)\n"
    "try: d=json.loads(chr(10).join(body)) if body else {}\n"
    "except Exception: sys.exit(0)\n"
    "t=d.setdefault(\"trustedFolders\",[])\n"
    "t.append(sys.argv[1]) if sys.argv[1] not in t else None\n"
    "os.makedirs(os.path.dirname(p),exist_ok=True)\n"
    "open(p,\"w\").write(chr(10).join(head+[json.dumps(d,indent=2)])+chr(10))' "
)


class VenueCopilotError(RuntimeError):
    """Raised when reservation/release plumbing fails.

    Raised only from :func:`reserve_cli_mode` -- a failure there means
    ``connect`` is never entered, so no interactive session is left dangling.
    :func:`release_cli_mode` never raises: a failed release must not mask the
    real outcome of an interactive session that already ran.
    """


def read_seed(args: Any) -> str | None:
    """The seed from ``--seed`` or ``--seed-file`` (``-`` = stdin)."""
    path = getattr(args, "seed_file", None)
    if path and getattr(args, "seed", None):
        raise ValueError("--seed and --seed-file are mutually exclusive")
    if not path:
        seed = getattr(args, "seed", None)
    elif path == "-":
        seed = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as fh:
            seed = fh.read()
    if seed and len(seed) > MAX_SEED_CHARS:
        raise ValueError(
            f"seed is {len(seed)} characters (max {MAX_SEED_CHARS}); put the "
            "details in a file the session can read and seed a short pointer"
        )
    return seed or None


def with_new_session(copilot_args: list[str]) -> list[str]:
    """Start a *new* Copilot session unless the caller chose one to resume."""
    if any(a.split("=", 1)[0] in SESSION_SELECTORS for a in copilot_args):
        return list(copilot_args)
    return [*copilot_args, f"--session-id={uuid.uuid4()}"]


def seed_delivery(seed: str | None, scope: str) -> tuple[str | None, str]:
    """``(seed_to_type, shell_prefix)`` -- the prefix stages a file when needed."""
    if not seed or ("\n" not in seed.strip() and len(seed) <= SEED_INLINE_MAX):
        return (seed.strip() if seed else None), ""
    safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in scope)
    path = f"{SEED_DIR}/{safe}-{int(time.time())}.md"
    prefix = f'mkdir -p "{SEED_DIR}" && printf %s {shlex.quote(seed)} > "{path}" && '
    pointer = (
        f"Read the task file {path.replace('$HOME', '~')} now and carry it out "
        "exactly as written; re-read it whenever you need the instructions again."
    )
    return pointer, prefix


def trust_folder_command(folder: str) -> str:
    """Shell snippet adding ``folder`` to the venue's Copilot ``trustedFolders``."""
    return _TRUST_FOLDER + shlex.quote(folder)


def registration_credentials_script(token: str, port: int) -> str:
    """Shell snippet provisioning agent-bridge registration credentials."""
    return (
        "mkdir -p ~/.agent-bridge && "
        f"printf 'token: %s\\n' {shlex.quote(token)} "
        "> ~/.agent-bridge/auth.yaml && "
        f"printf '{{\"active\": {{\"port\": {int(port)}}}}}' "
        "> ~/.agent-bridge/active.json"
    )


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
    venue: dict[str, Any] | None = None,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Reserve ``worktree_id``'s next CLI-mode Session Host slot on the host
    daemon (``agent-bridge --json live-sessions cli-mode reserve``).

    ``venue`` (``{"kind", "target", "mux_session_name"}``) is recorded on the
    reservation and inherited by the claiming live session, so the host bridge
    knows where a remote session lives without trusting the registering client.

    Raises :class:`VenueCopilotError` (never a provider-specific exception) on
    any failure -- including an already-active reservation (HTTP 409) -- so a
    caller can uniformly refuse to open an interactive channel that would only
    fail to register once inside the venue.
    """
    argv = [
        bridge_bin, "--json", "live-sessions", "cli-mode", "reserve",
        "--worktree-id", worktree_id, "--ttl-seconds", str(ttl_seconds),
    ]
    if venue:
        argv += ["--venue-json", json.dumps(venue, separators=(",", ":"))]
    reservation = _run_bridge(argv, run=run)
    if reservation.get("error"):
        raise VenueCopilotError(
            f"could not reserve a CLI-mode session for {worktree_id!r}: "
            f"{reservation['error']}"
        )
    return reservation


def get_cli_mode_reservation(
    worktree_id: str,
    *,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """The worktree's current CLI-mode reservation (``{}`` when none).

    ``claimed_by_session_id`` names the exact live session that registered
    against it -- how a detached launcher learns its session's identity.
    """
    argv = [
        bridge_bin, "--json", "live-sessions", "cli-mode", "status",
        "--worktree-id", worktree_id,
    ]
    return _run_bridge(argv, run=run)


def await_claim(
    worktree_id: str,
    reservation_id: str,
    timeout: float,
    *,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> str | None:
    """Wait until a CLI-mode reservation is claimed by a live session."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            try:
                row = get_cli_mode_reservation(worktree_id, bridge_bin=bridge_bin, run=run)
            except TypeError:
                # Older tests monkeypatch ``get_cli_mode_reservation`` with a
                # one-argument seam; keep the shared helper drop-in compatible.
                row = get_cli_mode_reservation(worktree_id)
        except VenueCopilotError:
            row = {}
        if row.get("reservation_id") == reservation_id and row.get("claimed_by_session_id"):
            return str(row["claimed_by_session_id"])
        if time.monotonic() >= deadline:
            return None
        time.sleep(3.0)


def release_cli_mode(
    worktree_id: str,
    *,
    reservation_id: str | None = None,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> int:
    """Best-effort release of ``worktree_id``'s CLI-mode reservation.

    With ``reservation_id``, only that exact reservation is removed
    (compare-and-delete), never a newer one created since.

    Never raises: called from a ``finally`` after the interactive session
    already ran, so a release failure must only be swallowed (the reservation
    still self-expires via its TTL), never surfaced as this command's outcome.
    """
    try:
        argv = [
            bridge_bin, "--json", "live-sessions", "cli-mode", "release",
            "--worktree-id", worktree_id,
        ]
        if reservation_id:
            argv += ["--reservation-id", reservation_id]
        result = _run_bridge(argv, run=run)
        return int(result.get("removed", 0) or 0)
    except VenueCopilotError:
        return 0


def live_session_for(
    handle: str,
    *,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """The live session a session id or worktree handle resolves to (``{}`` when none). Never raises."""
    try:
        return _run_bridge(
            [bridge_bin, "--json", "live-sessions", "resolve", "--handle", handle], run=run,
        )
    except (VenueCopilotError, OSError):
        return {}


def deregister_live_session(
    session_id: str,
    *,
    bridge_bin: str = "agent-bridge",
    run: Callable[..., Any] = subprocess.run,
) -> bool:
    """Best-effort removal of one exact live session whose process is gone.

    For a launcher that has just verified its session's process stopped: an
    abruptly killed CLI never deregisters itself, and the bridge would
    otherwise keep advertising it until the stale-heartbeat reaper runs.
    Never raises.
    """
    try:
        _run_bridge(
            [bridge_bin, "--json", "live-sessions", "deregister", "--session-id", session_id],
            run=run,
        )
        return True
    except (VenueCopilotError, OSError):
        return False


def build_copilot_remote_command(
    worktree_id: str,
    *,
    anchor: bool = False,
    driver: str | None = None,
    seed: str | None = None,
    ensure_mux: bool = True,
    embody_bin: str = "agent-worktrees",
    detach: bool = False,
    bridge_scope_id: str | None = None,
    copilot_args: list[str] | None = None,
    login_shell: bool = True,
) -> str:
    """The remote shell command a venue runs to deliver a TTY Copilot session.

    Mirrors ``agent-worktrees copilot``'s own CLI contract (PR #3126) exactly
    -- reserve/connect/release wraps that same local verb dispatched remotely,
    rather than reimplementing attach logic a third time. The venue must
    already carry a *full* ``agent-worktrees`` install (not just its lean
    self-provisioned tools) for this to resolve.

    ``detach=True`` runs ``agent-worktrees embody`` instead -- the same
    create-or-resume of the muxed session, but it returns a JSON result
    instead of attaching, and never re-seeds a session that already exists.
    ``bridge_scope_id`` / ``copilot_args`` forward ``--bridge-scope-id`` /
    ``--copilot-arg`` (the registration identity and extra Copilot flags such
    as ``--plugin-dir=...``). ``login_shell=False`` returns the bare command
    for a caller that already wraps it in its own login shell.

    ``anchor=True`` forwards ``--anchor`` instead of ``--worktree-id
    <worktree_id>`` -- a CodeSpace/container venue is conventionally
    anchor-only (the devcontainer/CodeSpace already clones the repo directly;
    there is no worktree unless an operator explicitly created one), matching
    headless ACP dispatch's own existing behavior of running straight in the
    anchor checkout. ``worktree_id`` is still required in this case (as the
    CLI-mode reservation identity -- see :func:`reserve_cli_mode`), but is
    never forwarded to the remote command itself.

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
    argv = [embody_bin, "embody" if detach else "copilot"]
    argv += ["--anchor"] if anchor else ["--worktree-id", worktree_id]
    if driver:
        argv += ["--driver", driver]
    if seed:
        argv += ["--seed", seed]
    if ensure_mux:
        argv.append("--ensure-mux")
    if bridge_scope_id:
        argv += ["--bridge-scope-id", bridge_scope_id]
    home_expandable: set[int] = set()
    for extra in copilot_args or []:
        if "$HOME" in extra:
            home_expandable.add(len(argv))
        argv.append(f"--copilot-arg={extra}")
    if detach:
        argv.append("--json")
    inner = " ".join(
        _quote_expanding_home(part) if i in home_expandable else shlex.quote(part)
        for i, part in enumerate(argv)
    )
    return f"bash -lc {shlex.quote(inner)}" if login_shell else inner


def _quote_expanding_home(part: str) -> str:
    """Shell-quote ``part`` but leave each literal ``$HOME`` expandable.

    Staged ``--plugin-dir`` paths are ``$HOME/...`` (the remote home is not
    known host-side). ``embody`` hands ``--copilot-arg`` values to the mux
    without a shell and Copilot never expands ``$HOME`` itself, so a fully
    single-quoted arg reaches Copilot as a literal (cwd-relative) path and the
    plugin silently fails to load. Only the remote shell can expand it.
    """
    return '"$HOME"'.join(shlex.quote(p) for p in part.split("$HOME"))


def run_venue_copilot(
    worktree_id: str,
    *,
    connect: Callable[[str], int],
    anchor: bool = False,
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

    ``worktree_id`` is always the CLI-mode reservation's identity (the string
    key agent-bridge's daemon correlates a self-registering session against);
    for ``anchor=True`` the caller passes the same synthesized
    ``anchor-<repo_name>`` identity ``agent-worktrees get session-scope-id``
    reports remotely once the anchor session registers, so the reservation
    can actually be claimed (see the effort's Phase 4 registration-identity
    follow-up).
    """
    reserve_cli_mode(
        worktree_id, ttl_seconds=ttl_seconds, bridge_bin=bridge_bin, run=run,
    )
    remote_command = build_copilot_remote_command(
        worktree_id, anchor=anchor, driver=driver, seed=seed,
        ensure_mux=ensure_mux, embody_bin=embody_bin,
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


def resolve_local_auth_token(config_dir: str | None = None) -> str | None:
    """The host ``agent-bridge`` daemon's own bearer token, or ``None``.

    Reads ``<config_dir>/auth.yaml``'s ``token:`` key -- the exact file
    ``agent_bridge.config.load_or_create_auth_token()`` writes, and the exact
    file the interactive CLI extension's own ``resolveToken()``
    (``extensions/agent-bridge/extension.mjs``) reads on whatever machine it
    runs on. A remote CLI-mode venue never has this file: nothing has ever
    provisioned it there. Without a token, that extension logs "no local
    agent-bridge auth token found; not registering (ok)" and silently never
    calls the registration endpoint at all -- confirmed live
    (agent-bridge-cli-mode-sessions Phase 4 follow-up): a real CodeSpace
    session loaded the extension, reached a ready prompt, and still never
    registered, because this file was never copied there. A caller (the venue
    `copilot` verb) is expected to provision this token (plus the matching
    ``active.json`` from :func:`resolve_daemon_port`) onto the remote venue
    before connecting -- see ``agent_codespaces.copilot_venue``'s
    ``_provision_registration_credentials``.

    Uses a plain regex, not a YAML parser, matching the JS extension's own
    parse and avoiding a PyYAML dependency in this vendored, no-heavy-deps
    lib. Returns ``None`` on any missing/unparseable file.
    """
    import re

    base = os.path.expanduser(config_dir or os.environ.get(
        "AGENT_BRIDGE_CONFIG_DIR", _DEFAULT_BRIDGE_CONFIG_DIR,
    ))
    try:
        with open(os.path.join(base, "auth.yaml"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    match = re.search(r"^\s*token:\s*(\S+)", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip("'\"")


def daemon_port_reverse_forward(port: int) -> str:
    """The ``-R`` spec string carrying the daemon's own port into the venue.

    Same loopback-to-loopback shape the credential-relay forward already
    uses (``reverse_forwards`` on ``CodeSpaceTransport``/``ContainerTransport``)
    -- additive alongside it, not a replacement.
    """
    return f"{port}:127.0.0.1:{port}"
