"""The **Dev Tunnels** federation rendezvous backend -- Phase 4 of the
``agent-dispatch-federation`` effort (the work-environment / single-user
counterpart to Phase 3's Gateway-hosted backend).

Where :class:`~agent_dispatch.federation.CoordinatorRendezvous` (Phase 3) needs
a coordinator to be reachable at a stable URL, a locked-down single-user work
environment has no such stable front -- but it does have **Microsoft Dev
Tunnels**: an Azure-relay, outbound-only, Entra-authed, single-user-by-default
directory+transport the operator doesn't self-host. ``dtssh`` (the dotfiles
`agent-ssh` plugin's transport) already proved the enumerate-then-connect
pattern this backend reuses: publish a small, portless, labeled Dev Tunnel per
federating instance, carry the directory entry in its JSON ``description``,
and enumerate by label to read the fleet.

This module implements the same :class:`~agent_dispatch.federation.Rendezvous`
Protocol as the Phase-3 backend -- nothing above the interface (the fenced
lease, the :class:`~agent_dispatch.federation_runner.FederationRunner`) changes
when this backend is swapped in; only the *transport* differs.

**Single-user boundary (by construction, not policy):** ``devtunnel list``
only ever enumerates tunnels owned by the CLI's own logged-in Dev Tunnels
account, so cross-account/foreign-instance leakage is not something this
backend can get wrong -- it is a substrate property, not an enforced check.

**Liveness is directory-level, not tunnel-level:** the underlying Dev Tunnel
object persists for its configured ``--expiration`` (renewed on every
heartbeat) independent of whether the owning process is alive, so an entry is
only considered *live* when its embedded ``last_seen`` timestamp is fresher
than ``ttl_seconds`` -- the same TTL-reap vocabulary
:class:`~agent_dispatch.satellites.FleetDirectory` uses for the Phase-3
backend. A stale tunnel is not deleted here (that would race a
still-registering peer); ``reap_stale`` is an explicit opt-in for a caller
that wants to garbage-collect old entries.

**Enumeration backs off on repeated failure:** every enumeration path
(:meth:`DevTunnelRendezvous._list_tunnels` for
:meth:`~DevTunnelRendezvous.discover_peers`;
:meth:`DevTunnelRendezvous._list_tunnels_fresh` for
:meth:`~DevTunnelRendezvous.discover_coordinator` and
:meth:`~DevTunnelRendezvous.reap_stale`) shares one backoff timer that
doubles on each consecutive ``devtunnel list`` failure (a lapsed
``dtssh login`` being the common case) up to a cap, so a persistent failure
does not re-invoke the CLI at the federation runner's full tick rate. **What
differs is what each caller does while backed off:**

- ``discover_peers`` (awareness plane) serves the last successfully
  enumerated snapshot -- a degraded but harmless stale presence picture.
- ``discover_coordinator`` (**claim plane** --
  :class:`~agent_dispatch.lease.CoordinatorLease` takes ``None`` as license to
  take over) raises :class:`DevTunnelError` **without even attempting the
  CLI call** while backed off, rather than answering from stale/cached data:
  a backed-off node's *own* enumeration outage says nothing about whether the
  real coordinator is actually gone, and its own ``register``/``heartbeat``
  calls are a *separate*, unbacked-off path that could still succeed --
  serving a stale "no coordinator" reply here would let it register itself
  as coordinator anyway, producing two simultaneous coordinators.
- ``reap_stale`` (maintenance) always attempts a **fresh** enumeration,
  ignoring the backoff timer entirely -- deciding what to delete from a
  stale snapshot is a correctness bug in its own right (a tunnel that looked
  dead when last cached may have since refreshed), independent of the
  runner-tick-rate concern the timer otherwise protects against.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .procutil import run_background_capture
from .satellites import DEFAULT_TTL_SECONDS, UnknownInstance

#: The label every federation-managed tunnel carries; enumeration filters on
#: it (``devtunnel list --all-labels <label>``) so this backend never sees or
#: touches a tunnel some other tool (e.g. ``dtssh`` itself) created.
DEFAULT_LABEL = "agent-dispatch-federation"

#: How long a published tunnel is allowed to exist before Dev Tunnels itself
#: expires it. Renewed on every heartbeat/register so a live instance's tunnel
#: never lapses; a dead instance's tunnel self-expires eventually even if
#: nothing ever reaps it from the directory side.
DEFAULT_EXPIRATION = "30d"

#: Timeout for a single ``devtunnel`` CLI invocation. The management service is
#: a network round trip; generous but bounded so a hung call doesn't wedge a
#: federation tick.
DEFAULT_TIMEOUT = 20.0

#: Enumeration backoff (``devtunnel list``, the awareness-plane read every
#: tick makes). A repeated enumeration failure -- most commonly a lapsed
#: ``dtssh login`` -- must not hot-loop the CLI at the runner's tick interval;
#: the delay doubles on each consecutive failure up to the cap, and resets on
#: the next success.
DEFAULT_ENUM_BACKOFF_SECONDS = 5.0
MAX_ENUM_BACKOFF_SECONDS = 300.0

CommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[str] | None"]


class DevTunnelError(RuntimeError):
    """A ``devtunnel`` CLI invocation failed or returned unparseable output."""


def _default_binary() -> str:
    """Resolve the ``devtunnel`` executable.

    ``AGENT_DISPATCH_FEDERATION_DEVTUNNEL_BIN`` wins when set (explicit
    override, e.g. for a non-standard install). Otherwise prefer whatever is
    on ``PATH``, then fall back to the copy ``dtssh`` vendors under its own
    data directory (Windows: ``%LOCALAPPDATA%\\dtssh\\bin``) -- reusing the
    same binary + auth cache ``dtssh login`` already populated, per the
    effort's "reuse dtssh discovery/auth" design.
    """
    override = os.environ.get("AGENT_DISPATCH_FEDERATION_DEVTUNNEL_BIN")
    if override:
        return override
    found = shutil.which("devtunnel")
    if found:
        return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        exe_name = "devtunnel.exe" if os.name == "nt" else "devtunnel"
        candidate = Path(local_app_data) / "dtssh" / "bin" / exe_name
        if candidate.exists():
            return str(candidate)
    return "devtunnel"


def _tunnel_id(instance: str) -> str:
    """Map a federation ``instance`` id to a valid, deterministic Dev Tunnel id.

    Tunnel ids share a single global namespace (not scoped to a label) and
    accept a restricted charset, so naive sanitization is not injective: e.g.
    ``"a/b"`` and ``"a-b"`` would otherwise collapse to the same id, and two
    distinct long ids could collide after truncation -- letting one instance's
    tunnel silently overwrite/heartbeat/deregister another's. A short digest of
    the *original* instance string is appended so the mapping is
    collision-resistant regardless of how the human-readable prefix collapses;
    the ``adf-`` prefix keeps a stray tunnel recognizable as federation-owned
    even without checking labels.
    """
    digest = hashlib.sha256(instance.encode("utf-8")).hexdigest()[:16]
    safe = "".join(c if c.isalnum() else "-" for c in instance.lower()).strip("-")
    prefix = (safe or "instance")[:40]
    return f"adf-{prefix}-{digest}"


class DevTunnelRendezvous:
    """Rendezvous backend over the Dev Tunnels management service.

    Every :class:`~agent_dispatch.federation.Rendezvous` operation shells out
    to the ``devtunnel`` CLI (JSON mode); ``runner`` is the injection seam unit
    tests use to avoid a real Dev Tunnels account. Distinct from
    :class:`~agent_dispatch.federation.CoordinatorRendezvous` (Phase 3,
    coordinator-hosted) only in transport -- both satisfy the same Protocol.
    """

    def __init__(
        self,
        *,
        binary: str | None = None,
        label: str = DEFAULT_LABEL,
        expiration: str = DEFAULT_EXPIRATION,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT,
        runner: CommandRunner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._binary = binary or _default_binary()
        self._label = label
        self._expiration = expiration
        self._ttl = float(ttl_seconds)
        self._timeout = timeout
        self._runner = runner or self._spawn
        # Backoff-deadline clock only (never entry liveness -- _is_live and
        # register/heartbeat's last_seen/registered_at compare against real
        # wall-clock time embedded in tunnel descriptions, so they always use
        # time.time() directly). Defaults to time.monotonic() specifically
        # *because* an NTP correction or manual wall-clock adjustment must
        # never extend or truncate a purely-local, in-process backoff window
        # -- monotonic time can only ever move forward at a steady rate.
        self._clock = clock
        # Enumeration backoff state (see DEFAULT_ENUM_BACKOFF_SECONDS).
        self._enum_backoff_seconds = 0.0
        self._enum_backoff_until = 0.0
        self._enum_cache: list[dict] = []

    # -- transport --------------------------------------------------------

    def _spawn(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        return run_background_capture([self._binary, *args], timeout=self._timeout)

    def _call(self, args: list[str], *, allow_missing: bool = False) -> dict | None:
        """Run a ``devtunnel`` subcommand with ``--json`` and parse its output.

        Returns ``None`` when ``allow_missing`` and the CLI reports the tunnel
        doesn't exist (the ``show``-before-create/update probe); raises
        :class:`DevTunnelError` for any other non-zero exit or unparseable
        output, so a real transport failure is never mistaken for absence.
        """
        result = self._runner([*args, "--json"])
        if result is None:
            raise DevTunnelError(f"devtunnel {' '.join(args)} timed out or failed to start")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if allow_missing and _looks_like_not_found(detail):
                return None
            raise DevTunnelError(
                f"devtunnel {' '.join(args)} failed (exit {result.returncode}): {detail}"
            )
        text = (result.stdout or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DevTunnelError(
                f"devtunnel {' '.join(args)} returned non-JSON output: {text[:200]!r}"
            ) from exc

    # -- entry (de)serialization -------------------------------------------

    @staticmethod
    def _encode(payload: dict) -> str:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _decode(self, tunnel: dict) -> dict | None:
        """Reconstruct a directory-entry dict from a tunnel's ``description``.

        Returns ``None`` for a tunnel this backend doesn't recognize as one of
        its own -- no parseable federation payload, or a payload whose fields
        don't have the expected types (a malformed or hand-crafted
        ``description`` must never raise and break the whole fleet read; it is
        simply not one of ours).
        """
        raw = tunnel.get("description")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        instance = payload.get("instance")
        if not isinstance(instance, str) or not instance:
            return None
        try:
            epoch = int(payload.get("epoch", 0))
            last_seen = float(payload.get("last_seen", 0.0))
            registered_at = float(payload.get("registered_at", last_seen))
        except (TypeError, ValueError):
            return None
        role = payload.get("role", "peer")
        if not isinstance(role, str) or not role:
            role = "peer"
        machine = payload.get("machine")
        if not isinstance(machine, str) or not machine:
            machine = instance
        gate_state = payload.get("gate_state", "open")
        if not isinstance(gate_state, str) or not gate_state:
            gate_state = "open"
        worktrees = payload.get("worktrees")
        if not isinstance(worktrees, list) or not all(isinstance(w, str) for w in worktrees):
            worktrees = []
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(c, str) for c in capabilities
        ):
            capabilities = []
        agent_versions = payload.get("agent_versions")
        if not isinstance(agent_versions, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in agent_versions.items()
        ):
            agent_versions = {}
        status = payload.get("status")
        if not isinstance(status, dict):
            status = {}
        now = time.time()
        return {
            "instance": instance,
            "role": role,
            "epoch": epoch,
            "machine": machine,
            "worktrees": worktrees,
            "capabilities": capabilities,
            "gate_state": gate_state,
            "agent_versions": agent_versions,
            "status": status,
            "registered_at": registered_at,
            "last_seen": last_seen,
            "expires_at": last_seen + self._ttl,
            "age": max(0.0, now - last_seen),
        }

    def _is_live(self, entry: dict) -> bool:
        return (entry["last_seen"] + self._ttl) > time.time()

    def _owns(self, tunnel: dict) -> bool:
        """Whether ``tunnel`` carries this backend's label.

        Guards every mutating call (update/delete) against a deterministic
        tunnel-id collision with a tunnel some *other* tool or federation
        label created: this backend must never overwrite or delete a tunnel
        it doesn't own, even if the id happens to match.
        """
        labels = tunnel.get("labels")
        return isinstance(labels, list) and self._label in labels

    def _upsert(
        self,
        instance: str,
        *,
        role: str,
        epoch: int,
        machine: str | None,
        gate_state: str,
        registered_at: float,
        worktrees: list[str] | None = None,
        capabilities: list[str] | None = None,
        agent_versions: dict[str, str] | None = None,
        status: dict | None = None,
    ) -> dict:
        tunnel_id = _tunnel_id(instance)
        existing = self._call(["show", tunnel_id], allow_missing=True)
        existing_tunnel = (existing or {}).get("tunnel")
        if existing_tunnel and not self._owns(existing_tunnel):
            raise DevTunnelError(
                f"tunnel {tunnel_id!r} exists but is not labeled {self._label!r} -- "
                "refusing to overwrite a tunnel this backend doesn't own"
            )
        payload = {
            "instance": instance,
            "role": role,
            "epoch": int(epoch),
            "machine": machine or instance,
            "gate_state": gate_state,
            "worktrees": list(worktrees) if worktrees else [],
            "capabilities": list(capabilities) if capabilities else [],
            "agent_versions": dict(agent_versions) if agent_versions else {},
            "status": dict(status) if status else {},
            "registered_at": registered_at,
            "last_seen": time.time(),
        }
        description = self._encode(payload)
        if existing_tunnel:
            result = self._call(
                [
                    "update",
                    tunnel_id,
                    "--description",
                    description,
                    "--expiration",
                    self._expiration,
                ]
            )
        else:
            result = self._call(
                [
                    "create",
                    tunnel_id,
                    "--labels",
                    self._label,
                    "--description",
                    description,
                    "--expiration",
                    self._expiration,
                ]
            )
        tunnel = (result or {}).get("tunnel") or {}
        return self._decode(tunnel) or {
            **payload,
            "expires_at": payload["last_seen"] + self._ttl,
            "age": 0.0,
        }

    # -- Rendezvous Protocol ------------------------------------------------

    def register(
        self,
        instance: str,
        *,
        role: str = "peer",
        epoch: int = 0,
        machine: str | None = None,
        worktrees: list[str] | None = None,
        capabilities: list[str] | None = None,
        gate_state: str = "open",
        agent_versions: dict[str, str] | None = None,
        status: dict | None = None,
    ) -> dict:
        tunnel_id = _tunnel_id(instance)
        existing = self._call(["show", tunnel_id], allow_missing=True)
        registered_at = time.time()
        if existing and existing.get("tunnel"):
            prior = self._decode(existing["tunnel"])
            if prior:
                registered_at = prior["registered_at"]
        return self._upsert(
            instance,
            role=role,
            epoch=epoch,
            machine=machine,
            gate_state=gate_state,
            registered_at=registered_at,
            worktrees=worktrees,
            capabilities=capabilities,
            agent_versions=agent_versions,
            status=status,
        )

    def heartbeat(
        self,
        instance: str,
        *,
        status: dict | None = None,
        worktrees: list[str] | None = None,
        gate_state: str | None = None,
        role: str | None = None,
        epoch: int | None = None,
    ) -> dict:
        tunnel_id = _tunnel_id(instance)
        existing = self._call(["show", tunnel_id], allow_missing=True)
        if not existing or not existing.get("tunnel"):
            raise UnknownInstance(instance)
        prior = self._decode(existing["tunnel"])
        if prior is None:
            raise UnknownInstance(instance)
        return self._upsert(
            instance,
            role=role if role is not None else prior["role"],
            epoch=epoch if epoch is not None else prior["epoch"],
            machine=prior["machine"],
            gate_state=gate_state if gate_state is not None else prior["gate_state"],
            registered_at=prior["registered_at"],
            worktrees=worktrees if worktrees is not None else prior["worktrees"],
            capabilities=prior["capabilities"],
            agent_versions=prior["agent_versions"],
            status=status if status is not None else prior["status"],
        )

    def deregister(self, instance: str) -> bool:
        tunnel_id = _tunnel_id(instance)
        existing = self._call(["show", tunnel_id], allow_missing=True)
        existing_tunnel = (existing or {}).get("tunnel")
        if not existing_tunnel:
            return False
        if not self._owns(existing_tunnel):
            raise DevTunnelError(
                f"tunnel {tunnel_id!r} exists but is not labeled {self._label!r} -- "
                "refusing to delete a tunnel this backend doesn't own"
            )
        self._call(["delete", tunnel_id])
        return True

    def discover_peers(self, *, role: str | None = None) -> list[dict]:
        tunnels = self._list_tunnels()
        return self._decode_live(tunnels, role=role)

    def discover_coordinator(self) -> dict | None:
        """The claim-plane read -- **never** answers from stale/cached data,
        while still respecting the same backoff timer as
        :meth:`discover_peers` so a persistent failure doesn't re-invoke the
        CLI on every federation tick.

        This feeds :class:`~agent_dispatch.lease.CoordinatorLease`, which
        takes ``discover_coordinator() is None`` as license to take over the
        role. A backed-off node's *own* enumeration outage says nothing about
        whether the real coordinator is actually gone -- serving a stale/
        cached "no coordinator" answer here would let a standby that merely
        can't currently list tunnels register itself as coordinator anyway
        (registration is a separate, unbacked-off call), producing two
        simultaneous coordinators the directory would never have allowed a
        live enumeration to see.

        So while backed off, this raises :class:`DevTunnelError` **without**
        even attempting the CLI call (fail-closed *and* rate-limited); once
        the backoff window elapses it attempts one live enumeration exactly
        like :meth:`discover_peers` would, and raises on failure (extending
        the backoff) or returns fresh data on success. Either way the lease's
        ``tick()`` sees a raised exception rather than a fabricated "no
        coordinator" reply, and aborts that cycle (fail-closed) exactly as it
        did before backoff existed.
        """
        now = self._clock()
        if now < self._enum_backoff_until:
            raise DevTunnelError(
                "enumeration is currently backed off after a prior failure -- "
                "refusing to answer coordinator discovery from stale/cached data"
            )
        tunnels = self._list_tunnels_fresh()
        coordinators = self._decode_live(tunnels, role="coordinator")
        if not coordinators:
            return None
        return max(coordinators, key=lambda e: (e["epoch"], e["instance"]))

    def _decode_live(self, tunnels: list[dict], *, role: str | None = None) -> list[dict]:
        entries = []
        for tunnel in tunnels:
            entry = self._decode(tunnel)
            if entry is not None and self._is_live(entry):
                entries.append(entry)
        if role is not None:
            entries = [e for e in entries if e["role"] == role]
        return sorted(entries, key=lambda e: e["instance"])

    # -- enumeration backoff -------------------------------------------------

    def _list_tunnels(self) -> list[dict]:
        """``devtunnel list --all-labels <label>``, serving the last
        successfully enumerated snapshot while backed off.

        A federation tick calls this every interval; a persistent enumeration
        failure (most commonly a lapsed ``dtssh login``) must not re-invoke
        the CLI at that same rate indefinitely **for the awareness plane**
        (:meth:`discover_peers`). While backed off, this returns the cached
        snapshot (degrading to an already-known, possibly-stale awareness
        picture) instead of hammering the management API; the backoff clears
        -- and a fresh enumeration is attempted -- as soon as it elapses.
        Never used by the claim plane (:meth:`discover_coordinator`) or
        maintenance (:meth:`reap_stale`), which both need a guaranteed-fresh
        answer -- see :meth:`_list_tunnels_fresh`.
        """
        now = self._clock()
        if now < self._enum_backoff_until:
            return self._enum_cache
        return self._list_tunnels_fresh()

    def _list_tunnels_fresh(self) -> list[dict]:
        """Always attempts a live ``devtunnel list --all-labels <label>``,
        ignoring any active backoff window -- the *first* failure still
        propagates (via :class:`DevTunnelError`) so a caller sees it at least
        once rather than the backoff silently swallowing it forever.

        Used by :meth:`reap_stale` (an explicit, infrequent maintenance
        action where deciding what to delete from a stale snapshot is itself
        a correctness bug -- a tunnel that looked dead when last cached but
        has since refreshed must not be deleted) and, after
        :meth:`discover_coordinator` has already checked the backoff timer
        itself, by the claim plane.
        """
        now = self._clock()
        try:
            result = self._call(["list", "--all-labels", self._label])
        except DevTunnelError:
            self._enum_backoff_seconds = min(
                self._enum_backoff_seconds * 2 if self._enum_backoff_seconds else (
                    DEFAULT_ENUM_BACKOFF_SECONDS
                ),
                MAX_ENUM_BACKOFF_SECONDS,
            )
            self._enum_backoff_until = now + self._enum_backoff_seconds
            raise
        self._enum_backoff_seconds = 0.0
        self._enum_backoff_until = 0.0
        self._enum_cache = (result or {}).get("tunnels") or []
        return self._enum_cache

    # -- maintenance (explicit opt-in, not called by the Protocol) ----------

    def reap_stale(self) -> int:
        """Delete every managed tunnel whose entry is no longer live.

        Not part of the :class:`~agent_dispatch.federation.Rendezvous`
        Protocol -- an operator/cron convenience so a permanently-dead
        instance's tunnel doesn't linger for its full ``--expiration``.
        Always enumerates fresh (:meth:`_list_tunnels_fresh`, ignoring any
        backoff window) rather than deciding what to delete from a
        potentially-stale cached snapshot -- a tunnel this backend hasn't
        re-listed since backoff kicked in could have been refreshed in the
        meantime, and deleting a now-live instance's tunnel out from under it
        would be a real, unrelated correctness bug, not a mere efficiency
        trade-off.
        """
        tunnels = self._list_tunnels_fresh()
        removed = 0
        for tunnel in tunnels:
            entry = self._decode(tunnel)
            if entry is not None and not self._is_live(entry):
                self._call(["delete", tunnel["tunnelId"]])
                removed += 1
        return removed


def _looks_like_not_found(detail: str) -> bool:
    lowered = detail.lower()
    return "not found" in lowered or "does not exist" in lowered or "404" in lowered


def devtunnel_rendezvous() -> DevTunnelRendezvous | None:
    """The Dev Tunnels rendezvous, or ``None`` when this backend isn't selected.

    Selected via ``AGENT_DISPATCH_FEDERATION_BACKEND=devtunnels`` (the Gateway
    backend, :func:`agent_dispatch.federation_runner.hosted_rendezvous`, remains
    the default so existing Phase-3 deployments are unaffected)."""
    from . import config

    if config.federation_backend() != "devtunnels":
        return None
    return DevTunnelRendezvous(
        label=config.federation_devtunnel_label(),
    )
