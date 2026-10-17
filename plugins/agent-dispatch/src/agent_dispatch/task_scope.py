"""Task-scoped capability tokens -- the authorization boundary a **projected
relay** needs (``agent-dispatch-fleet-authority`` effort, Phase 1,
https://github.com/ThomasMichon/copilot-extensions/issues/6617).

**The gap this closes:** every task-lifecycle HTTP route
(:mod:`agent_dispatch.coordinator_tasks`) takes ``worker_id`` /
``owner_session_id`` straight out of the caller-supplied JSON body. The only
gate is a single shared bearer token (:func:`agent_dispatch.coordinator_auth
._make_auth`) -- any holder of that token can assert **any** ``worker_id``
against **any** task. A task-scoped token binds a caller's credential to one
exact ``(task_id, worker_id, owner_session_id)`` triple, so a projected relay
can hand a containerized/identity-managed agent a credential that is
authorization-bound to *its own task only*, never the full shared bearer.

**Design: no new durable state.** The token is an HMAC derived from the
coordinator's ``control_token`` (mirroring the existing
:func:`agent_dispatch.coordinator_auth.scoped_control_token` pattern) over the
claims below. Verification recomputes the HMAC and then the caller re-checks
the claims against the task's **current** DB state (see
:mod:`agent_dispatch.task_authority`'s ``resolve_task_authority``) -- so a
token naturally and immediately invalidates the instant the task's owner,
owner_session_id, or fencing epoch changes (reassignment, completion, a
coordinator takeover), with no revocation list to maintain.

Carries the fencing epoch (see :mod:`agent_dispatch.lease`'s
``FencingGuard``) at mint time, so a token minted by a since-superseded
coordinator is rejected the same way a stale coordinator's own writes are --
this is the one piece of the fencing guard this module wires through for
free; the server-side epoch comparison itself still happens in the caller
(``FencingGuard.is_valid``), not here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass

#: The header a caller presents a task-scoped token through -- distinct from
#: the ordinary ``Authorization: Bearer`` header the ambient shared/control
#: token uses, so a route can tell the two credential shapes apart and a
#: projected relay never needs to construct an ``Authorization`` header at
#: all for the narrower credential it hands to a container.
TASK_SCOPE_HEADER = "X-Agent-Dispatch-Task-Token"

#: Default token lifetime. Short-lived by design -- the real invalidation
#: mechanism is the task-state recheck (owner/owner_session_id/epoch), not
#: this expiry; the expiry is only a defense-in-depth bound on exposure
#: window (e.g. a task somehow re-opened with a coincidentally-reused
#: owner_session_id). A long-running container's supervisor is expected to
#: renew through its own trusted `task-grant` call well within this
#: window, not treat one token as good for a whole task's lifetime.
DEFAULT_TTL_SECONDS = 900.0

_VERSION = "v1"


class TaskScopeTokenError(Exception):
    """A task-scoped token is malformed, has an invalid signature, or has
    expired. Callers map this to an HTTP 401/403, never to a 500 -- an
    invalid credential is an ordinary authorization outcome, not a server
    error."""


@dataclass(frozen=True)
class TaskScopeClaims:
    """The verified claims carried by a task-scoped token.

    ``epoch`` is the coordinator's fencing epoch (see
    :func:`agent_dispatch.lease.current_fencing_epoch`) at mint time; ``exp``
    is a Unix timestamp defense-in-depth expiry independent of task-state
    staleness.
    """

    task_id: str
    worker_id: str
    owner_session_id: str
    epoch: int
    exp: float
    generation: int = 0


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.b64decode(data + padding, altchars=b"-_", validate=True)


def _sign(control_token: str, payload: bytes) -> str:
    return hmac.new(
        control_token.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def mint_task_scope_token(
    control_token: str,
    *,
    task_id: str,
    worker_id: str,
    owner_session_id: str,
    epoch: int,
    generation: int = 0,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Mint a task-scoped capability token for ``(task_id, worker_id,
    owner_session_id)`` at the given fencing ``epoch``.

    No durable row is created -- the token is self-contained (claims +
    HMAC signature); the caller is responsible for re-checking the claims
    against the task's current DB state on every use (see
    :mod:`agent_dispatch.task_authority`).
    """
    ts = now if now is not None else time.time()
    if not control_token or not all(
        isinstance(value, str) and value
        for value in (task_id, worker_id, owner_session_id)
    ):
        raise ValueError("task token requires a signing key and non-empty identities")
    if (
        type(epoch) is not int or epoch < 0
        or type(generation) is not int or generation < 0
        or not math.isfinite(ts) or not math.isfinite(ttl_seconds)
        or ttl_seconds <= 0
    ):
        raise ValueError("task token requires non-negative fences and finite positive lifetime")
    claims = {
        "v": _VERSION,
        "task_id": task_id,
        "worker_id": worker_id,
        "owner_session_id": owner_session_id,
        "epoch": int(epoch),
        "generation": generation,
        "exp": ts + max(0.0, ttl_seconds),
    }
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = _sign(control_token, payload)
    return f"{_b64encode(payload)}.{signature}"


def verify_task_scope_token(
    control_token: str, token: str, *, now: float | None = None
) -> TaskScopeClaims:
    """Verify ``token``'s signature and expiry, returning its claims.

    Raises :class:`TaskScopeTokenError` on any malformed, forged, or expired
    token. Does **not** check the claims against current task state -- that
    is the caller's job (the whole point of this module's no-revocation-list
    design: staleness is detected by the caller's own DB read, not here).
    """
    try:
        if len(token) > 8192:
            raise ValueError("token exceeds size bound")
        encoded_payload, signature = token.rsplit(".", 1)
        payload = _b64decode(encoded_payload)
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise TaskScopeTokenError("malformed task-scoped token") from exc
    expected = _sign(control_token, payload)
    if not signature.isascii() or not hmac.compare_digest(signature, expected):
        raise TaskScopeTokenError("invalid task-scoped token signature")
    try:
        claims = json.loads(payload)
    except ValueError as exc:
        raise TaskScopeTokenError("malformed task-scoped token payload") from exc
    if not isinstance(claims, dict) or claims.get("v") != _VERSION:
        raise TaskScopeTokenError("unsupported task-scoped token version")
    try:
        task_id = claims["task_id"]
        worker_id = claims["worker_id"]
        owner_session_id = claims["owner_session_id"]
        epoch = claims["epoch"]
        generation = claims["generation"]
        exp = claims["exp"]
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskScopeTokenError("malformed task-scoped token claims") from exc
    ts = now if now is not None else time.time()
    if (
        not all(isinstance(value, str) and value for value in
                (task_id, worker_id, owner_session_id))
        or type(epoch) is not int or epoch < 0
        or type(generation) is not int or generation < 0
        or type(exp) not in (int, float) or not math.isfinite(exp)
        or not math.isfinite(ts)
    ):
        raise TaskScopeTokenError("malformed task-scoped token claims")
    if ts >= exp:
        raise TaskScopeTokenError("expired task-scoped token")
    return TaskScopeClaims(
        task_id=task_id,
        worker_id=worker_id,
        owner_session_id=owner_session_id,
        epoch=epoch,
        exp=exp,
        generation=generation,
    )
