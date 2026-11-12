"""Real Git discovery through coordinator routes, without enrolling a live host."""

from __future__ import annotations

import json
import os
import subprocess

from agent_dispatch.pool_source_snapshot import read_supervisor_declarations
from agent_dispatch.registrar_discovery import RegistrarSources, add_pointer
from tests import test_pool_publication_custody as custody
from tests.test_pool_placement_client import connected
from tests.test_registrar_standing import _document
from tests.test_standing_charter_exposure import CONTROL, app

owner = custody.owner
NAMESPACE = "repo:harness"
REPO = "example/harness"


def test_committed_source_enrolls_and_retires_but_dirty_or_rewritten_source_cannot(
    owner, tmp_path, monkeypatch, caplog,
):
    daemon, queue, _ = owner
    root = tmp_path / "harness"
    root.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args):
        return subprocess.run(
            [
                "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false", *args,
            ],
            cwd=root, env=environment, check=True, capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    git("init", "-q", "--template=", "-b", "main")
    declaration = root / ".copilot-extensions" / "agent-dispatch" / "registrar" / "dispatcher-primary.json"
    declaration.parent.mkdir(parents=True)
    original = json.dumps(_document()) + "\n"
    declaration.write_text(original, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "Declare standing charter")
    first_revision = git("rev-parse", "HEAD")

    monkeypatch.setattr(
        "agent_dispatch.registrar_discovery.dispatch_install_dir", lambda: tmp_path / "install",
    )
    monkeypatch.setattr(
        "agent_dispatch.configuration_layers_adapter.configuration_report", lambda _root: None,
    )
    base = tmp_path / "registrar-state"
    add_pointer("example", root, kind="repo", base=base)
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "absent", activation_source=lambda: None,
    )
    daemon.declared_source = lambda: read_supervisor_declarations(sources)
    requests, writes = [], []

    with app(queue) as api, connected(api, requests, control=True) as client:
        daemon.client.standing_source_repositories = client.standing_source_repositories
        daemon.client.standing_source_observation = client.standing_source_observation

        def reconcile(*args, **kwargs):
            result = client.reconcile_standing_source_set(*args, **kwargs)
            writes.append(result)
            return result

        daemon.client.reconcile_standing_source_set = reconcile
        assert set(daemon._desired()) == {"declared:repo:example:worker"}
        task_id, = writes[0]["enrolled"]
        before = queue.get(task_id)
        assert before.standing_charter["lifecycle"] == "active"
        assert queue.standing_source_observation(REPO, NAMESPACE) == first_revision
        assert before.status == "queued"
        assert before.owner is None and before.owner_session_id is None
        assert before.requires == ["standing-charter:explicit-attachment"]
        with queue._connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM task_pool_assignments WHERE task_id=?", (task_id,),
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM spawn_reservations WHERE task_id=?", (task_id,),
            ).fetchone()[0] == 0
        finite = queue.create("Finite emitted child", repo=REPO, standing_parent_id=task_id)
        claimed = queue.claim_one("fixture-worker", repo=REPO)
        assert claimed.id == finite.id
        queue.start(finite.id, "fixture-worker")
        assert queue.complete(finite.id, "fixture-worker").status == "completed"
        assert queue.get(task_id) == before

        daemon._desired()
        assert writes[-1]["unchanged"] == [task_id]
        assert queue.get(task_id) == before
        write_count = len(writes)

        declaration.write_text(json.dumps(_document(title="Dirty edit")) + "\n", encoding="utf-8")
        daemon._desired()
        assert len(writes) == write_count
        assert queue.get(task_id) == before
        assert queue.standing_source_observation(REPO, NAMESPACE) == first_revision
        declaration.write_text(original, encoding="utf-8")

        untracked = declaration.with_name("secondary.json")
        untracked.write_text(
            json.dumps(_document(name="secondary", spec={"lane_key": "secondary"})) + "\n",
            encoding="utf-8",
        )
        daemon._desired()
        assert len(writes) == write_count
        assert queue.get(task_id) == before
        assert queue.standing_source_observation(REPO, NAMESPACE) == first_revision
        untracked.unlink()

        declaration.unlink()
        git("add", "-A")
        git("commit", "-qm", "Remove standing charter")
        removed_revision = git("rev-parse", "HEAD")
        daemon._desired()
        assert writes[-1]["retired"] == [{"id": task_id, "reason": "removed"}]
        assert queue.get(task_id).standing_charter["lifecycle"] == "retiring"
        assert queue.standing_source_observation(REPO, NAMESPACE) == removed_revision

        write_count = len(writes)
        rewritten_revision = git("commit-tree", "HEAD^{tree}", "-m", "Unrelated history")
        git("update-ref", "HEAD", rewritten_revision)
        daemon._desired()
        observation, = sources.last_report.source_observations
        assert observation.revision_state == "ordered"
        assert observation.revision == rewritten_revision
        assert len(writes) == write_count
        assert queue.standing_source_observation(REPO, NAMESPACE) == removed_revision
        assert "source-revision-not-forward" in caplog.text

        mutations = [request for request in requests if request.method == "POST"]
        assert len(mutations) == len(writes)
        assert all(
            request.headers.get(key) == value
            for request in mutations for key, value in CONTROL.items()
        )
