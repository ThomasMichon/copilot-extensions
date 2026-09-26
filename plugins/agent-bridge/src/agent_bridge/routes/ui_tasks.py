"""Task verbs for the ``/ui`` control surface.

The bridge UI's unit of work is a *task*: a Picker-visible worktree plus the
sessions that run in it (and the venue workers it supervises). These routes
wrap the existing non-interactive ``agent-worktrees`` CLI, so the UI can list
every workspace -- including old ones with no live session -- start a new task,
and resume an old one. Everything runs argv-only (never a shell string), from a
fixed verb set, against projects the local repo registry names.

- ``GET  /api/v1/ui/workspaces`` -- worktrees of every registered worktree-class
  repo, served stale-while-revalidate (listing a large repo takes seconds).
- ``POST /api/v1/ui/tasks`` -- ``create --origin user`` then ``embody --seed``.
- ``POST /api/v1/ui/tasks/{worktree_id}/resume`` -- ``embody`` an existing one.

The write routes launch Copilot sessions, so beyond the bearer token they
require a same-origin browser request and are rate limited.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..agent_registry import _agent_worktrees_bin
from . import live_sessions as _live
from . import worktrees as _wt

log = logging.getLogger("agent-bridge")

router = APIRouter()

#: How long a workspace listing is served before a background refresh.
WORKSPACES_TTL = 20.0
#: Per-command budgets (seconds). Listing a big repo takes ~10s; create runs setup.
LIST_TIMEOUT = 90.0
CREATE_TIMEOUT = 180.0
EMBODY_TIMEOUT = 240.0
#: How long live PR titles/states and commit subjects are reused (seconds).
PR_TTL = 120.0
SUBJECT_TTL = 300.0
#: Task launches allowed per rolling minute.
LAUNCHES_PER_MINUTE = 6
MAX_PROMPT_CHARS = 20000
#: agent-worktrees keeps titles short for the Picker (longer ones are cut there).
MAX_TITLE_CHARS = 30

#: Worktree fields the UI uses; everything else stays on the host.
_ROW_FIELDS = (
    "id", "repo", "machine", "path", "branch", "status", "title", "summary", "codename",
    "origin", "picker_hidden", "follow_up", "live_intent", "live_intent_at", "live_rest",
    "live_session_ids", "session_lock_live", "last_session_id", "started_at",
    "last_resumed_at", "status_note_at", "session_count", "turn_count", "resume_count", "pr",
    "claims_summary", "pair_id", "pair_kind", "pair_role",
)
_GITHUB_PR = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)$")


def _state(request: Request) -> dict[str, Any]:
    st = getattr(request.app.state, "ui_tasks", None)
    if st is None:
        st = request.app.state.ui_tasks = {
            "cache": None, "refresh": None, "launches": [], "lock": asyncio.Lock(),
            "prs": {}, "subjects": {},
        }
    return st


async def _aw(args: list[str], *, timeout: float, parse: bool = True) -> tuple[Any | None, str]:
    """Run ``agent-worktrees <args>``; parse its JSON stdout unless ``parse=False``."""
    exe = _agent_worktrees_bin()
    if not exe:
        return None, "agent-worktrees is not installed on this machine"
    stdout, stderr = await _wt._exec_ex([exe, *args], timeout=timeout)
    if stdout is None:
        return None, (stderr or "").strip()[-600:] or "agent-worktrees failed or timed out"
    if not parse:
        return stdout, ""
    try:
        return json.loads(stdout), ""
    except ValueError:
        return None, "agent-worktrees returned non-JSON output"


def _find(data: Any, key: str) -> Any:
    """First value for *key* in a nested JSON document (breadth-first)."""
    queue = [data]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, dict):
            if cur.get(key):
                return cur[key]
            queue.extend(cur.values())
        elif isinstance(cur, list):
            queue.extend(cur)
    return None


def _project_row(row: dict[str, Any], project: str) -> dict[str, Any]:
    out = {k: row[k] for k in _ROW_FIELDS if k in row}
    out["project"] = project
    return out


async def _list_projects() -> tuple[list[str], str]:
    data, err = await _aw(["repos", "list", "--json"], timeout=30.0)
    if data is None:
        return [], err
    repos = data.get("repos", data) if isinstance(data, dict) else data
    names = [
        str(r["name"]) for r in repos or []
        if isinstance(r, dict) and r.get("name") and r.get("class", "worktree") == "worktree"
    ]
    return sorted(set(names)), ""


async def _pr_details(st: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Attach each GitHub PR's live title and state (one query per repository).

    The worktree record stores a PR's state as of when it was opened; the board
    needs the current one. Numbers and names are validated before they go into
    the query, and the call runs through the repo-scoped account resolver.
    """
    now = time.time()
    cache: dict[str, tuple[float, dict[str, Any]]] = st["prs"]
    wanted: dict[str, set[int]] = {}
    for row in rows:
        pr = row.get("pr")
        m = _GITHUB_PR.match(str((pr or {}).get("url") or "")) if isinstance(pr, dict) else None
        if not m:
            continue
        hit = cache.get(m.group(0))
        if hit and now - hit[0] < PR_TTL:
            continue
        wanted.setdefault(f"{m.group(1)}/{m.group(2)}", set()).add(int(m.group(3)))

    async def one(slug: str, numbers: set[int]) -> None:
        owner, name = slug.split("/", 1)
        fields = " ".join(
            f"p{n}: pullRequest(number: {n}) {{ title state }}" for n in sorted(numbers)
        )
        query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'
        data, err = await _aw(
            ["repos", "gh", slug, "--", "api", "graphql", "-f", f"query={query}"], timeout=30.0,
        )
        repo = ((data or {}).get("data") or {}).get("repository") or {}
        if not repo:
            log.info("ui: PR lookup for %s failed: %s", slug, err)
            return
        for n in numbers:
            info = repo.get(f"p{n}")
            if isinstance(info, dict):
                cache[f"https://github.com/{slug}/pull/{n}"] = (now, {
                    "title": str(info.get("title") or ""),
                    "state": str(info.get("state") or "").lower(),
                })

    await asyncio.gather(*(one(s, n) for s, n in wanted.items()))
    for row in rows:
        pr = row.get("pr")
        if isinstance(pr, dict) and pr.get("url") in cache:
            row["pr"] = {**pr, **cache[pr["url"]][1], "live": True}


async def _subjects(st: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Attach the subject of each worktree's newest unpushed commit, if any."""
    git = shutil.which("git")
    if not git:
        return
    now = time.time()
    cache: dict[str, tuple[float, str]] = st["subjects"]
    gate = asyncio.Semaphore(8)

    async def one(row: dict[str, Any]) -> None:
        key, path = row["id"], row.get("path")
        hit = cache.get(key)
        if hit is None or now - hit[0] >= SUBJECT_TTL:
            async with gate:
                out, _err = await _wt._exec_ex(
                    [git, "-C", str(path), "log", "-1", "--format=%s", "HEAD",
                     "--not", "--remotes"],
                    timeout=15.0)
            hit = cache[key] = (now, (out or "").strip()[:200])
        if hit[1]:
            row["subject"] = hit[1]

    await asyncio.gather(
        *(one(r) for r in rows if r.get("status") == "active" and r.get("path"))
    )


async def _collect(st: dict[str, Any]) -> dict[str, Any]:
    projects, err = await _list_projects()
    errors: dict[str, str] = {"_repos": err} if err else {}

    async def one(project: str) -> list[dict[str, Any]]:
        data, e = await _aw(["-p", project, "list", "--json", "--all"], timeout=LIST_TIMEOUT)
        if data is None:
            errors[project] = e
            return []
        rows = data.get("worktrees", data) if isinstance(data, dict) else data
        return [
            _project_row(r, project) for r in rows or [] if isinstance(r, dict) and r.get("id")
        ]

    lists = await asyncio.gather(*(one(p) for p in projects))
    rows = [r for group in lists for r in group]
    await asyncio.gather(_pr_details(st, rows), _subjects(st, rows))
    return {"workspaces": rows, "projects": projects, "errors": errors, "fetched_at": time.time()}


def _start_refresh(st: dict[str, Any]) -> asyncio.Task:
    """Single-flight refresh: concurrent callers share one listing pass.

    The task is held in ``st["refresh"]`` until the next one replaces it, so a
    background refresh is never garbage-collected mid-run.
    """
    task = st.get("refresh")
    if task is None or task.done():
        async def run() -> dict[str, Any]:
            st["dirty"] = False
            result = await _collect(st)
            st["cache"] = result
            if st.get("dirty"):
                # A write landed mid-pass, so this result may predate it; run
                # again once this task has finished.
                asyncio.get_running_loop().call_soon(_start_refresh, st)
            return result

        task = st["refresh"] = asyncio.create_task(run(), name="ui-workspaces-refresh")
    return task


async def _refresh(st: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.shield(_start_refresh(st))


def _revalidate(st: dict[str, Any]) -> None:
    """Refresh the listing in the background; readers keep the previous one meanwhile."""
    st["dirty"] = True
    _start_refresh(st)


@router.get("/api/v1/ui/workspaces", include_in_schema=False)
async def list_workspaces(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Every worktree of every registered repo (active and ended), cached."""
    st = _state(request)
    cache = st.get("cache")
    if cache is None or refresh:
        cache = await _refresh(st)
        return {**cache, "stale": False}
    stale = time.time() - cache["fetched_at"] > WORKSPACES_TTL
    if stale:
        _revalidate(st)
    return {**cache, "stale": stale}


# -- write verbs ------------------------------------------------------------------


def _refuse(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _same_origin(request: Request) -> bool:
    """Only the bridge's own page may launch sessions (defense in depth over the token)."""
    origin = request.headers.get("origin")
    if origin is None:
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")
    host = request.headers.get("host", "")
    return origin in (f"http://{host}", f"https://{host}")


def _take_launch_slot(st: dict[str, Any]) -> bool:
    now = time.monotonic()
    st["launches"] = [t for t in st["launches"] if now - t < 60.0]
    if len(st["launches"]) >= LAUNCHES_PER_MINUTE:
        return False
    st["launches"].append(now)
    return True


async def _guard(
    request: Request,
) -> tuple[dict[str, Any], dict[str, Any] | None, JSONResponse | None]:
    st = _state(request)
    if not _same_origin(request):
        return st, None, _refuse(403, "cross-origin requests may not launch sessions")
    try:
        body = await request.json()
    except ValueError:
        body = None
    if body is not None and not isinstance(body, dict):
        return st, None, _refuse(400, "expected a JSON object")
    if not _take_launch_slot(st):
        return st, None, _refuse(429, "too many launches in the last minute; try again shortly")
    return st, body or {}, None


async def _embody(project: str, worktree_id: str, seed: str | None) -> tuple[Any | None, str]:
    args = ["-p", project, "embody", "--worktree-id", worktree_id, "--json"]
    if seed:
        args[5:5] = ["--seed", seed]
    return await _aw(args, timeout=EMBODY_TIMEOUT)


@router.post("/api/v1/ui/tasks", include_in_schema=False)
async def start_task(request: Request) -> JSONResponse:
    """Start a task: a fresh Picker-visible worktree whose session is seeded with *prompt*."""
    st, body, refused = await _guard(request)
    if refused:
        return refused
    project = str(body.get("project") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    title = " ".join(str(body.get("title") or "").split())[:MAX_TITLE_CHARS]
    if not prompt:
        return _refuse(400, "describe the task in the prompt")
    if len(prompt) > MAX_PROMPT_CHARS:
        return _refuse(400, f"the prompt is longer than {MAX_PROMPT_CHARS} characters")
    projects, err = await _list_projects()
    if project not in projects:
        known = ", ".join(projects)
        return _refuse(400, err or f"unknown project {project!r}; choose one of: {known}")

    async with st["lock"]:
        created, err = await _aw(["-p", project, "create", "--origin", "user", "--json"],
                                 timeout=CREATE_TIMEOUT)
        worktree_id = _find(created, "worktree_id") if created is not None else None
        if not worktree_id:
            reason = err or "no worktree id returned"
            return _refuse(502, f"could not create a worktree: {reason}")
        if title:
            _set, terr = await _aw(["-p", project, "status", "--worktree-id", str(worktree_id),
                                    "--title", title], timeout=30.0, parse=False)
            if _set is None:
                log.warning("ui task %s: could not set title: %s", worktree_id, terr)
        embodied, err = await _embody(project, str(worktree_id), prompt)
    _revalidate(st)
    if embodied is None or not embodied.get("ok", True):
        reason = err or str(_find(embodied, "error") or "embody failed")
        return _refuse(
            502, f"created worktree {worktree_id} but could not start its session: {reason}",
        )
    return JSONResponse({
        "worktree_id": worktree_id, "project": project,
        "seeded": bool(embodied.get("seed_submitted")),
        "seed_reason": embodied.get("seed_reason"),
    })


@router.post("/api/v1/ui/tasks/{worktree_id}/resume", include_in_schema=False)
async def resume_task(worktree_id: str, request: Request) -> JSONResponse:
    """Start a Copilot session again in an existing worktree (optionally with a first prompt)."""
    st, body, refused = await _guard(request)
    if refused:
        return refused
    cache = st.get("cache") or await _refresh(st)
    row = next((r for r in cache["workspaces"] if r.get("id") == worktree_id), None)
    if row is None:
        return _refuse(404, "unknown worktree")
    if row.get("status") != "active":
        status = row.get("status")
        return _refuse(409, f"this worktree is {status}; only active worktrees can resume")
    live = (await _live.list_live_sessions(request, worktree_id=worktree_id)).live_sessions
    if row.get("session_lock_live") or any(s.status == "live" for s in live):
        return _refuse(409, "this worktree already has a live session")
    prompt = str(body.get("prompt") or "").strip()[:MAX_PROMPT_CHARS]
    async with st["lock"]:
        embodied, err = await _embody(row["project"], worktree_id, prompt or None)
    _revalidate(st)
    if embodied is None or not embodied.get("ok", True):
        return _refuse(502, "could not resume: " + (err or "embody failed"))
    return JSONResponse({"worktree_id": worktree_id, "resumed": True})


@router.post("/api/v1/ui/tasks/{worktree_id}/title", include_in_schema=False)
async def set_task_title(worktree_id: str, request: Request) -> JSONResponse:
    """Rename a task: sets the worktree's title (the same one the Picker shows)."""
    st = _state(request)
    if not _same_origin(request):
        return _refuse(403, "cross-origin requests may not change tasks")
    try:
        body = await request.json()
    except ValueError:
        body = None
    raw = (body or {}).get("title") if isinstance(body, dict) else ""
    title = " ".join(str(raw or "").split())
    if not title:
        return _refuse(400, "a title is required")
    if len(title) > MAX_TITLE_CHARS:
        return _refuse(400, f"keep the title to {MAX_TITLE_CHARS} characters")
    cache = st.get("cache") or await _refresh(st)
    row = next((r for r in cache["workspaces"] if r.get("id") == worktree_id), None)
    if row is None:
        return _refuse(404, "unknown worktree")
    done, err = await _aw(["-p", row["project"], "status", "--worktree-id", worktree_id,
                           "--title", title], timeout=30.0, parse=False)
    if done is None:
        return _refuse(502, "could not set the title: " + (err or "agent-worktrees failed"))
    row["title"] = title
    _revalidate(st)
    return JSONResponse({"worktree_id": worktree_id, "title": title})
