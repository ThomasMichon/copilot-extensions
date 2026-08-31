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

# NOTE: no ``from __future__ import annotations`` here on purpose -- MCPServer
# evaluates each tool's annotations in the function's *module* globals, which
# can't see ``Context`` imported locally inside ``build_coordinator_mcp``. Real
# (non-stringized) annotations resolve at def-time via the enclosing scope.

import json
from dataclasses import asdict
from typing import Annotated, Any

from pydantic import PlainValidator, WithJsonSchema

from . import telemetry
from .events import EventBus
from .identity import canonicalize_remote
from .queue import (
    CompletionOutcome,
    StructuredResult,
    Task,
    TaskError,
    TaskQueue,
    worker_id_for,
)

MACHINE_HEADER = "x-agent-machine"
WORKTREE_HEADER = "x-agent-worktree"
REPO_HEADER = "x-agent-repo"


def _validate_mcp_structured_result(value: Any) -> StructuredResult:
    if not isinstance(value, (dict, list)):
        raise ValueError("result must be a JSON object or array")
    return value


McpStructuredResult = Annotated[
    StructuredResult,
    PlainValidator(_validate_mcp_structured_result),
    WithJsonSchema({"anyOf": [{"type": "object"}, {"type": "array"}]}),
]


def _headers_of(ctx: Any) -> dict[str, str]:
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    return dict(req.headers) if req is not None else {}


def _bulk_task_dict(task: Task) -> dict:
    result = asdict(task)
    result.pop("result")
    return result


def _event_task_dict(task: dict) -> dict:
    result = dict(task)
    result["has_result"] = result.pop("result", None) is not None or bool(
        result.get("has_result")
    )
    return result


def build_coordinator_mcp(queue: TaskQueue, bus: EventBus) -> Any:
    """Build the MCPServer the coordinator mounts at ``/mcp``.

    Raises ``RuntimeError`` (via import failure) if the ``mcp`` extra is absent;
    the caller treats that as "don't mount the MCP endpoint".

    Returns the ``MCPServer`` itself (not its ASGI app): mcp 2.0 moved the
    transport options onto ``streamable_http_app()`` and drives the streamable
    session manager via ``server.session_manager.run()``, both of which the
    coordinator needs off this object.
    """
    from mcp.server.mcpserver import Context, MCPServer

    # mcp 2.0: stateless/streamable-path options moved off the constructor onto
    # streamable_http_app() (see coordinator.create_app).
    mcp = MCPServer("agent-dispatch-coordinator")

    def _emit(event_type: str, task: dict) -> None:
        event_task = _event_task_dict(task)
        bus.publish({"type": event_type, "task": event_task})
        # Generic telemetry seam (no-op unless a consumer registered a sink).
        telemetry.emit(telemetry.task_lifecycle_event(event_type, event_task))

    def _mutate(op, event_type: str | None) -> dict:
        """Run a queue mutation; map TaskError to an error dict; emit on success."""
        try:
            mutation = op()
            if isinstance(mutation, CompletionOutcome):
                event_type = mutation.event_type
                mutation = mutation.task
            result = asdict(mutation)
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
        evaluator_ref: str | None = None,
        dedup_key: str | None = None,
        goal: str | None = None,
        done_criteria: str | None = None,
        not_before: float = 0.0,
        proposed: bool = False,
    ) -> dict:
        """Enqueue a task (``proposed=True`` for an unclaimable draft).

        ``repo`` is the **lane** (a remote URL, or the ``X-Agent-Repo`` header);
        it is required -- tasks stay in their producing repo's lane. ``payload``
        is inline Markdown; a large one spills to a content-addressed blob.
        ``sweep``/``find`` before ``create`` to avoid duplicates.

        ``goal`` + ``done_criteria`` make the task a **durable, resumable goal**
        (the *resumable-goal* feature): a worker loops toward ``goal`` and
        completes only once ``done_criteria`` are met, resuming from the
        accumulated progress log. Omit both for a plain one-shot task.
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
            evaluator_ref=evaluator_ref,
            dedup_key=dedup_key,
            goal=goal,
            done_criteria=done_criteria,
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
        return [
            _bulk_task_dict(t)
            for t in queue.find(query, repo=_repo(ctx, repo), limit=limit)
        ]

    @mcp.tool(name="dispatch_sweep")
    def sweep(ctx: Context, limit: int = 500, repo: str | None = None) -> list[dict]:
        """The dedup corpus for the lane: every non-abandoned task, newest first."""
        return [
            _bulk_task_dict(t)
            for t in queue.sweep(repo=_repo(ctx, repo), limit=limit)
        ]

    # -- recipes -------------------------------------------------------------

    @mcp.tool(name="dispatch_recipe_list")
    def recipe_list() -> list[dict]:
        """List the built-in loop recipes (reviewer / conflict-resolution /
        goal-driven) with their parameters, suspend-on events, and resolution."""
        from .recipes import list_recipes

        return [
            {
                "name": r.name,
                "summary": r.summary,
                "params": [
                    {
                        "name": p.name,
                        "required": p.required,
                        "default": p.default,
                        "description": p.description,
                    }
                    for p in r.params
                ],
                "suspend_on": list(r.suspend_on),
                "resolution": r.resolution,
            }
            for r in list_recipes()
        ]

    @mcp.tool(name="dispatch_recipe_render")
    def recipe_render(name: str, params: dict[str, str] | None = None) -> dict:
        """Render a recipe with parameters into the fields of a task (creates
        nothing) -- inspect what ``dispatch_recipe_kick`` would enqueue."""
        from .recipes import RecipeError, render_recipe

        try:
            return render_recipe(name, params or {}).to_dict()
        except RecipeError as exc:
            return {"error": str(exc)}

    @mcp.tool(name="dispatch_recipe_kick")
    def recipe_kick(
        ctx: Context,
        name: str,
        params: dict[str, str] | None = None,
        repo: str | None = None,
        dedup_key: str | None = None,
        labels: list[str] | None = None,
        target_machine: str | None = None,
        target_worktree: str | None = None,
        target_repo: str | None = None,
        proposed: bool = False,
    ) -> dict:
        """Carve an ad-hoc task from a recipe (the no-wrapper-service path).

        Renders the recipe and enqueues it, deriving a reserved-work
        ``dedup_key`` from the recipe + params so re-kicking the same target
        **collides rather than forking**. Extra ``labels`` are merged with the
        recipe's own (e.g. route the task onto a supervisor pool with
        ``["general"]``). Enqueues only; embodying a worker to drive the loop is
        the supervisor/host's job (as with ``create``)."""
        from .recipes import RecipeError, dedup_key_for, render_recipe

        try:
            rendered = render_recipe(name, params or {})
        except RecipeError as exc:
            return {"error": str(exc)}
        lane = _repo(ctx, repo)
        if not lane:
            return {"error": "no repo (lane): send X-Agent-Repo or pass repo=<remote URL>"}
        merged_labels = list(dict.fromkeys([*rendered.labels, *(labels or [])]))
        make = queue.propose if proposed else queue.create
        task = make(
            rendered.title,
            repo=lane,
            prompt=rendered.prompt,
            requires=list(rendered.requires),
            labels=merged_labels,
            dedup_key=dedup_key or dedup_key_for(rendered),
            goal=rendered.goal,
            done_criteria=rendered.done_criteria,
            source="recipe",
            origin_ref=rendered.recipe,
            target_machine=target_machine,
            target_worktree=target_worktree,
            target_repo=target_repo,
        )
        result = asdict(task)
        _emit("task.proposed" if proposed else "task.created", result)
        return result

    @mcp.tool(name="dispatch_emitter_side_load")
    def emitter_side_load(registration_id: str, change_ref: str) -> dict:
        """Run one registered emitter's on-demand path on the coordinator host."""
        from .producers.emitter import EmitterError, run_side_load
        from . import remote_dispatch

        registration = queue.get_registration(registration_id)
        if registration is None:
            return {"error": f"no such registration {registration_id!r}"}
        if remote_dispatch.is_peer_machine(registration.machine):
            try:
                completed = remote_dispatch.browse_remote(
                    registration.machine,
                    [
                        "agent-dispatch",
                        "emitter",
                        "side-load",
                        registration_id,
                        change_ref,
                        "--env",
                        registration.env,
                    ],
                    timeout=120,
                )
            except remote_dispatch.RemoteDispatchUnavailable as exc:
                return {"error": str(exc)}
            if completed.returncode != 0:
                return {
                    "error": remote_dispatch.diagnose_remote_failure(
                        registration.machine,
                        completed.returncode,
                        completed.stderr,
                    )
                }
            try:
                return json.loads(completed.stdout)
            except ValueError as exc:
                return {"error": f"remote side-load returned invalid JSON: {exc}"}

        class _QueueClient:
            def create(self, title: str, **fields: Any) -> dict:
                proposed = bool(fields.pop("proposed", False))
                task = (
                    queue.propose(title, **fields)
                    if proposed
                    else queue.create(title, **fields)
                )
                result = asdict(task)
                _emit("task.proposed" if proposed else "task.created", result)
                return result

        try:
            return run_side_load(
                _QueueClient(),
                asdict(registration),
                change_ref,
                current_machine=remote_dispatch.local_machine(),
                current_env=registration.env,
            )
        except EmitterError as exc:
            return {"error": str(exc)}

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
            _bulk_task_dict(t)
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

    @mcp.tool(name="dispatch_wakes")
    def wakes(task_id: str) -> list[dict]:
        """Return a task's durable wake outbox operations."""
        return [asdict(wake) for wake in queue.list_wakes(task_id)]

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

    @mcp.tool(name="dispatch_result")
    def result(task_id: str) -> dict:
        """Return a task's structured completion result."""
        task = queue.get(task_id)
        if task is None:
            return {"error": f"no such task {task_id!r}"}
        return {
            "task_id": task.id,
            "ref": task.result_ref,
            "result": queue.read_result(task),
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
            **{k: [_bulk_task_dict(t) for t in v] for k, v in inbox.items()},
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
        from .coordinator import _resolve_owner_session_id

        owner_session_id = _resolve_owner_session_id(worker_id)
        return _mutate(
            lambda: queue.start(task_id, worker_id, owner_session_id=owner_session_id),
            "task.started",
        )

    @mcp.tool(name="dispatch_yield")
    def yield_task(task_id: str, worker_id: str, note: str | None = None) -> dict:
        """Return a held task to ``queued`` with a note (recoverable snag)."""
        return _mutate(lambda: queue.yield_task(task_id, worker_id, note=note), "task.yielded")

    @mcp.tool(name="dispatch_suspend")
    def suspend(task_id: str, worker_id: str, reason: str) -> dict:
        """Park a started task as owner-preserving, dormant ``suspended`` work."""
        return _mutate(
            lambda: queue.suspend(task_id, worker_id, reason=reason),
            "task.suspended",
        )

    @mcp.tool(name="dispatch_resume")
    def resume(
        task_id: str,
        worker_id: str,
        wake: bool = True,
        message: str | None = None,
    ) -> dict:
        """Resume a suspended task under the same owner and optionally wake it."""
        wake_message = message or (
            f"Task {task_id} has been resumed. Continue toward its goal "
            "from the durable progress already recorded."
        )
        task = _mutate(
            lambda: queue.resume(
                task_id,
                worker_id,
                wake_requested=wake,
                wake_message=wake_message,
            ),
            "task.resumed",
        )
        return {
            **task,
            "resume_woken": None,
            "resume_wake_status": (
                task.get("wake_status") if wake else "not_requested"
            ),
        }

    @mcp.tool(name="dispatch_release")
    def release(
        task_id: str,
        worker_id: str,
        reason: str | None = None,
    ) -> dict:
        """Release a suspended task to ``queued`` for replacement embodiment."""
        return _mutate(
            lambda: queue.release_suspended(
                task_id, worker_id, reason=reason
            ),
            "task.released",
        )

    @mcp.tool(name="dispatch_complete")
    def complete(
        task_id: str,
        worker_id: str,
        result_ref: str | None = None,
        result: McpStructuredResult = None,  # type: ignore[assignment]
    ) -> dict:
        """Complete a task.

        Omit ``result`` for no structured result; explicit JSON null is invalid.
        """
        if result is not None:
            result = _validate_mcp_structured_result(result)
        return _mutate(
            lambda: queue.complete_with_outcome(
                task_id, worker_id, result_ref=result_ref, result=result
            ),
            None,
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
        """Force a liveness GC pass (requeue tasks whose owner is confirmed gone)."""
        counts = queue.reconcile_liveness()
        return {"recovered": counts["requeued"], **counts}

    @mcp.tool(name="dispatch_rearm_spawn")
    def rearm_spawn(
        task_id: str,
        permit: bool = False,
        reason: str | None = None,
        min_failures: int = 3,
    ) -> dict:
        """Atomically rearm a queued task's dead-lettered spawn history."""
        try:
            result = queue.rearm_spawn(
                task_id,
                permitted=permit,
                reason=reason,
                min_failures=min_failures,
            )
        except TaskError as exc:
            return {"error": str(exc)}
        bus.publish({"type": "spawn.rearmed", "rearm": result})
        return result

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
