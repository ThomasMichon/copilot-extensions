"""Coordinator-hosted HTTP MCP endpoint.

A nearly 1:1 MCP surface **served by the coordinator itself** (mounted at
``/mcp``), so any MCP client that can reach the coordinator over HTTP -- e.g. an
``agent-mcp`` bridge on another host -- gets the dispatch tools without a local
``agent-dispatch`` install. It complements the *local* stdio shim
(:mod:`agent_dispatch.mcp_server`): the stdio shim resolves identity from the
caller's CWD, whereas this server-side surface takes the caller's
``machine``/``worktree`` identity from a **request header**
(``X-Agent-Machine`` / ``X-Agent-Worktree``) or an **explicit tool argument**.

Tools operate directly on the :class:`~agent_dispatch.queue.TaskQueue` and
publish the same ``task.*`` events to the coordinator's :class:`EventBus` as the
REST routes, so subscribers see MCP-driven changes identically.

Requires the optional ``mcp`` extra; the coordinator mounts this only when it is
importable (otherwise the REST API still serves).
"""

# NOTE: no ``from __future__ import annotations`` here on purpose -- FastMCP
# evaluates each tool's annotations in the function's *module* globals, which
# can't see ``Context`` imported locally inside ``build_coordinator_mcp``. Real
# (non-stringized) annotations resolve at def-time via the enclosing scope.

from dataclasses import asdict
from typing import Any

from .events import EventBus
from .queue import TaskError, TaskQueue, worker_id_for

MACHINE_HEADER = "x-agent-machine"
WORKTREE_HEADER = "x-agent-worktree"


def _headers_of(ctx: Any) -> dict[str, str]:
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    return dict(req.headers) if req is not None else {}


def build_coordinator_mcp(queue: TaskQueue, bus: EventBus) -> Any:
    """Build the FastMCP server the coordinator mounts at ``/mcp``.

    Raises ``RuntimeError`` (via import failure) if the ``mcp`` extra is absent;
    the caller treats that as "don't mount the MCP endpoint".
    """
    from mcp.server.fastmcp import Context, FastMCP

    # streamable_http_path="/" so mounting the app at "/mcp" yields the endpoint
    # at "/mcp" (not "/mcp/mcp").
    mcp = FastMCP("agent-dispatch-coordinator", stateless_http=True, streamable_http_path="/")

    def _emit(event_type: str, task: dict) -> None:
        bus.publish({"type": event_type, "task": task})

    def _mutate(op, event_type: str | None) -> dict:
        """Run a queue mutation; map TaskError to an error dict; emit on success."""
        try:
            result = asdict(op())
        except TaskError as exc:
            return {"error": str(exc)}
        if event_type is not None:
            _emit(event_type, result)
        return result

    def _identity(
        ctx: Context, machine: str | None, worktree: str | None
    ) -> tuple[str | None, str | None]:
        if machine and worktree:
            return machine, worktree
        h = _headers_of(ctx)
        return (machine or h.get(MACHINE_HEADER), worktree or h.get(WORKTREE_HEADER))

    # -- producers -----------------------------------------------------------

    @mcp.tool(name="dispatch_create")
    def create(
        title: str,
        prompt: str = "",
        payload: str | None = None,
        payload_ref: str | None = None,
        requires: list[str] | None = None,
        affinity: dict[str, str] | None = None,
        labels: list[str] | None = None,
        target_machine: str | None = None,
        target_worktree: str | None = None,
        target_repo: str | None = None,
        dedup_key: str | None = None,
        not_before: float = 0.0,
        proposed: bool = False,
    ) -> dict:
        """Enqueue a task (``proposed=True`` for an unclaimable draft).

        ``payload`` is inline Markdown; a large one spills to a
        content-addressed blob. ``find`` before ``create`` to avoid duplicates.
        """
        make = queue.propose if proposed else queue.create
        task = make(
            title,
            prompt=prompt,
            payload_inline=payload,
            payload_ref=payload_ref,
            requires=requires or [],
            affinity=affinity or {},
            labels=labels or [],
            target_machine=target_machine,
            target_worktree=target_worktree,
            target_repo=target_repo,
            dedup_key=dedup_key,
            not_before=not_before,
        )
        result = asdict(task)
        _emit("task.proposed" if proposed else "task.created", result)
        return result

    @mcp.tool(name="dispatch_approve")
    def approve(task_id: str) -> dict:
        """Move a ``proposed`` task to ``queued`` (makes it claimable)."""
        return _mutate(lambda: queue.approve(task_id), "task.approved")

    # -- browse --------------------------------------------------------------

    @mcp.tool(name="dispatch_find")
    def find(query: str, limit: int = 50) -> list[dict]:
        """Substring-search task titles/prompts -- run before ``create`` to dedup."""
        return [asdict(t) for t in queue.find(query, limit=limit)]

    @mcp.tool(name="dispatch_list")
    def list_tasks(
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """List tasks, optionally filtered by status/machine/repo/label."""
        return [
            asdict(t)
            for t in queue.list(
                status=status,
                target_machine=target_machine,
                target_repo=target_repo,
                label=label,
                limit=limit,
            )
        ]

    @mcp.tool(name="dispatch_show")
    def show(task_id: str) -> dict:
        """Return one task's full record."""
        task = queue.get(task_id)
        return asdict(task) if task else {"error": f"no such task {task_id!r}"}

    @mcp.tool(name="dispatch_events")
    def events(task_id: str) -> list[dict]:
        """Return a task's append-only audit trail."""
        return queue.events(task_id)

    @mcp.tool(name="dispatch_payload")
    def payload(task_id: str) -> dict:
        """Return a task's resolved payload (inline text or blob content)."""
        task = queue.get(task_id)
        if task is None:
            return {"error": f"no such task {task_id!r}"}
        return {
            "task_id": task.id,
            "ref": task.payload_ref,
            "inline": task.payload_inline is not None,
            "payload": queue.read_payload(task),
        }

    # -- identity-bearing ----------------------------------------------------

    @mcp.tool(name="dispatch_worktree_status")
    def worktree_status(
        ctx: Context, machine: str | None = None, worktree: str | None = None
    ) -> dict:
        """This worktree's inbox: tasks targeted at + owned by its identity.

        Identity comes from ``X-Agent-Machine``/``X-Agent-Worktree`` headers
        unless the ``machine``/``worktree`` arguments override them.
        """
        machine, worktree = _identity(ctx, machine, worktree)
        if not machine or not worktree:
            return {"error": "no identity: send X-Agent-Machine/X-Agent-Worktree or pass args"}
        inbox = queue.mine(machine, worktree)
        return {
            "machine": machine,
            "worktree": worktree,
            **{k: [asdict(t) for t in v] for k, v in inbox.items()},
        }

    @mcp.tool(name="dispatch_claim")
    def claim(
        ctx: Context,
        capabilities: list[str] | None = None,
        task_id: str | None = None,
        lease_seconds: int | None = None,
        machine: str | None = None,
        worktree: str | None = None,
    ) -> dict | None:
        """Atomically lease one eligible task (identity via header or args).

        The claim honors targeting: only untargeted tasks or tasks targeted at
        this identity are eligible. Returns the claimed task, or ``None``.
        """
        machine, worktree = _identity(ctx, machine, worktree)
        if not machine or not worktree:
            return {"error": "no identity: send X-Agent-Machine/X-Agent-Worktree or pass args"}
        task = queue.claim_one(
            worker_id_for(machine, worktree),
            capabilities or [],
            machine=machine,
            worktree=worktree,
            task_id=task_id,
            lease_seconds=lease_seconds,
        )
        if task is None:
            return None
        result = asdict(task)
        _emit("task.claimed", result)
        return result

    # -- lifecycle -----------------------------------------------------------

    @mcp.tool(name="dispatch_start")
    def start(task_id: str, worker_id: str) -> dict:
        """Mark a claimed task ``started``."""
        return _mutate(lambda: queue.start(task_id, worker_id), "task.started")

    @mcp.tool(name="dispatch_yield")
    def yield_task(task_id: str, worker_id: str, note: str | None = None) -> dict:
        """Return a held task to ``queued`` with a note (recoverable snag)."""
        return _mutate(lambda: queue.yield_task(task_id, worker_id, note=note), "task.yielded")

    @mcp.tool(name="dispatch_complete")
    def complete(task_id: str, worker_id: str, result_ref: str | None = None) -> dict:
        """Mark a started task ``completed``."""
        return _mutate(
            lambda: queue.complete(task_id, worker_id, result_ref=result_ref), "task.completed"
        )

    @mcp.tool(name="dispatch_abandon")
    def abandon(
        task_id: str, worker_id: str | None = None, permit: bool = False, reason: str | None = None
    ) -> dict:
        """Terminally abandon a task -- requires ``permit=True`` (permission-gated)."""
        return _mutate(
            lambda: queue.abandon(task_id, worker_id=worker_id, permitted=permit, reason=reason),
            "task.abandoned",
        )

    @mcp.tool(name="dispatch_heartbeat")
    def heartbeat(task_id: str, worker_id: str) -> dict:
        """Extend the lease on a held task during long work."""
        return _mutate(lambda: queue.heartbeat(task_id, worker_id), None)

    @mcp.tool(name="dispatch_detach")
    def detach(task_id: str) -> dict:
        """Demote a hard worktree pin to a soft affinity (portability)."""
        return _mutate(lambda: queue.detach(task_id), "task.detached")

    @mcp.tool(name="dispatch_recover")
    def recover() -> dict:
        """Force a lease-recovery sweep (requeue expired-lease tasks)."""
        return {"recovered": queue.recover_expired_leases()}

    return mcp


def bearer_guard_middleware(token: str):
    """A Starlette middleware factory that 401s the MCP mount without the token."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _Guard(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse({"detail": "invalid or missing bearer token"}, status_code=401)
            return await call_next(request)

    return _Guard


# re-exported for the coordinator without importing mcp at module load
__all__ = ["bearer_guard_middleware", "build_coordinator_mcp"]
