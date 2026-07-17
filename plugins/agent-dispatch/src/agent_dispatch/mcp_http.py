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
from .identity import canonicalize_remote
from .queue import TaskError, TaskQueue, worker_id_for

MACHINE_HEADER = "x-agent-machine"
WORKTREE_HEADER = "x-agent-worktree"
REPO_HEADER = "x-agent-repo"


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

    def _repo(ctx: Context, repo: str | None) -> str | None:
        """Resolve the lane key from an explicit arg or the ``X-Agent-Repo``
        header, canonicalized. Server-side we do *not* map local names (the
        caller's registry lives on its own device), so the caller sends a
        remote URL (the agent-mcp bridge injects the header, like identity)."""
        raw = repo or _headers_of(ctx).get(REPO_HEADER)
        return canonicalize_remote(raw)

    # -- producers -----------------------------------------------------------

    @mcp.tool(name="dispatch_create")
    def create(
        ctx: Context,
        title: str,
        repo: str | None = None,
        prompt: str = "",
        payload: str | None = None,
        payload_ref: str | None = None,
        requires: list[str] | None = None,
        excludes: list[str] | None = None,
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

        ``repo`` is the **lane** (a remote URL, or the ``X-Agent-Repo`` header);
        it is required -- tasks stay in their producing repo's lane. ``payload``
        is inline Markdown; a large one spills to a content-addressed blob.
        ``sweep``/``find`` before ``create`` to avoid duplicates.
        """
        lane = _repo(ctx, repo)
        if not lane:
            return {"error": "no repo (lane): send X-Agent-Repo or pass repo=<remote URL>"}
        make = queue.propose if proposed else queue.create
        task = make(
            title,
            repo=lane,
            prompt=prompt,
            payload_inline=payload,
            payload_ref=payload_ref,
            requires=requires or [],
            excludes=excludes or [],
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
    def find(ctx: Context, query: str, limit: int = 50, repo: str | None = None) -> list[dict]:
        """Substring-search task titles/prompts in the lane -- a quick dedup probe."""
        return [asdict(t) for t in queue.find(query, repo=_repo(ctx, repo), limit=limit)]

    @mcp.tool(name="dispatch_sweep")
    def sweep(ctx: Context, limit: int = 500, repo: str | None = None) -> list[dict]:
        """The dedup corpus for the lane: every non-abandoned task, newest first."""
        return [asdict(t) for t in queue.sweep(repo=_repo(ctx, repo), limit=limit)]

    @mcp.tool(name="dispatch_list")
    def list_tasks(
        ctx: Context,
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        limit: int = 200,
        repo: str | None = None,
    ) -> list[dict]:
        """List tasks in the lane, optionally filtered by status/machine/repo/label."""
        return [
            asdict(t)
            for t in queue.list(
                repo=_repo(ctx, repo),
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
        ctx: Context,
        machine: str | None = None,
        worktree: str | None = None,
        repo: str | None = None,
    ) -> dict:
        """This worktree's inbox: tasks targeted at + owned by its identity.

        Identity comes from ``X-Agent-Machine``/``X-Agent-Worktree`` headers and
        the lane from ``X-Agent-Repo`` unless the arguments override them.
        """
        machine, worktree = _identity(ctx, machine, worktree)
        if not machine or not worktree:
            return {"error": "no identity: send X-Agent-Machine/X-Agent-Worktree or pass args"}
        lane = _repo(ctx, repo)
        inbox = queue.mine(machine, worktree, repo=lane)
        return {
            "machine": machine,
            "worktree": worktree,
            "repo": lane,
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
        repo: str | None = None,
    ) -> dict | None:
        """Atomically lease one eligible task (identity + lane via header or args).

        The claim honors the repo lane and targeting: only tasks in this repo's
        lane that are untargeted or targeted at this identity are eligible.
        Returns the claimed task, or ``None``.
        """
        machine, worktree = _identity(ctx, machine, worktree)
        if not machine or not worktree:
            return {"error": "no identity: send X-Agent-Machine/X-Agent-Worktree or pass args"}
        task = queue.claim_one(
            worker_id_for(machine, worktree),
            capabilities or [],
            repo=_repo(ctx, repo),
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
    """A Starlette middleware factory that 401s the MCP mount without the token.

    Mirrors the coordinator's HTTP-API loopback exemption: a loopback peer (this
    host's own processes) is trusted and skips the bearer, while any non-loopback
    caller (a container via the docker bridge, or the LAN) must present it. See
    ``coordinator.is_loopback_client`` for why a loopback peer IP is trustworthy.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    from .coordinator import is_loopback_client

    class _Guard(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            client = request.client
            if client is not None and is_loopback_client(client.host):
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse({"detail": "invalid or missing bearer token"}, status_code=401)
            return await call_next(request)

    return _Guard


# re-exported for the coordinator without importing mcp at module load
__all__ = ["bearer_guard_middleware", "build_coordinator_mcp"]
