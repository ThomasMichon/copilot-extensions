"""Strict all-project authorization and nonblocking tracking-write admission."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import contextlib

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import installer, pr_authority, pr_ops, pr_publish, tracking

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.rewrite_authority")


@pytest.fixture
def authority_state(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    registry = root / "projects.yaml"
    registry.write_text("schema_version: 2\nprojects:\n  selected: {}\n  alias: {}\n")
    ledgers = {name: tmp_path / name / "worktrees" for name in ("selected", "alias")}
    for path in ledgers.values():
        path.mkdir(parents=True)
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: ledgers[name or "selected"])
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: registry)
    record = tracking.create_new_record(
        "owner", "worktree/owner", str(tmp_path / "checkout"), "selected",
        "test", "linux", ledgers["selected"],
    )
    record.prs = [tracking.PRRecord(
        branch="pr/change-aaaa", state="open", repo="org/repo", head_sha="a" * 40,
        rewrite_identity="host/org/repo", rewrite_owner="owner:pr/change-aaaa",
    )]
    tracking.save_record(record)
    config = cfg.Config(srcroot=str(tmp_path), machine="test", platform="linux", repo_name="selected")
    return config, record, root, registry, ledgers


def test_pr_authority_full_destination_and_project_aliases(authority_state):
    config, record, root, registry, ledgers = authority_state
    # An alias may use the exact same worktree ID without sharing its authority.
    other = tracking.create_new_record(
        "owner", "worktree/owner", "elsewhere", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(record.pr, rewrite_identity="another-host/org/repo")]
    tracking.save_record(other)
    with pr_authority.guard():
        pr_authority.assert_exclusive(config, record, record.pr)
    other.pr.rewrite_identity = record.pr.rewrite_identity
    other.pr.pr_revision += 1
    tracking.save_record(other)
    with pr_authority.guard(), pytest.raises(ValueError, match="destination|Another worktree"):
        pr_authority.assert_exclusive(config, record, record.pr)


@pytest.mark.parametrize("defect", [
    "registry-shape", "registry-version", "registry-duplicate", "registry-entry",
    "record-shape", "record-prs", "record-state", "record-duplicate", "ambiguous",
])
def test_pr_authority_refuses_uncertain_state(authority_state, defect):
    config, record, root, registry, ledgers = authority_state
    malformed = {
        "registry-shape": "projects: []",
        "registry-version": "schema_version: 99\nprojects: {}",
        "registry-duplicate": "projects: {}\nprojects: {}",
        "registry-entry": "projects:\n  alias: null",
        "record-shape": "[]",
        "record-prs": "worktree_id: other\nprs: [null]",
        "record-state": "worktree_id: other\nprs:\n- state: future\n  branch: unrelated",
        "record-duplicate": "worktree_id: other\nprs: []\nprs: []",
        "ambiguous": "worktree_id: other\nprs:\n- state: open\n  branch: pr/change-aaaa",
    }
    target = registry if defect.startswith("registry") else ledgers["alias"] / "other.yaml"
    target.write_text(malformed[defect])
    with pr_authority.guard(), pytest.raises(ValueError):
        pr_authority.assert_exclusive(config, record, record.pr)


def test_publication_decorator_uses_selected_configuration(authority_state, monkeypatch):
    from agent_worktrees import publication_deadline

    config, _record, _root, _registry, _ledgers = authority_state
    config = replace(config, repos={config.repo_name: cfg.RepoConfig(
        anchor="unused", worktree_root="unused", pr=cfg.PRConfig(push_timeout_seconds=600),
    )})
    calls = []

    @contextlib.contextmanager
    def guard(**kwargs):
        calls.append(kwargs)
        yield

    monkeypatch.setattr(pr_authority, "guard", guard)

    @pr_authority.publication
    def publish(value, *, config):
        return value

    assert publish("published", config=config) == "published"
    assert calls == [{"timeout": publication_deadline.lock_wait(600)}]


def test_publication_lock_revalidates_exact_sibling_before_mutation(authority_state, monkeypatch):
    config, record, _root, _registry, _ledgers = authority_state
    sibling = replace(record.pr, pr_id="sibling", branch="pr/sibling", opened_at="2000")
    record.pr.opened_at = "2100"
    record.prs.append(sibling)
    tracking.save_record(record)
    selected = replace(sibling)
    sibling.head_sha = "b" * 40
    tracking.save_record(record)
    released = []

    class Finalization:
        def acquire(self):
            pass
        def release(self):
            released.append(True)

    lock = pr_authority.PublicationLock(Finalization(), record, selected, timeout=1)
    with pytest.raises(ValueError, match="authority changed"):
        lock.acquire()
    assert released == [True]
    assert lock.authority is None


def test_legacy_provider_backfill_persists_verified_publication(authority_state):
    config, record, _root, _registry, _ledgers = authority_state
    config = replace(config, repos={config.repo_name: cfg.RepoConfig(
        anchor="unused", worktree_root="unused", pr=cfg.PRConfig(provider="github"),
    )})
    expected = replace(record.pr)
    record.pr.provider = "github"
    record.pr.head_sha = "b" * 40
    pr_publish.persist_publication(config, record, record.pr, expected=expected)
    fresh = tracking.load_record(record.yaml_path)
    assert fresh.pr.head_sha == "b" * 40
    assert fresh.pr.provider == "github"


def test_registration_reads_registry_under_authority_guard(authority_state, monkeypatch):
    _config, _record, _root, _registry, _ledgers = authority_state
    original = installer.read_projects_registry
    reads = []

    def guarded_read(*args, **kwargs):
        assert pr_authority._held.paths
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(installer, "read_projects_registry", guarded_read)
    installer.register_project("new-project", "unused", "main")
    assert reads
    assert "new-project" in original()["projects"]


def test_concurrent_project_registration_preserves_both_ledgers(authority_state):
    _config, _record, root, registry, _ledgers = authority_state
    script = """
import sys,time
from pathlib import Path
from agent_worktrees import config as cfg,installer
cfg.install_dir=lambda:Path(sys.argv[1])
installer.projects_yaml_path=lambda:Path(sys.argv[2])
read=installer.read_projects_registry
def delayed_read(*a,**k):
    result=read(*a,**k)
    time.sleep(0.2)
    return result
installer.read_projects_registry=delayed_read
installer.register_project(sys.argv[3], 'unused', 'main')
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(pr_publish.__file__).parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    children = [subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(registry), name],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ) for name in ("first-project", "second-project")]
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stdout + stderr
    assert {"first-project", "second-project"} <= set(installer.read_projects_registry()["projects"])


def test_registry_install_manifest_git_probes_remain_headless(authority_state, monkeypatch):
    _config, _record, root, _registry, _ledgers = authority_state
    monkeypatch.setattr(installer, "install_dir", lambda: root)
    monkeypatch.setattr(installer, "find_package_source", lambda *a: root / "plugin" / "src" / "agent_worktrees")
    monkeypatch.setattr(installer, "_source_kind", lambda *a: "local")
    observed = []

    def run(args, **kwargs):
        assert kwargs == {"capture_output": True, "text": True, **installer.no_window_kwargs()}
        observed.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", run)
    installer.write_deploy_manifest(root, "example-machine")
    assert len(observed) == 3


@pytest.mark.parametrize("operation", ["stat", "iterdir"])
def test_pr_authority_refuses_unreadable_ledger(authority_state, monkeypatch, operation):
    config, record, root, registry, ledgers = authority_state
    original = getattr(Path, operation)

    def denied(path, *args, **kwargs):
        if path == ledgers["alias"]:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, denied)
    with pr_authority.guard(), pytest.raises(ValueError, match="Cannot enumerate|Cannot inspect"):
        pr_authority.assert_exclusive(config, record, record.pr)


def run_writer(root, ledger, wid, operation):
    script = """
import json,sys
from pathlib import Path
from agent_worktrees import config as cfg,installer,pr_ops,pr_publish,tracking
cfg.install_dir=lambda:Path(sys.argv[1])
cfg.tracking_dir=lambda *a:Path(sys.argv[2])
pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S=0.1
wid,operation=sys.argv[3:]
if operation=='set':
    result=pr_ops.set_pr(wid,branch='pr/change-aaaa',state='open')
else:
    rec=tracking.load_record(cfg.tracking_dir()/f'{wid}.yaml')
    if operation=='stamp': rec.title='unrelated stamp'
    else: rec.pr.branch='pr/change-aaaa'
    try:
        if operation=='locked':
            with tracking._RecordLock(rec.yaml_path,require_sidecar=True):
                tracking._save_record_unlocked(rec)
        elif operation=='registry':
            installer.write_projects_registry({'projects':{'alias':{}}},Path(sys.argv[1])/'projects.yaml')
        else: tracking.save_record(rec)
        result={'success':True}
    except TimeoutError as exc:
        result={'success':False,'error':str(exc)}
print(json.dumps(result))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(pr_publish.__file__).parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(ledger), wid, operation],
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_pr_authority_cross_process_writers_and_stamps(authority_state):
    config, record, root, registry, ledgers = authority_state
    other = tracking.create_new_record(
        "other", "worktree/other", "elsewhere", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(record.pr, branch="pr/other-aaaa", pr_id="other-pr")]
    tracking.save_record(other)
    foreign_ledger = root.parent / "foreign-ledger"
    foreign_ledger.mkdir()
    foreign = tracking.create_new_record(
        "foreign", "worktree/foreign", "elsewhere", "foreign", "test", "linux", foreign_ledger,
    )
    foreign.prs = [replace(other.pr, pr_id="foreign-pr")]
    tracking.save_record(foreign)
    with pr_authority.guard():
        pr_authority.assert_exclusive(config, record, record.pr)
        for operation in ("set", "save", "locked", "registry"):
            result = run_writer(root, ledgers["alias"], "other", operation)
            assert not result["success"] and "timed out" in result["error"].lower()
        assert run_writer(root, ledgers["alias"], "other", "stamp")["success"]
        assert tracking.load_record(other.yaml_path).pr.branch == "pr/other-aaaa"
        assert run_writer(root.parent / "another-installation", foreign_ledger, "foreign", "save")["success"]
    assert run_writer(root, ledgers["alias"], "other", "set")["success"]
    with pr_authority.guard(), pytest.raises(ValueError, match="destination|Another worktree"):
        pr_authority.assert_exclusive(config, record, record.pr)


def test_publication_generation_fresh_rmw_and_stale_refusal(authority_state):
    config, record, root, registry, ledgers = authority_state
    stale = tracking.load_record(record.yaml_path)
    competing_publisher = tracking.load_record(record.yaml_path)
    record.pr.head_sha = "b" * 40
    pr_publish.persist_publication(config, record, record.pr)
    current = tracking.load_record(record.yaml_path)
    assert current.pr.head_sha == "b" * 40
    assert current.pr.pr_revision == stale.pr.pr_revision + 1
    tracking.save_record(stale)
    assert tracking.load_record(record.yaml_path).pr.head_sha == "b" * 40
    competing_publisher.pr.head_sha = "c" * 40
    with pytest.raises(ValueError, match="tracking authority changed"):
        pr_publish.persist_publication(config, competing_publisher, competing_publisher.pr)
    assert tracking.load_record(record.yaml_path).pr.head_sha == "b" * 40


def test_pr_authority_legacy_candidate_uses_its_project_remote(authority_state, monkeypatch, tmp_path):
    config, record, root, registry, ledgers = authority_state
    checkout = tmp_path / "legacy-checkout"
    checkout.mkdir()
    other = tracking.create_new_record(
        "other", "worktree/other", str(checkout), "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(record.pr, pr_id="legacy", rewrite_identity="", rewrite_owner="")]
    tracking.save_record(other)
    project = tmp_path / "alias-config"
    project.mkdir()
    (project / "config.yaml").write_text("remote: upstream\n")
    alias = replace(config, repo_name="alias", repos={"alias": cfg.RepoConfig(
        anchor=str(checkout), worktree_root=str(tmp_path), remote="upstream",
    )})
    monkeypatch.setattr(cfg, "project_dir", lambda name: project)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: alias)
    observed = []
    monkeypatch.setattr(pr_publish, "push_identity",
                        lambda remote, **k: observed.append(remote) or (
                            record.pr.rewrite_identity if remote == "upstream" else "different/repo"))
    with pr_authority.guard(), pytest.raises(ValueError, match="Another worktree"):
        pr_authority.assert_exclusive(config, record, record.pr)
    assert observed == ["upstream"]


@pytest.mark.parametrize("state,conflicts", [("active", True), ("completed", False)])
def test_pr_authority_native_provider_states(authority_state, state, conflicts):
    config, record, root, registry, ledgers = authority_state
    other = tracking.create_new_record(
        "other", "worktree/other", "elsewhere", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(record.pr, pr_id="other", state=state)]
    tracking.save_record(other)
    with pr_authority.guard():
        if conflicts:
            with pytest.raises(ValueError, match="Another worktree"):
                pr_authority.assert_exclusive(config, record, record.pr)
        else:
            pr_authority.assert_exclusive(config, record, record.pr)


@pytest.mark.parametrize("writer", ["set-pr", "save", "locked-without-handoff-merge"])
def test_state_correction_generation_survives_stale_save(authority_state, writer):
    config, record, root, registry, ledgers = authority_state
    stale = tracking.load_record(record.yaml_path)
    if writer == "set-pr":
        assert pr_ops.set_pr(record.worktree_id, config=config, state="closed")["success"]
    else:
        fresh = tracking.load_record(record.yaml_path)
        fresh.pr.state = "closed"
        if writer == "save":
            tracking.save_record(fresh)
        else:
            with tracking._RecordLock(fresh.yaml_path, require_sidecar=True):
                tracking._save_record_unlocked(fresh, preserve_handoff_reservations=False)
    closed = tracking.load_record(record.yaml_path)
    assert closed.pr.state == "closed" and closed.pr.pr_revision > stale.pr.pr_revision
    stale.title = "unrelated stamp"
    tracking.save_record(stale)
    assert tracking.load_record(record.yaml_path).pr.state == "closed"
