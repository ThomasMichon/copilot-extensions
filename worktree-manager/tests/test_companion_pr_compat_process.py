"""Real caller code against real current and prior PR-association owner processes.

The Companion's discovery, association read, and point read run through the
actual engine client subprocess path. The current owner is the real
agent-worktrees entry point with only its tracking-file location, status
daemon, configuration source, and provider fetch replaced; the snapshot payload
and the Companion codec are never mocked. The prior owner is the frozen
status-parser subset that predates the optional association form.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

import pytest
import yaml

from worktree_manager import companion_pr_state as cpr
from worktree_manager import engine_client as ec
from test_agenda_end_to_end import SOURCE_PATHS

HEAD = "b" * 40
OWNER = r"""
import json, os, sys
from pathlib import Path
from types import SimpleNamespace

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
argv = sys.argv[2:]
with open(spec["log"], "a", encoding="utf-8") as log:
    log.write(json.dumps(argv) + "\n")

if spec["owner"] == "prior":
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("worktree-status-bundle")
    status.add_argument("--worktree", "--worktree-id", dest="worktree_id", required=True)
    status.add_argument("--force-refresh", action="store_true")
    status.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps({"project": "sample", "worktree_id": args.worktree_id, "facts": {}}))
    raise SystemExit(0)

sys.path[:0] = spec["source_paths"]
os.environ["GH_HOST"] = "github.com"
from agent_worktrees import __main__ as main, config, git_ops, providers, repos, tracking
from agent_worktrees import pr_config, pr_read_context, pr_related_binding, pr_watch
from agent_worktrees import session_tracking_cli as cli, worktree_status_daemon
from agent_worktrees.pr_snapshot import PRSnapshot, Review

def forbidden(label):
    def fail(*args, **kwargs):
        raise SystemExit(f"forbidden {label}")
    return fail

git_ops.git = forbidden("git observation")
providers.account_token_for_slug = forbidden("credential acquisition")
tracking.save_record = forbidden("canonical write")
main.front_door_cli.resolve_invocation_project = lambda *args: True
path = Path(spec["record"])
cli._find_tracking_file = lambda wid: path
cli._project_for_tracking_file = lambda found: "sample"
config.project_dir = lambda project: path.parent
repos.list_repos = lambda **kwargs: []
config._control_plane_related_pr_map = lambda: {
    "target": {"provider": "github", "repository": "example/repository"},
}

def load(*args, **kwargs):
    pr_related_binding.control_plane_pr_map()
    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig(provider="github"))
    return SimpleNamespace(repo_name="sample", default_repo=repo, repos={})

config.load_config = load
worktree_status_daemon.status_with_boot = lambda **kwargs: {
    "project": "sample", "worktree_id": "sample-work", "facts": {},
}

if argv[:2] == ["pr-watch", "snapshot"]:
    pr_read_context.load_read_config = lambda path=None: SimpleNamespace()
    pr_config.resolve_repo_config_for_slug = lambda cfg, slug, **kwargs: SimpleNamespace(
        resolved=True, error="",
        repo_config=SimpleNamespace(pr=config.PRConfig(provider="github")),
    )
    construction = spec["snapshot"]

    def build_fetch(prcfg, repo, number, **kwargs):
        def fetch():
            if "error" in construction:
                raise providers.ProviderError(construction["error"])
            values = dict(construction)
            values["reviews"] = tuple(Review(**review) for review in values.get("reviews", ()))
            return PRSnapshot(**values)
        return fetch

    pr_watch.build_fetch = build_fetch

raise SystemExit(main.main(argv))
"""


def _record() -> dict:
    return {
        "worktree_id": "sample-work", "branch": "worktree/sample-work",
        "worktree_path": "/example/work", "repo": "sample", "machine": "host",
        "platform": "linux", "started_at": "2026-01-01", "last_resumed_at": "2026-01-01",
        "resume_count": 0, "title": None, "status": "active", "completed_at": None,
        "creation_nonce": "incarnation-one",
    }


def _pr(**changes) -> dict:
    return {
        "pr_id": "a" * 32, "pr_revision": 1, "provider": "github",
        "repo": "example/repository", "number": 7,
        "url": "https://github.com/example/repository/pull/7", "state": "open", **changes,
    }


@pytest.fixture
def owner(tmp_path, monkeypatch):
    for source in reversed(SOURCE_PATHS):
        assert source.is_dir(), f"missing declared source closure: {source}"
        monkeypatch.syspath_prepend(str(source))
    from agent_worktrees import tracking

    fields = {**asdict(tracking.WorktreeRecord(**_record())), "prs": [_pr()]}
    record = tmp_path / "sample-work.yaml"
    record.write_text(yaml.safe_dump(fields), encoding="utf-8")
    script = tmp_path / "owner.py"
    script.write_text(OWNER, encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    log = tmp_path / "calls.jsonl"

    class Owner:
        command = [sys.executable, str(script), str(spec_path)]
        before = record.read_bytes()

        def set(self, mode: str, *, prs=None, snapshot=None) -> None:
            if prs is not None:
                record.write_text(yaml.safe_dump({**fields, "prs": prs}), encoding="utf-8")
                self.before = record.read_bytes()
            spec_path.write_text(json.dumps({
                "owner": mode, "log": str(log), "record": str(record),
                "source_paths": [str(source) for source in SOURCE_PATHS],
                "snapshot": snapshot or {},
            }), encoding="utf-8")

        def calls(self) -> list[list[str]]:
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

        def unchanged(self) -> bool:
            return record.read_bytes() == self.before

    value = Owner()
    value.set("current")
    return value


def _bundle(owner) -> dict:
    return ec.run_json(None, ["worktree-status-bundle", "--worktree", "sample-work", "--json"],
                       engine_command=owner.command)


def _admitted(owner) -> cpr.Association:
    fact = cpr.read_associations("sample-work", engine_command=owner.command, project="sample")
    (association,), rejected = cpr.admitted(fact)
    assert rejected == 0
    return association


def test_old_caller_new_owner_keeps_prior_form(owner):
    bundle = _bundle(owner)
    assert cpr.supported(bundle) and "pr_associations" not in bundle["facts"]
    assert owner.unchanged()


def test_new_caller_old_owner_is_unsupported_without_a_read(owner):
    owner.set("prior")
    bundle = _bundle(owner)
    assert not cpr.supported(bundle)
    with pytest.raises(ec.EngineError):
        cpr.read_associations("sample-work", engine_command=owner.command)
    assert [call[-1] for call in owner.calls()] == ["--json", "--json"]
    assert "--pr-associations" in owner.calls()[1]


def test_declared_new_new_reads_and_rollback_refuses_before_snapshot(owner):
    association = _admitted(owner)
    assert association.endpoint == "github.com" and association.source == ("sample", "incarnation-one")
    owner.set("current", snapshot={"pr_state": "open", "head_sha": HEAD, "endpoint": "github.com"})
    fact, read = cpr.revalidated_read(association, "sample-work", engine_command=owner.command)
    assert fact is not None and read.state == "open" and read.head_sha == HEAD

    owner.set("prior")
    calls = len(owner.calls())
    fact, read = cpr.revalidated_read(association, "sample-work", engine_command=owner.command)
    assert fact is None and read.state == "unavailable"
    assert read.reason.startswith("association revalidation failed")
    assert [call[0] for call in owner.calls()[calls:]] == ["worktree-status-bundle"]

    owner.set("current", snapshot={"pr_state": "closed", "merged": True, "head_sha": HEAD,
                                   "endpoint": "github.com"})
    fact, read = cpr.revalidated_read(association, "sample-work", engine_command=owner.command)
    assert fact is not None and read.state == "merged"
    assert owner.unchanged()


def _read(owner, snapshot, *, recorded="open"):
    owner.set("current", prs=[_pr(state=recorded)], snapshot=snapshot)
    association = _admitted(owner)
    assert association.recorded_state == recorded
    read = cpr.point_read(association, engine_command=owner.command)
    assert owner.calls()[-1] == ["pr-watch", "snapshot", "example/repository", "7", "--json"]
    assert owner.unchanged()
    return read


@pytest.mark.parametrize(
    ("recorded", "construction", "state"),
    [
        ("merged", {"pr_state": "open"}, "open"),
        ("open", {"pr_state": "closed"}, "closed-unmerged"),
        ("closed", {"pr_state": "closed", "merged": True}, "merged"),
    ],
)
def test_actual_snapshot_state_is_independent_of_recorded_lifecycle(
    owner, recorded, construction, state
):
    read = _read(owner, {**construction, "head_sha": HEAD, "endpoint": "github.com"},
                 recorded=recorded)
    assert read.state == state and read.reason == ""


def test_actual_snapshot_keeps_pending_mergeability_and_reviews(owner):
    read = _read(owner, {
        "pr_state": "open", "head_sha": HEAD, "endpoint": "github.com", "mergeable": None,
        "checks_state": "pending", "checks_complete": False, "draft": True,
        "reviews": [{"id": 3, "state": "APPROVED", "user": "reviewer"}],
    })
    assert (read.state, read.mergeable, read.checks_state, read.checks_complete) == (
        "open", None, "pending", False)
    assert (read.draft, read.reviews, read.last_review) == (True, 1, "APPROVED")


@pytest.mark.parametrize(
    ("construction", "reason"),
    [
        ({"endpoint": "foreign.example"}, "foreign endpoint"),
        ({"endpoint": ""}, "endpoint unknown"),
        ({"pr_state": "draft"}, "malformed typed state"),
        ({"merged": "yes"}, "malformed typed state"),
        ({"mergeable": "unknown"}, "malformed typed state"),
        ({"checks_state": "green"}, "malformed typed state"),
        ({"head_sha": "not-an-object-id"}, "malformed typed state"),
        ({"merged": True}, "contradictory merged/open state"),
        ({"error": "provider read failed"}, "(exit 3)"),
        ({"title": "x" * cpr.MAX_SNAPSHOT_BYTES}, "exceeded"),
    ],
)
def test_actual_snapshot_failures_are_explicit_unavailability(owner, construction, reason):
    snapshot = construction if "error" in construction else {
        "pr_state": "open", "head_sha": HEAD, "endpoint": "github.com", **construction,
    }
    read = _read(owner, snapshot)
    assert read.state == "unavailable" and reason in read.reason
