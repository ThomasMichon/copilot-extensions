"""Recover a CodeSpace's Copilot session-state into the agent-logger hub.

Pulls ``~/.copilot`` session data (the ``session-state/`` tree plus the
``session-store.db*`` index files -- never credentials, keys, or settings) off a
CodeSpace over the multiplexed SSH connection, then lands it in the configured
agent-logger storage target under ``.codespaces/<name>`` by shelling out to the
``session-sync push`` CLI (agent-logger).

This keeps the two plugins decoupled with no shared venv: agent-codespaces owns
the CodeSpace/SSH pull; agent-logger owns the storage pattern. The binary
``tar`` payload is transferred base64-wrapped in sentinels so stray banner/log
text on the SSH channel can never corrupt it (a real failure mode -- see the
dotfiles ``sync-copilot-sessions.ps1`` history).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import string
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from plugin_activation import ActivePlugin, resolve_active_plugins
from session_liveness_probe import SessionLiveness, build_probe_script, parse_probe_output

from ._ssh_retry import exec_with_retry
from .codespace_config import CodespaceSource

log = logging.getLogger("agent-codespaces")

# A Shutdown CodeSpace boots on connect; match the SSH command's patience.
_BOOT_TIMEOUT = float(os.environ.get("AGENT_CODESPACES_BOOT_TIMEOUT", "180"))

#: The push subprocess (``session-sync push``) previously ran unbounded --
#: a hung/stuck push could block a reclaim indefinitely, past whatever
#: budget the caller (e.g. the claim-provider registry's reclaim callback
#: timeout) actually enforces, leaving the resource neither confirmed
#: recovered nor deleted (claim-provider-pattern effort review finding:
#: "Align reclaim timeout with full recovery and deletion phases" --
#: "its session-sync push subprocess is not bounded by that timeout
#: either").
_PUSH_TIMEOUT_SECONDS = 60.0

_B64_START = "===ACS_SESSION_B64_START==="
_B64_END = "===ACS_SESSION_B64_END==="
_B64_CHARS = frozenset(string.ascii_letters + string.digits + "+/=")

# Remote: tar ONLY the session-state tree + db index files (never the rest of
# ~/.copilot -- OAuth/credential state, keys, settings), gzip + base64, wrapped
# in sentinels. Emits nothing between the sentinels when there are no sessions.
_PULL_CMD = (
    "cd ~/.copilot 2>/dev/null && { "
    "files=$(ls -d session-state session-store.db session-store.db-wal "
    'session-store.db-shm 2>/dev/null); [ -n "$files" ] && '
    "{ echo " + _B64_START + "; tar czf - $files 2>/dev/null | base64 -w0; "
    "echo; echo " + _B64_END + "; }; } || true"
)


def _extract_b64(text: str) -> str:
    """Return the base64 payload between the sentinels, keeping only valid
    base64 characters (robust to interleaved log lines)."""
    out: list[str] = []
    capture = False
    for line in text.splitlines():
        if _B64_START in line:
            capture = True
            continue
        if _B64_END in line:
            break
        if capture:
            out.append("".join(ch for ch in line if ch in _B64_CHARS))
    return "".join(out)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left.resolve(strict=False) == right.resolve(strict=False)


def _agent_codespaces_marketplace(
    active: tuple[ActivePlugin, ...],
) -> tuple[str | None, str]:
    codespaces = [plugin for plugin in active if plugin.name == "agent-codespaces"]

    explicit_root = os.environ.get("COPILOT_PLUGIN_ROOT", "").strip()
    if explicit_root:
        matches = [
            plugin
            for plugin in codespaces
            if any(
                _same_path(selected.root, Path(explicit_root).expanduser())
                for selected in plugin.live_roots
            )
        ]
        if len(matches) == 1:
            return matches[0].marketplace, ""
        return None, (
            "session-sync unavailable: the invoking agent-codespaces payload "
            "does not identify one active marketplace installation"
        )

    marketplaces = {plugin.marketplace for plugin in codespaces}
    if len(marketplaces) == 1:
        return next(iter(marketplaces)), ""
    detail = (
        "agent-codespaces marketplace identity is unavailable"
        if not marketplaces
        else "multiple active agent-codespaces marketplace installations found"
    )
    return None, f"session-sync unavailable: {detail}"


def find_session_sync() -> tuple[str | None, str]:
    """Locate the same-marketplace agent-logger payload command."""
    active = tuple(resolve_active_plugins().active.values())
    marketplace, unavailable = _agent_codespaces_marketplace(active)
    if marketplace is None:
        return None, unavailable
    loggers = [
        plugin
        for plugin in active
        if plugin.name == "agent-logger" and plugin.marketplace == marketplace
    ]
    if not loggers:
        return None, (
            "session-sync unavailable: agent-logger is not installed and enabled "
            f"in marketplace '{marketplace}'"
        )
    if len(loggers) != 1:
        return None, (
            "session-sync unavailable: multiple active agent-logger installations "
            f"found in marketplace '{marketplace}'"
        )

    command_name = "session-sync.cmd" if sys.platform == "win32" else "session-sync"
    command = loggers[0].root / "bin" / command_name
    if not command.is_file():
        return None, (
            "session-sync unavailable: agent-logger's payload command is missing: "
            f"{command}"
        )
    if sys.platform != "win32" and not os.access(command, os.X_OK):
        return None, (
            "session-sync unavailable: agent-logger's payload command is not executable: "
            f"{command}"
        )
    return str(command), ""


async def _connect_with_retry(
    manager, name: str, *, timeout: float, account: str | None = None,
    token: str | None = None,
) -> None:
    """ensure_connected with boot-patience retry (a Shutdown CS boots here).

    ``account`` -- when given, pins the connection to a caller-resolved
    account (e.g. the one :func:`lifecycle.get_codespace_status` already
    confirmed owns this CodeSpace) rather than re-resolving via
    ``account_for_codespace``'s own limited listing/ambient fallback, which
    can silently disagree for a CodeSpace outside that listing's first page
    or owned by a non-ambient/beyond-first-page account (claim-provider-
    pattern effort review finding: "Preserve the resolved account through
    CodeSpace reclamation").

    ``token`` -- when given, passed straight through to
    :class:`codespace_config.CodespaceSource` to use this EXACT pre-minted
    token directly, bypassing its own internal ``env_for_account`` re-
    derivation entirely (which can silently fall back to ambient
    credentials) -- see that class's own docstring."""
    from .lifecycle import account_for_codespace
    if account is None and token is None:
        account = account_for_codespace(name)
    source = CodespaceSource(name, account=account, token=token)
    deadline = time.monotonic() + timeout
    backoff = 3.0
    while True:
        try:
            await manager.ensure_connected(name, source, [])
            return
        except (ConnectionError, TimeoutError) as exc:
            if time.monotonic() + backoff >= deadline:
                raise
            log.info("CodeSpace %s not ready (booting?): %s -- retry in %.0fs",
                     name, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 20.0)


async def _pull_tar_bytes(manager, name: str, *, timeout: float) -> bytes | None:
    """Run the remote tar+base64 and return decoded gzip-tar bytes (or None)."""
    result = await exec_with_retry(manager, name, _PULL_CMD, timeout=timeout)
    b64 = _extract_b64(result.stdout or "")
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except ValueError:  # binascii.Error is a ValueError subclass
        log.warning("session pull from %s produced an undecodable payload", name)
        return None


def _stage_and_push(tar_bytes: bytes, name: str, *, verbose: bool) -> dict:
    """Extract the pulled archive to a staging dir (validating it), then push it
    into the agent-logger hub under ``.codespaces/<name>``."""
    with tempfile.TemporaryDirectory(prefix=f"acs-sessions-{name}-") as tmp:
        staging = Path(tmp)
        tar_path = staging / "sessions.tar.gz"
        tar_path.write_bytes(tar_bytes)
        try:
            with tarfile.open(tar_path, "r:gz") as tf:
                tf.extractall(staging)  # noqa: S202 - own CodeSpace session data
        except (tarfile.TarError, OSError) as exc:
            return {"ok": False, "session_count": 0,
                    "detail": f"invalid/corrupt session archive: {exc}"}
        finally:
            tar_path.unlink(missing_ok=True)

        # Drop a corrupt session-store.db rather than store a bad index.
        db = staging / "session-store.db"
        if db.is_file():
            with open(db, "rb") as fh:
                header_ok = fh.read(15) == b"SQLite format 3"
            if not header_ok:
                log.warning("session-store.db failed SQLite header check; dropping")
                db.unlink()

        ss = staging / "session-state"
        count = sum(1 for d in ss.iterdir() if d.is_dir()) if ss.is_dir() else 0
        ok, detail = _push_via_session_sync(staging, f".codespaces/{name}", verbose=verbose)
        return {"ok": ok, "session_count": count, "detail": detail}


def _is_stale_session_sync(stderr: str) -> bool:
    """True when session-sync rejected the ``push`` subcommand.

    Indicates the deployed agent-logger predates ``session-sync push`` -- a
    version skew where agent-codespaces (which calls ``push``) is newer than the
    installed agent-logger. The CLI prints e.g.
    ``argument command: invalid choice: 'push' (choose from run, status, doctor)``.
    See dotfiles#246.
    """
    return "invalid choice: 'push'" in (stderr or "").lower()


def _push_via_session_sync(staging: Path, machine_label: str, *, verbose: bool) -> tuple[bool, str]:
    """Shell out to ``session-sync push`` (agent-logger) to land *staging* in
    the configured hub target under *machine_label*."""
    exe, unavailable = find_session_sync()
    if not exe:
        return False, unavailable
    cmd = [exe, "push", "--source", str(staging), "--machine", machine_label]
    if verbose:
        cmd.append("--verbose")
    child_env = os.environ.copy()
    child_env.pop("COPILOT_PLUGIN_ROOT", None)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=child_env,
            timeout=_PUSH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"session-sync push timed out after {_PUSH_TIMEOUT_SECONDS:.0f}s"
        )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        err = (proc.stderr or out).strip()
        if _is_stale_session_sync(err):
            return False, (
                "deployed session-sync is stale (no 'push' subcommand) -- "
                "agent-logger is older than agent-codespaces. Upgrade it to "
                "restore session recovery: run the agent-logger installer "
                "(plugins/agent-logger/scripts/install.ps1 install on Windows, "
                "install.sh install on Linux/WSL) or `agent-worktrees update`. "
                f"[session-sync: {err}]"
            )
        return False, f"session-sync push failed: {err}"
    return True, out


def sync_codespace_sessions(
    name: str,
    *,
    timeout: float = 300.0,
    verbose: bool = False,
    skip_if_shutdown: bool = False,
    account: str | None = None,
    token: str | None = None,
    lock: TargetLock | None = None,  # noqa: F821 - ssh_manager, imported lazily
) -> dict:
    """Pull a CodeSpace's Copilot session-state and land it in the agent-logger
    hub under ``.codespaces/<name>``.

    ``account`` -- when given, pins the connection to a caller-resolved
    account (see :func:`_connect_with_retry`'s own docstring) instead of
    re-resolving it independently.

    ``token`` -- when given, passed straight through to
    :func:`_connect_with_retry` to use this EXACT pre-minted token
    directly rather than let the connection re-derive (and possibly
    silently ambient-fallback) credentials for ``account`` moments later
    (claim-provider-pattern effort review finding: "Preserve validated
    credentials during status and reclaim").

    ``lock`` -- when given, an ALREADY-ACQUIRED ``ssh_manager.TargetLock``
    for ``name`` that this call reuses instead of acquiring/releasing its
    own. This is the session-rescue-parity Phase 3 lock-widening seam: a
    destructive caller (stop/finalize/delete/prune/reclaim) that must hold
    the target lock across its own **entire** sync-then-act sequence (not
    only this sync sub-step) acquires the lock itself, passes it here, and
    releases it only after its own destructive action completes -- closing
    the window where a concurrent capture could pass its liveness probe and
    pull between "sync finished" and "the destructive action actually
    ran". When ``lock`` is None (the default), this function acquires and
    releases its own lock exactly as before -- unchanged for every
    preserving/no-widening caller.

    Returns a result dict: ``{ok, session_count, detail, skipped?}``. Never
    raises for routine connect/pull failures -- callers (delete hook, finalize)
    treat a failed sync as non-fatal and decide whether to proceed.

    ``skip_if_shutdown`` -- when True, if the CodeSpace is already ``Shutdown``,
    return a no-op success **without booting it**. A Shutdown box's sessions were
    already captured when it was stopped/finalized, so booting it just to re-pull
    is wasteful and (on a busy account) trips the "too many codespaces running"
    quota. The *preserving* callers (``finalize`` without ``--delete``, ``stop``)
    pass this; the *destructive* callers (``delete``, ``finalize --delete``, the
    final pre-prune pull) do NOT -- they must recover even a Shutdown box before
    it is gone, booting if necessary.
    """
    if skip_if_shutdown:
        try:
            from .lifecycle import _SHUTDOWN_STATE, list_codespaces

            state = next(
                (cs.state for cs in list_codespaces() if cs.name == name), None
            )
        except RuntimeError:
            state = None  # can't list (auth/network) -> fall through, try normally
        if state == _SHUTDOWN_STATE:
            log.info(
                "CodeSpace %s is Shutdown; skipping boot-to-recover "
                "(sessions were captured when it was stopped/finalized)", name,
            )
            return {"ok": True, "skipped": True, "session_count": 0,
                    "detail": "already Shutdown; skipped boot-to-recover"}

    from ssh_manager import ConnectionManager, TargetBusyError, TargetLock

    owns_lock = lock is None
    if owns_lock:
        lock = TargetLock(name, op="session-sync")
        try:
            lock.acquire(force=False)
        except TargetBusyError as busy:
            return {"ok": False, "skipped": True, "session_count": 0,
                    "detail": f"target busy, skipped sync: {busy}"}

    manager = ConnectionManager()

    async def _run() -> dict:
        try:
            await _connect_with_retry(
                manager, name, timeout=_BOOT_TIMEOUT, account=account, token=token,
            )
        except (ConnectionError, TimeoutError, RuntimeError) as exc:
            # RuntimeError covers the SSH-config-fetch timeout / gh failures
            # (codespace_config) that an unbootable CodeSpace raises -- treat
            # them as a connect failure, never propagate, so a --force
            # delete/finalize is not blocked by an unreachable target (#155).
            return {"ok": False, "session_count": 0, "detail": f"could not connect: {exc}"}
        try:
            tar_bytes = await _pull_tar_bytes(manager, name, timeout=timeout)
        finally:
            await manager.disconnect(name)
        if not tar_bytes:
            return {"ok": True, "session_count": 0, "detail": "no sessions on CodeSpace"}
        return _stage_and_push(tar_bytes, name, verbose=verbose)

    try:
        return asyncio.run(_run())
    except Exception as exc:  # recovery must never block delete (#155)
        # The contract is "never raise for routine connect/pull failures". Any
        # unexpected error here (e.g. an SSH/relay failure surfacing as a plain
        # Exception) must still return a failed-recovery result so a --force
        # finalize/delete can proceed rather than aborting.
        return {"ok": False, "session_count": 0,
                "detail": f"session recovery error: {exc}"}
    finally:
        if owns_lock:
            lock.release()


def _capture_hold_reason(name: str, *, account: str | None) -> str | None:
    """Return a defer reason if *name* is held by any of the four holder
    shapes ``pool.derive_disposition`` treats as ``IN_USE`` (a local lease,
    a ``#897`` claim -- which shows up as a local lease too, a
    cross-machine L2 Git-ref lease overlay, or a live display-name beacon),
    else ``None``.

    session-rescue-parity Phase 1's recorded decision: capture defers on
    ANY of these four regardless of holder identity -- a capture requires
    no lease/claim identity of its own to invoke, so the safety property is
    this disposition check, not caller identity (mirroring
    ``agent_containers.replacement``'s unconditional
    ``get_lease(...) is not None`` defer for ``rescue-capture``) -- **except**
    an *orphaned* ``#897`` claim (the owning worktree path positively gone,
    per ``pool._holder_worktree_gone``): nothing is heartbeating it anymore,
    so it is not treated as a live hold here, mirroring how ``pool.py``
    itself surfaces (but does not gate on) ``orphaned``.

    **Fails closed on every signal, not just a confirmed hold**: a lease-store
    read failure, a listing failure for the beacon check, or an L2-overlay
    read failure each defer too -- an *unknown* hold state must never be
    silently treated as *unheld*, which would defeat the whole point of this
    gate (review finding on the original cut of this function).
    """
    from .lease import LEASE_FILE, Lease, get_lease
    from .pool import _holder_worktree_gone

    if LEASE_FILE.exists():
        # `lease.get_lease()`/`_read_leases()` intentionally degrade a
        # corrupt/unreadable ``leases.json``, OR a syntactically-valid file
        # whose record for THIS codespace fails to construct a ``Lease``
        # (a ``TypeError``, silently ``continue``'d past), to ``{}``/absent
        # for pool.py's display-only purposes -- exactly the "no lease"
        # outcome this gate must never silently accept. Read it directly
        # first so either failure is caught HERE as an unknown-state defer,
        # before ever calling into the degrade-safe helper.
        try:
            raw = json.loads(LEASE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return f"could not confirm lease state (fail-closed): leases.json unreadable: {exc}"
        rec = (raw or {}).get(name) if isinstance(raw, dict) else None
        if rec is not None:
            try:
                Lease(**rec)
            except TypeError as exc:
                return (
                    f"could not confirm lease state (fail-closed): "
                    f"malformed lease record for {name!r}: {exc}"
                )

    try:
        lease = get_lease(name)
    except Exception as exc:
        return f"could not confirm lease state (fail-closed): {exc}"
    if lease is not None:
        try:
            orphaned = _holder_worktree_gone(lease.worktree)
        except Exception as exc:
            # A syntactically-valid-but-schema-malformed record (e.g. a
            # non-string ``worktree``) can pass `Lease(**rec)` (a dataclass
            # constructor validates keys, not field types) yet still raise
            # here (`os.path.isabs()` on a non-str). Treat evaluating the
            # orphan check itself as an unknown lease state, not a crash.
            return (
                f"could not confirm lease state (fail-closed): error "
                f"evaluating orphan status: {exc}"
            )
        if not orphaned:
            return f"CodeSpace has an active lease (effort {lease.effort!r})"

    try:
        from . import gh_account

        # Re-verify the account is still authenticatable immediately before
        # this listing -- `_list_codespaces_under` re-derives its own
        # environment via `env_for_account`, which silently falls back to
        # ambient auth on a minting failure; that would let an ambient
        # listing (possibly a DIFFERENT account's same-named CodeSpace) be
        # read here instead of the resolved account's real beacon state.
        if not gh_account.token_for_account(account):
            return (
                "could not confirm cross-machine beacon state (fail-closed): "
                f"could not authenticate as account {account!r}"
            )

        from .lifecycle import _list_codespaces_under
        from .pool import _beacon_id

        entries = _list_codespaces_under(account)
        info = next((cs for cs in entries if cs.name == name), None)
        if info is None:
            # The status preflight already confirmed this exact name exists
            # under this exact account -- an absent entry here means
            # `_list_codespaces_under`'s own listing is incomplete (its
            # ``--limit 50`` cap, or malformed-output normalization to an
            # empty list), not that the CodeSpace has no beacon. Treat an
            # unknown beacon state as a hold, never as cleared.
            return (
                "could not confirm cross-machine beacon state (fail-closed): "
                "CodeSpace missing from the account's own listing"
            )
        beacon = _beacon_id(info.display_name)
    except Exception as exc:
        return f"could not confirm cross-machine beacon state (fail-closed): {exc}"
    if beacon is not None:
        return "CodeSpace has a live cross-machine display-name beacon"

    try:
        from . import coordination

        l2_leases = coordination.list_leases()
        if l2_leases is None:
            # None means the L2 store itself was unreadable -- fail closed
            # rather than coercing it to an empty map via `or {}`, which
            # would silently let capture proceed past an unknown holder.
            # Note (accepted narrower residual): `list_leases()` silently
            # drops an individual malformed record rather than failing the
            # whole read, so a record malformed for THIS exact target only
            # is not distinguished from "no L2 hold" here -- unlike the
            # local-lease check above, which validates the exact target's
            # record directly. Closing that would require duplicating
            # `coordination.py`'s own subprocess/JSON parsing rather than
            # reusing its public, already degrade-safe `list_leases()`.
            return (
                "could not confirm cross-machine L2 lease state (fail-closed): "
                "the L2 store is unavailable"
            )
        l2 = l2_leases.get(name)
    except Exception as exc:
        return f"could not confirm cross-machine L2 lease state (fail-closed): {exc}"
    if l2 is not None and getattr(l2, "live", False):
        return "CodeSpace has a live cross-machine L2 lease overlay"
    return None


async def _probe_codespace_liveness(manager, name: str, *, timeout: float) -> SessionLiveness:
    """Run the vendored, transport-agnostic liveness probe over this
    CodeSpace's existing SSH connection.

    Uses ``session_liveness_probe`` (session-rescue-parity Phase 2): the
    script requires Bash, so it is always invoked via ``bash -c``, and the
    exit code comes from ``CommandResult.exit_code`` (ssh-manager's
    ``exec_with_retry``), never ``.returncode``.
    """
    import shlex

    command = f"bash -c {shlex.quote(build_probe_script())}"
    result = await exec_with_retry(manager, name, command, timeout=timeout)
    return parse_probe_output(result.exit_code, result.stdout, result.stderr)


def capture_codespace_sessions(
    name: str,
    *,
    account: str | None = None,
    timeout: float = 300.0,
    verbose: bool = False,
) -> dict:
    """Non-destructive, non-lifecycle-transition session capture: pull a
    CodeSpace's Copilot session-state while it stays leased and running.

    This is the CodeSpaces peer to ``agent-containers``' ``rescue-capture``
    (``ThomasMichon/copilot-extensions#3574``) -- session-rescue-parity
    Phase 3. Unlike :func:`sync_codespace_sessions`, this function:

    - **never boots or connects to a non-``Available`` CodeSpace.** A hard
      preflight (list/inspect state only) defers immediately for any state
      other than ``Available`` -- this is capture-only-shaped, not
      destroy/finalize-shaped; it never reuses
      :func:`sync_codespace_sessions`'s boot-tolerant ``skip_if_shutdown``
      special-case, which still boots every OTHER non-Shutdown,
      non-Available state.
    - **defers on any active hold** (local lease, ``#897`` claim,
      cross-machine L2, beacon) regardless of holder identity, checked BOTH
      up front and again immediately after the SSH target lock is acquired
      -- the up-front check runs before any lock exists, so it alone cannot
      close the window where a hold appears between that check and the lock
      actually taking effect; the fence-time re-check does. See
      :func:`_capture_hold_reason`, which also fails closed (defers) on any
      read failure -- an *unknown* hold state is never treated as *unheld*.
    - **requires an unambiguous account**: an explicit ``account`` or a
      confirmed exact per-name binding (``account_binding.bound_account``)
      -- never ``get_codespace_status_with_account``'s generic
      multi-candidate scan, whose first-match semantics are exactly the
      same-name-across-accounts ambiguity this item exists to close. Fails
      closed (defers) when neither is available, and mints the connection
      token for that EXACT resolved account once, up front -- never letting
      ``CodespaceSource``/``env_for_account`` re-derive (and possibly
      silently ambient-fallback for) credentials moments later, which would
      let a same-named CodeSpace under the ambient account be read under
      this identity if minting failed between the preflight and the
      connect (mirroring ``claim_provider_cli.py``'s reclaim path).
    - **gates the pull on the vendored liveness probe both before AND
      after** the pull, never capturing a session whose state is anything
      but ``idle`` at either checkpoint. This narrows, but does not close,
      the underlying race: a session that both acquires AND releases its
      ``inuse.*.lock`` entirely within the pull's own duration is invisible
      to both probes alike -- an explicitly accepted residual risk for a
      periodic/advisory capture (session-rescue-parity Phase 1's recorded
      decision), identical in kind to what ``rescue-capture`` already
      accepts.
    - **re-checks the hold status too, both immediately after acquiring the
      SSH target lock and again after the pull completes.** A lease/claim
      is acquired through the separate lease/coordination store, never this
      ``TargetLock``, so a new holder can appear at any point up to and
      including while the pull itself is in flight; a failed re-check is
      treated the same as a found hold.

    Returns ``{ok, session_count, detail, deferred}``. Never raises.
    """
    from .account_binding import bound_account
    from .lifecycle import _AVAILABLE_STATE, get_codespace_status_with_account

    if account is None:
        account = bound_account(name)
        if account is None:
            return {
                "ok": False, "deferred": True, "session_count": 0,
                "detail": (
                    f"no explicit account and no exact account binding for "
                    f"{name!r}; refusing to guess which account owns it "
                    f"(a same-named CodeSpace can exist under a different "
                    f"account)"
                ),
            }

    try:
        exists, state, _confirmed_account = get_codespace_status_with_account(
            name, account,
        )
    except RuntimeError as exc:
        return {"ok": False, "deferred": True, "session_count": 0,
                "detail": f"could not confirm CodeSpace status: {exc}"}
    if not exists:
        return {"ok": False, "deferred": True, "session_count": 0,
                "detail": f"CodeSpace {name!r} not found under account {account!r}"}
    if state != _AVAILABLE_STATE:
        return {
            "ok": False, "deferred": True, "session_count": 0,
            "detail": (
                f"CodeSpace state {state!r} is not Available; capture never "
                f"boots or connects to a non-running venue"
            ),
        }

    hold_reason = _capture_hold_reason(name, account=account)
    if hold_reason is not None:
        return {"ok": False, "deferred": True, "session_count": 0,
                "detail": hold_reason}

    # Mint the token ONCE for the exact resolved account, right here --
    # never letting the connection re-derive (and possibly silently
    # ambient-fallback for) credentials moments later.
    from . import gh_account

    token = gh_account.token_for_account(account)
    if not token:
        return {
            "ok": False, "deferred": True, "session_count": 0,
            "detail": (
                f"could not mint an authenticated gh token for account "
                f"{account!r}; refusing to connect under ambient fallback"
            ),
        }

    from ssh_manager import ConnectionManager, TargetBusyError, TargetLock

    lock = TargetLock(name, op="codespace-capture")
    try:
        lock.acquire(force=False)
    except TargetBusyError as busy:
        return {"ok": False, "deferred": True, "session_count": 0,
                "detail": f"target busy, skipped capture: {busy}"}

    try:
        # ``ConnectionManager()`` construction lives inside this protected
        # block too: if it raises (e.g. local SSH configuration), the lock
        # acquired above must still be released, and this function's own
        # never-raises contract must still hold.
        manager = ConnectionManager()

        async def _run() -> dict:
            # Re-check the hold status INSIDE the fence too -- the checks
            # above all ran before this lock was acquired, leaving a window
            # in which a lease/claim/beacon/L2 hold could still appear
            # between that check and the lock actually taking effect.
            fenced_hold_reason = _capture_hold_reason(name, account=account)
            if fenced_hold_reason is not None:
                return {"ok": False, "deferred": True, "session_count": 0,
                        "detail": fenced_hold_reason}
            try:
                await _connect_with_retry(
                    manager, name, timeout=timeout, account=account, token=token,
                )
            except (ConnectionError, TimeoutError, RuntimeError) as exc:
                return {"ok": False, "deferred": True, "session_count": 0,
                        "detail": f"could not connect: {exc}"}
            try:
                liveness = await _probe_codespace_liveness(manager, name, timeout=30.0)
                if liveness.state != "idle":
                    return {
                        "ok": False, "deferred": True, "session_count": 0,
                        "detail": (
                            f"session liveness is {liveness.state!r} "
                            f"({liveness.reason or 'active session-state lock present'}); "
                            f"capture-only never disturbs a mid-write session"
                        ),
                    }
                tar_bytes = await _pull_tar_bytes(manager, name, timeout=timeout)
                if not tar_bytes:
                    return {"ok": True, "deferred": False, "session_count": 0,
                            "detail": "no sessions on CodeSpace"}
                # Re-validate AFTER the pull, not only before it (Phase 3's
                # own item): narrows, but -- per this function's docstring --
                # does not close, the acquire-then-release-within-the-pull
                # race.
                post_liveness = await _probe_codespace_liveness(
                    manager, name, timeout=30.0,
                )
                if post_liveness.state != "idle":
                    return {
                        "ok": False, "deferred": True, "session_count": 0,
                        "detail": (
                            f"session became {post_liveness.state!r} during "
                            f"the pull; discarding this capture rather than "
                            f"staging a possibly mid-write snapshot"
                        ),
                    }
                # Re-check the hold status too, not just liveness: a
                # lease/claim is acquired through the lease/coordination
                # store, not this SSH target lock, so a new holder can
                # appear while the pull itself is in progress. Treat a
                # failed re-check the same as a found hold -- defer rather
                # than stage/push.
                try:
                    post_hold_reason = _capture_hold_reason(name, account=account)
                except Exception as exc:
                    post_hold_reason = f"could not confirm hold state after pull: {exc}"
                if post_hold_reason is not None:
                    return {
                        "ok": False, "deferred": True, "session_count": 0,
                        "detail": (
                            f"{post_hold_reason} (appeared during the pull); "
                            f"discarding this capture rather than staging it"
                        ),
                    }
            finally:
                await manager.disconnect(name)
            result = _stage_and_push(tar_bytes, name, verbose=verbose)
            result["deferred"] = False
            return result

        return asyncio.run(_run())
    except Exception as exc:
        return {"ok": False, "deferred": True, "session_count": 0,
                "detail": f"capture error: {exc}"}
    finally:
        lock.release()
