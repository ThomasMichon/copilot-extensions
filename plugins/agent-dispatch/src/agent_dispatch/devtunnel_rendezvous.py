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
    ) -> None:
        self._binary = binary or _default_binary()
        self._label = label
        self._expiration = expiration
        self._ttl = float(ttl_seconds)
        self._timeout = timeout
        self._runner = runner or self._spawn

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
        raw = tunnel.get("description") or ""
        if not raw:
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
        now = time.time()
        return {
            "instance": instance,
            "role": role,
            "epoch": epoch,
            "machine": machine,
            "worktrees": [],
            "capabilities": [],
            "gate_state": gate_state,
            "agent_versions": {},
            "status": {},
            "registered_at": registered_at,
            "last_seen": last_seen,
            "expires_at": last_seen + self._ttl,
            "age": max(0.0, now - last_seen),
        }

    def _is_live(self, entry: dict) -> bool:
        return (entry["last_seen"] + self._ttl) > time.time()

    def _upsert(
        self,
        instance: str,
        *,
        role: str,
        epoch: int,
        machine: str | None,
        gate_state: str,
        registered_at: float,
    ) -> dict:
        tunnel_id = _tunnel_id(instance)
        existing = self._call(["show", tunnel_id], allow_missing=True)
        payload = {
            "instance": instance,
            "role": role,
            "epoch": int(epoch),
            "machine": machine or instance,
            "gate_state": gate_state,
            "registered_at": registered_at,
            "last_seen": time.time(),
        }
        description = self._encode(payload)
        if existing and existing.get("tunnel"):
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
        return self._decode(tunnel) or {**payload, "worktrees": [], "capabilities": [],
                                        "agent_versions": {}, "status": {},
                                        "expires_at": payload["last_seen"] + self._ttl, "age": 0.0}

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
        # worktrees/capabilities/agent_versions/status are intentionally not
        # carried over this transport -- see module docstring on tunnel
        # description size; the awareness plane still reports role/epoch/
        # machine/gate_state/last-beat, which is the Phase-4 minimum bar.
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
        )

    def deregister(self, instance: str) -> bool:
        tunnel_id = _tunnel_id(instance)
        existing = self._call(["show", tunnel_id], allow_missing=True)
        if not existing or not existing.get("tunnel"):
            return False
        self._call(["delete", tunnel_id])
        return True

    def discover_peers(self, *, role: str | None = None) -> list[dict]:
        result = self._call(["list", "--all-labels", self._label])
        tunnels = (result or {}).get("tunnels") or []
        entries = []
        for tunnel in tunnels:
            entry = self._decode(tunnel)
            if entry is not None and self._is_live(entry):
                entries.append(entry)
        if role is not None:
            entries = [e for e in entries if e["role"] == role]
        return sorted(entries, key=lambda e: e["instance"])

    def discover_coordinator(self) -> dict | None:
        coordinators = self.discover_peers(role="coordinator")
        if not coordinators:
            return None
        return max(coordinators, key=lambda e: (e["epoch"], e["instance"]))

    # -- maintenance (explicit opt-in, not called by the Protocol) ----------

    def reap_stale(self) -> int:
        """Delete every managed tunnel whose entry is no longer live.

        Not part of the :class:`~agent_dispatch.federation.Rendezvous`
        Protocol -- an operator/cron convenience so a permanently-dead
        instance's tunnel doesn't linger for its full ``--expiration``.
        """
        result = self._call(["list", "--all-labels", self._label])
        tunnels = (result or {}).get("tunnels") or []
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
