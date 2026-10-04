"""Tests for the /ui task verbs (routes/ui_tasks.py) with a fake agent-worktrees."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.routes import ui, ui_tasks

REPOS = {"repos": [
    {"name": "harness", "class": "worktree"}, {"name": "ext", "class": "worktree"},
    {"name": "scratch", "class": "clone"},
]}


class FakeCli:
    """Records every agent-worktrees / git argv and answers from a script."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.lists = {
            "harness": [{"id": "h1", "repo": "harness", "status": "active", "path": "/w/h1",
                         "title": "Fix login", "summary": "PR open", "secret_field": "x",
                         "pr": {"number": 7, "url": "https://github.com/o/harness/pull/7", "state": "open"}}],
            "ext": [{"id": "e1", "repo": "ext", "status": "finalized"},
                    {"id": "e2", "repo": "ext", "status": "active", "path": "/w/e2"}],
        }
        self.fail: set[str] = set()
        self.state_roots: dict[str, dict] = {}
        self.repos = json.loads(json.dumps(REPOS))

    async def __call__(self, cmd, *, timeout=None):
        args = list(cmd[1:])
        self.calls.append(args)
        if cmd[0] == "git":
            return ("Teach the thing\n" if "/w/e2" in args else ""), ""
        verb = args[2] if args[:1] == ["-p"] else args[0]
        if verb in self.fail:
            return None, f"{verb} exploded"
        if args[:2] == ["repos", "list"]:
            return json.dumps(self.repos), ""
        if verb == "state-root":
            return json.dumps(self.state_roots.get(args[1], {"source": "project", "repo": args[1]})), ""
        if args[:2] == ["repos", "gh"]:
            return json.dumps({"data": {"repository": {"p7": {"title": "Fix the login page", "state": "MERGED"}}}}), ""
        if verb == "list":
            return json.dumps({"version": 1, "worktrees": self.lists[args[1]]}), ""
        if verb == "create":
            return json.dumps({"version": 1, "plan": {"worktree_id": "h-new", "project": args[1]}}), ""
        if verb == "status":
            return "[OK] title set\n", ""
        if verb == "embody":
            return json.dumps({"ok": True, "worktree_id": args[4], "seed_submitted": "--seed" in args}), ""
        raise AssertionError(f"unexpected argv {args}")


@pytest.fixture
def cli(monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(ui_tasks, "_agent_worktrees_bin", lambda: "agent-worktrees")
    monkeypatch.setattr(ui_tasks.shutil, "which", lambda name: "git" if name == "git" else None)
    monkeypatch.setattr(ui_tasks._wt, "_exec_ex", fake)
    live: list = []

    async def fake_live(request, worktree_id=None, include_dead=False):
        return SimpleNamespace(live_sessions=[s for s in live if s.worktree_id == worktree_id])

    monkeypatch.setattr(ui_tasks._live, "list_live_sessions", fake_live)
    fake.live = live
    return fake


@pytest.fixture
def client(cli):
    app = FastAPI()
    app.include_router(ui.router)
    return TestClient(app, base_url="http://127.0.0.1:10756")


SAME = {"Origin": "http://127.0.0.1:10756"}


def test_workspaces_lists_every_worktree_repo_with_a_projection(client, cli) -> None:
    data = client.get("/api/v1/ui/workspaces").json()
    assert data["projects"] == ["ext", "harness"]  # the non-worktree clone is not a project
    rows = {r["id"]: r for r in data["workspaces"]}
    assert set(rows) == {"h1", "e1", "e2"}
    assert rows["h1"]["project"] == "harness" and "secret_field" not in rows["h1"]
    assert ["-p", "ext", "list", "--json", "--all"] in cli.calls
    # The PR's live title/state replaces the stale recorded one, one query per repo.
    assert rows["h1"]["pr"]["state"] == "merged" and rows["h1"]["pr"]["title"] == "Fix the login page"
    gh = [c for c in cli.calls if c[:2] == ["repos", "gh"]]
    assert len(gh) == 1 and gh[0][2] == "o/harness"
    assert "pullRequest(number: 7)" in gh[0][-1]
    # Unpushed commit subjects describe untitled work; finalized rows are skipped.
    assert rows["e2"]["subject"] == "Teach the thing" and "subject" not in rows["e1"]


def test_workspaces_are_served_from_cache_and_revalidated(client, cli) -> None:
    client.get("/api/v1/ui/workspaces")
    n = len(cli.calls)
    again = client.get("/api/v1/ui/workspaces").json()
    assert again["stale"] is False and len(cli.calls) == n
    st = client.app.state.ui_tasks
    st["cache"]["fetched_at"] = time.time() - ui_tasks.WORKSPACES_TTL - 1
    assert client.get("/api/v1/ui/workspaces").json()["stale"] is True


def test_a_failing_repo_is_reported_without_hiding_the_others(client, cli) -> None:
    cli.lists["ext"] = None
    original = cli.__call__

    async def flaky(cmd, *, timeout=None):
        if cmd[1:4] == ["-p", "ext", "list"]:
            return None, "boom"
        return await original(cmd, timeout=timeout)

    ui_tasks._wt._exec_ex = flaky
    data = client.get("/api/v1/ui/workspaces").json()
    assert data["errors"] == {"ext": "boom"}
    assert [r["id"] for r in data["workspaces"]] == ["h1"]


def test_a_listing_that_fails_later_keeps_that_repos_last_good_rows(client, cli) -> None:
    assert {r["id"] for r in client.get("/api/v1/ui/workspaces").json()["workspaces"]} == {"h1", "e1", "e2"}
    original = cli.__call__

    async def slow_ext(cmd, *, timeout=None):
        if cmd[1:4] == ["-p", "ext", "list"]:
            return None, "timed out after 90s"
        return await original(cmd, timeout=timeout)

    ui_tasks._wt._exec_ex = slow_ext
    data = client.get("/api/v1/ui/workspaces?refresh=true").json()
    assert data["errors"] == {"ext": "timed out after 90s"}
    assert {r["id"] for r in data["workspaces"]} == {"h1", "e1", "e2"}  # ext's rows are kept, not blanked


def test_a_failing_repo_registry_keeps_the_previous_projects(client, cli) -> None:
    first = client.get("/api/v1/ui/workspaces").json()
    original = cli.__call__

    async def no_registry(cmd, *, timeout=None):
        if cmd[1:3] == ["repos", "list"]:
            return None, "registry busy"
        return await original(cmd, timeout=timeout)

    ui_tasks._wt._exec_ex = no_registry
    data = client.get("/api/v1/ui/workspaces?refresh=true").json()
    assert data["errors"].get("_repos") == "registry busy"
    assert data["projects"] == first["projects"] and data["project_info"] == first["project_info"]
    assert {r["id"] for r in data["workspaces"]} == {"h1", "e1", "e2"}


def test_github_pr_urls_are_validated_before_they_reach_a_query(client, cli) -> None:
    cli.lists["harness"][0]["pr"]["url"] = 'https://github.com/o/x"){evil}/pull/7'
    client.get("/api/v1/ui/workspaces")
    assert not [c for c in cli.calls if c[:2] == ["repos", "gh"]]


def test_start_task_creates_titles_and_seeds_a_worktree(client, cli) -> None:
    resp = client.post("/api/v1/ui/tasks", headers=SAME,
                       json={"project": "harness", "prompt": "Fix the login page", "title": "  Login  fix "})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"worktree_id": "h-new", "project": "harness", "mode": None,
                           "seeded": True, "seed_reason": None}
    assert ["-p", "harness", "create", "--origin", "user", "--json"] in cli.calls
    assert ["-p", "harness", "status", "--worktree-id", "h-new", "--title", "Login fix"] in cli.calls
    embody = next(c for c in cli.calls if "embody" in c)
    assert embody == ["-p", "harness", "embody", "--worktree-id", "h-new", "--seed",
                      "Fix the login page", "--json"]


@pytest.mark.parametrize("body, status", [
    ({"project": "harness", "prompt": "  "}, 400),
    ({"project": "scratch", "prompt": "x"}, 400),
    ({"project": "../etc", "prompt": "x"}, 400),
    ({"project": "harness", "prompt": "x" * (ui_tasks.MAX_PROMPT_CHARS + 1)}, 400),
])
def test_start_task_validates_its_input(client, cli, body, status) -> None:
    assert client.post("/api/v1/ui/tasks", headers=SAME, json=body).status_code == status
    assert not [c for c in cli.calls if "create" in c]


MODES_YAML = """
modes:
  - id: task
    label: One task
    description: One worker.
    prompt: "{prompt}"
  - id: campaign
    label: Campaign
    description: Several workers sharing a board.
    prompt: >-
      Run this as a campaign
      across workers. The goal: {prompt}
"""


def _declare_modes(cli, tmp_path, name="harness", text=MODES_YAML):
    checkout = tmp_path / name
    (checkout / ".copilot-extensions" / "agent-bridge").mkdir(parents=True)
    (checkout / ".copilot-extensions" / "agent-bridge" / "task-modes.yaml").write_text(text, "utf-8")
    for row in cli.repos["repos"]:
        if row["name"] == name:
            row["paths"] = {"windows": str(checkout), "linux": str(checkout)}


def test_workspaces_describe_each_repos_modes_and_knowledge_role(client, cli, tmp_path) -> None:
    _declare_modes(cli, tmp_path)
    cli.state_roots["harness"] = {"source": "knowledge_repo", "repo": "ext"}
    info = client.get("/api/v1/ui/workspaces").json()["project_info"]
    assert [m["id"] for m in info["harness"]["modes"]] == ["task", "campaign"]
    assert "prompt" not in info["harness"]["modes"][1]  # templates never leave the host
    assert info["ext"]["knowledge_for"] == "harness" and "knowledge_for" not in info["harness"]
    assert info["ext"]["modes"] == []


def test_start_task_seeds_the_chosen_modes_template(client, cli, tmp_path) -> None:
    _declare_modes(cli, tmp_path)
    resp = client.post("/api/v1/ui/tasks", headers=SAME,
                       json={"project": "harness", "prompt": "Speed up the list page", "mode": "campaign"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "campaign"
    embody = next(c for c in cli.calls if "embody" in c)
    assert embody[embody.index("--seed") + 1] == (
        "Run this as a campaign across workers. The goal: Speed up the list page")


def test_start_task_defaults_to_the_first_mode_and_refuses_an_undeclared_one(client, cli, tmp_path) -> None:
    _declare_modes(cli, tmp_path)
    assert client.post("/api/v1/ui/tasks", headers=SAME,
                       json={"project": "harness", "prompt": "Fix it"}).status_code == 200
    embody = next(c for c in cli.calls if "embody" in c)
    assert embody[embody.index("--seed") + 1] == "Fix it"
    for project, mode in (("harness", "rogue"), ("ext", "campaign")):
        resp = client.post("/api/v1/ui/tasks", headers=SAME, json={"project": project, "prompt": "x", "mode": mode})
        assert resp.status_code == 400 and "task mode" in resp.json()["detail"]


def test_write_verbs_refuse_cross_origin_requests(client, cli) -> None:
    evil = {"Origin": "https://evil.example"}
    body = {"project": "harness", "prompt": "x"}
    assert client.post("/api/v1/ui/tasks", headers=evil, json=body).status_code == 403
    assert client.post("/api/v1/ui/tasks/h1/resume", headers=evil, json={}).status_code == 403
    assert client.post("/api/v1/ui/tasks/h1/title", headers=evil, json={"title": "x"}).status_code == 403
    cross_site = {"Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/v1/ui/tasks", headers=cross_site, json=body).status_code == 403
    assert not [c for c in cli.calls if "create" in c or "embody" in c or "status" in c]


def test_launches_are_rate_limited(client, cli) -> None:
    body = {"project": "harness", "prompt": "x"}
    codes = [client.post("/api/v1/ui/tasks", headers=SAME, json=body).status_code
             for _ in range(ui_tasks.LAUNCHES_PER_MINUTE + 1)]
    assert codes[:-1] == [200] * ui_tasks.LAUNCHES_PER_MINUTE and codes[-1] == 429


def test_a_failed_embody_names_the_worktree_it_created(client, cli) -> None:
    cli.fail.add("embody")
    resp = client.post("/api/v1/ui/tasks", headers=SAME, json={"project": "harness", "prompt": "x"})
    assert resp.status_code == 502 and "h-new" in resp.json()["detail"]


def test_resume_starts_a_session_only_for_an_idle_active_worktree(client, cli) -> None:
    assert client.post("/api/v1/ui/tasks/nope/resume", headers=SAME, json={}).status_code == 404
    assert client.post("/api/v1/ui/tasks/e1/resume", headers=SAME, json={}).status_code == 409
    cli.live.append(SimpleNamespace(worktree_id="h1", status="live"))
    assert client.post("/api/v1/ui/tasks/h1/resume", headers=SAME, json={}).status_code == 409
    cli.live.clear()
    resp = client.post("/api/v1/ui/tasks/h1/resume", headers=SAME, json={"prompt": "Carry on"})
    assert resp.status_code == 200 and resp.json() == {"worktree_id": "h1", "resumed": True}
    assert ["-p", "harness", "embody", "--worktree-id", "h1", "--seed", "Carry on", "--json"] in cli.calls


def test_rename_sets_the_picker_title(client, cli) -> None:
    resp = client.post("/api/v1/ui/tasks/h1/title", headers=SAME, json={"title": " New  name "})
    assert resp.status_code == 200 and resp.json()["title"] == "New name"
    assert ["-p", "harness", "status", "--worktree-id", "h1", "--title", "New name"] in cli.calls
    too_long = "x" * (ui_tasks.MAX_TITLE_CHARS + 1)
    assert client.post("/api/v1/ui/tasks/h1/title", headers=SAME, json={"title": too_long}).status_code == 400
    assert client.post("/api/v1/ui/tasks/zz/title", headers=SAME, json={"title": "x"}).status_code == 404


def test_the_launch_budget_outlasts_embodys_seed_wait():
    """embody keeps waiting for a busy Copilot to take its seed up to its hard
    cap (agent-worktrees ``mux_seed_pane(hard_timeout=900.0)``); a shorter UI
    budget would kill the launcher mid-wait and leave the session unseeded."""
    import inspect

    from agent_bridge.routes import ui_tasks

    assert ui_tasks.EMBODY_TIMEOUT > ui_tasks.EMBODY_SEED_HARD_CAP >= 900.0
    try:
        from agent_worktrees import sessions
    except ImportError:  # agent-worktrees isn't importable from this plugin's test env
        return
    cap = inspect.signature(sessions.mux_seed_pane).parameters["hard_timeout"].default
    assert ui_tasks.EMBODY_TIMEOUT > cap
