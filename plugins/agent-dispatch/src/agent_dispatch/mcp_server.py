"""Local stdio MCP shim for agent-dispatch (``agent-dispatch mcp``).

This is the **per-agent interaction layer**: a thin, local `MCP
<https://modelcontextprotocol.io>`_ server that an agent attaches to over stdio.
It resolves the caller's identity (``machine``/``worktree``) from the current
directory -- exactly the way the CLI and git do -- and proxies each tool call to
the (possibly remote) coordinator over HTTP via :class:`DispatchClient`. So an
agent gets first-class dispatch *tools* with its worktree identity injected
automatically, and no separate credential/bridge wiring.

The tool logic lives in :class:`DispatchTools` (a plain, transport-free object
taking a client factory + identity resolver) so it is unit-testable without an
MCP transport; :func:`build_server` wraps those methods as FastMCP tools.

Requires the optional ``mcp`` extra (``pip install 'agent-dispatch[mcp]'``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .client import DispatchClient
from .config import client_token, client_url
from .identity import resolve_identity, resolve_repo, resolve_repo_selector
from .queue import worker_id_for

ClientFactory = Callable[[], DispatchClient]
IdentityResolver = Callable[[], "tuple[str | None, str | None]"]
RepoResolver = Callable[[], "str | None"]


def _default_client() -> DispatchClient:
    return DispatchClient(client_url(), token=client_token())


class DispatchTools:
    """Transport-free implementation of the dispatch MCP tools.

    Each method opens a short-lived client to the coordinator. Identity-bearing
    tools (``claim``, ``worktree_status``) resolve ``machine``/``worktree`` from
    the working directory unless the caller overrides them. Repo-scoped tools
    (``create``, ``find``, ``list``, ``sweep``, ``claim``, ``worktree_status``)
    resolve the caller's **lane** (repo) the same way -- tasks stay in their
    producing repo's lane.
    """

    def __init__(
        self,
        client_factory: ClientFactory = _default_client,
        identity_resolver: IdentityResolver = resolve_identity,
        repo_resolver: RepoResolver = resolve_repo,
    ):
        self._client_factory = client_factory
        self._identity = identity_resolver
        self._repo = repo_resolver

    def _resolve(self, machine: str | None, worktree: str | None) -> tuple[str | None, str | None]:
        if machine is None or worktree is None:
            r_machine, r_worktree = self._identity()
            machine = machine or r_machine
            worktree = worktree or r_worktree
        return machine, worktree

    def _scope_repo(self, repo: str | None) -> str | None:
        """Resolve the lane: an explicit ``repo`` (local name or remote) wins,
        else the calling repo from the CWD. Returns a canonical remote or None."""
        return resolve_repo_selector(repo) if repo else self._repo()

    # -- producers -----------------------------------------------------------

    def create(
        self,
        title: str,
        *,
        repo: str | None = None,
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

        ``repo`` is the **lane** (a local repo name or remote URL); it defaults
        to the calling repo resolved from the CWD. Tasks stay in their producing
        repo's lane -- for a cross-repo *code* target use ``target_repo`` and let
        the lane agent do it via ``working-cross-repo``.

        ``payload`` is inline Markdown; the coordinator spills it to a
        content-addressed blob when large. Prefer ``sweep``/``find`` before
        ``create`` to avoid duplicates (``dedup_key`` backstops it).
        """
        lane = self._scope_repo(repo)
        if not lane:
            raise ValueError(
                "could not resolve the repo (lane); pass repo=<local name|remote URL>"
            )
        with self._client_factory() as c:
            return c.create(
                title,
                repo=lane,
                prompt=prompt,
                proposed=proposed,
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

    def approve(self, task_id: str) -> dict:
        """Move a ``proposed`` task to ``queued`` (makes it claimable)."""
        with self._client_factory() as c:
            return c.approve(task_id)

    # -- browse --------------------------------------------------------------

    def find(self, query: str, limit: int = 50, repo: str | None = None) -> list[dict]:
        """Substring-search task titles/prompts in the lane -- a quick dedup probe."""
        with self._client_factory() as c:
            return c.find(query, repo=self._scope_repo(repo), limit=limit)

    def sweep(self, limit: int = 500, repo: str | None = None) -> list[dict]:
        """The dedup corpus for the lane: every non-abandoned task, newest first.

        Read this before ``create`` to verify the work doesn't already exist.
        """
        with self._client_factory() as c:
            return c.sweep(repo=self._scope_repo(repo), limit=limit)

    def list(
        self,
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        limit: int = 200,
        repo: str | None = None,
    ) -> list[dict]:
        """List tasks in the lane, optionally filtered by status/machine/repo/label."""
        with self._client_factory() as c:
            return c.list(
                repo=self._scope_repo(repo),
                status=status,
                target_machine=target_machine,
                target_repo=target_repo,
                label=label,
                limit=limit,
            )

    def show(self, task_id: str) -> dict:
        """Return one task's full record."""
        with self._client_factory() as c:
            return c.get(task_id)

    def events(self, task_id: str) -> list[dict]:
        """Return a task's append-only audit trail."""
        with self._client_factory() as c:
            return c.events(task_id)

    def payload(self, task_id: str) -> dict:
        """Return a task's resolved payload (inline text or blob content)."""
        with self._client_factory() as c:
            return c.payload(task_id)

    # -- identity-bearing ----------------------------------------------------

    def worktree_status(
        self, machine: str | None = None, worktree: str | None = None, repo: str | None = None
    ) -> dict:
        """This worktree's inbox: tasks targeted at + owned by its identity.

        Identity and lane are resolved from the working directory unless overridden.
        """
        machine, worktree = self._resolve(machine, worktree)
        if not machine or not worktree:
            return {"error": "could not resolve worktree identity; pass machine and worktree"}
        lane = self._scope_repo(repo)
        with self._client_factory() as c:
            inbox = c.mine(machine, worktree, repo=lane)
        return {"machine": machine, "worktree": worktree, "repo": lane, **inbox}

    def claim(
        self,
        capabilities: list[str] | None = None,
        task_id: str | None = None,
        lease_seconds: int | None = None,
        machine: str | None = None,
        worktree: str | None = None,
        repo: str | None = None,
    ) -> dict | None:
        """Atomically lease one eligible task (identity + lane auto-resolved from CWD).

        The claim honors the repo lane and targeting: only tasks in this repo's
        lane that are untargeted or targeted at this identity are eligible.
        Returns the claimed task, or ``None`` when nothing is claimable.
        """
        machine, worktree = self._resolve(machine, worktree)
        worker_id = worker_id_for(machine, worktree) if machine and worktree else None
        with self._client_factory() as c:
            return c.claim(
                worker_id=worker_id,
                capabilities=capabilities or [],
                repo=self._scope_repo(repo),
                machine=machine,
                worktree=worktree,
                task_id=task_id,
                lease_seconds=lease_seconds,
            )

    # -- lifecycle -----------------------------------------------------------

    def start(self, task_id: str, worker_id: str) -> dict:
        """Mark a claimed task ``started`` (under active implementation)."""
        with self._client_factory() as c:
            return c.start(task_id, worker_id)

    def yield_task(self, task_id: str, worker_id: str, note: str | None = None) -> dict:
        """Return a held task to ``queued`` with a note (a recoverable snag)."""
        with self._client_factory() as c:
            return c.yield_task(task_id, worker_id, note=note)

    def complete(self, task_id: str, worker_id: str, result_ref: str | None = None) -> dict:
        """Mark a started task ``completed``."""
        with self._client_factory() as c:
            return c.complete(task_id, worker_id, result_ref=result_ref)

    def abandon(
        self,
        task_id: str,
        worker_id: str | None = None,
        permit: bool = False,
        reason: str | None = None,
    ) -> dict:
        """Terminally abandon a task -- requires ``permit=True`` (permission-gated)."""
        with self._client_factory() as c:
            return c.abandon(task_id, worker_id=worker_id, permitted=permit, reason=reason)

    def heartbeat(self, task_id: str, worker_id: str) -> dict:
        """Extend the lease on a held task during long work."""
        with self._client_factory() as c:
            return c.heartbeat(task_id, worker_id)

    def detach(self, task_id: str) -> dict:
        """Demote a hard worktree pin to a soft affinity (portability)."""
        with self._client_factory() as c:
            return c.detach(task_id)

    def recover(self) -> dict:
        """Force a lease-recovery sweep (requeue expired-lease tasks)."""
        with self._client_factory() as c:
            return c.recover()


def build_server(tools: DispatchTools | None = None) -> Any:
    """Build the FastMCP stdio server exposing the dispatch tools.

    Imported lazily so the ``mcp`` extra is only required for ``agent-dispatch
    mcp``, not for the CLI/coordinator.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover -- exercised via the CLI path
        raise RuntimeError(
            "the MCP server requires the 'mcp' extra: pip install 'agent-dispatch[mcp]'"
        ) from exc

    t = tools or DispatchTools()
    mcp = FastMCP("agent-dispatch")

    # Register each DispatchTools method as an MCP tool. Explicit wrappers keep
    # the tool schemas (names, params, docstrings) stable and discoverable.
    mcp.tool(name="dispatch_create")(t.create)
    mcp.tool(name="dispatch_approve")(t.approve)
    mcp.tool(name="dispatch_find")(t.find)
    mcp.tool(name="dispatch_sweep")(t.sweep)
    mcp.tool(name="dispatch_list")(t.list)
    mcp.tool(name="dispatch_show")(t.show)
    mcp.tool(name="dispatch_events")(t.events)
    mcp.tool(name="dispatch_payload")(t.payload)
    mcp.tool(name="dispatch_worktree_status")(t.worktree_status)
    mcp.tool(name="dispatch_claim")(t.claim)
    mcp.tool(name="dispatch_start")(t.start)
    mcp.tool(name="dispatch_yield")(t.yield_task)
    mcp.tool(name="dispatch_complete")(t.complete)
    mcp.tool(name="dispatch_abandon")(t.abandon)
    mcp.tool(name="dispatch_heartbeat")(t.heartbeat)
    mcp.tool(name="dispatch_detach")(t.detach)
    mcp.tool(name="dispatch_recover")(t.recover)
    return mcp


def serve_stdio() -> None:
    """Run the local dispatch MCP server over stdio (blocking)."""
    build_server().run(transport="stdio")
