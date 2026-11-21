"""Strict all-project authorization and nonblocking tracking-write admission."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from agent_procutil import no_window_kwargs
from agent_worktrees import config as cfg
from agent_worktrees import installer, pr_authority, pr_ops, pr_publish, tracking
from agent_worktrees.tracking_lifecycle import create_new_record

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.rewrite_authority")
AuthorityState = tuple[cfg.Config, tracking.WorktreeRecord, Path, Path, dict[str, Path]]


def pr_entry(record: tracking.WorktreeRecord) -> tracking.PRRecord:
    pr = record.pr
    assert pr is not None
    return pr


@pytest.fixture
def authority_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AuthorityState:
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
    record = create_new_record(
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


def test_pr_authority_full_destination_and_project_aliases(authority_state: AuthorityState) -> None:
    config, record, root, registry, ledgers = authority_state
    # An alias may use the exact same worktree ID without sharing its authority.
    other = create_new_record(
        "owner", "worktree/owner", "elsewhere", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(pr_entry(record), rewrite_identity="another-host/org/repo")]
    tracking.save_record(other)
    with pr_authority.guard():
        pr_authority.assert_exclusive(config, record, pr_entry(record))
    pr_entry(other).rewrite_identity = pr_entry(record).rewrite_identity
    pr_entry(other).pr_revision += 1
    tracking.save_record(other)
    with pr_authority.guard(), pytest.raises(ValueError, match="destination|Another worktree"):
        pr_authority.assert_exclusive(config, record, pr_entry(record))


@pytest.mark.parametrize("defect", [
    "registry-shape", "registry-version", "registry-duplicate", "registry-entry",
    "record-shape", "record-prs", "record-state", "record-duplicate", "ambiguous",
])
def test_pr_authority_refuses_uncertain_state(authority_state: AuthorityState, defect: str) -> None:
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
        pr_authority.assert_exclusive(config, record, pr_entry(record))


def test_publication_decorator_uses_selected_configuration(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import publication_deadline

    config, _record, _root, _registry, _ledgers = authority_state
    config = replace(config, repos={config.repo_name: cfg.RepoConfig(
        anchor="unused", worktree_root="unused", pr=cfg.PRConfig(push_timeout_seconds=600),
    )})
    calls: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def guard(**kwargs: Any) -> Iterator[None]:
        calls.append(kwargs)
        yield

    monkeypatch.setattr(pr_authority, "guard", guard)

    @pr_authority.publication
    def publish(value: str, *, config: cfg.Config) -> str:
        return value

    assert publish("published", config=config) == "published"
    assert calls == [{"timeout": publication_deadline.lock_wait(600)}]


def test_publication_lock_revalidates_exact_sibling_before_mutation(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    config, record, _root, _registry, _ledgers = authority_state
    sibling = replace(pr_entry(record), pr_id="sibling", branch="pr/sibling", opened_at="2000")
    pr_entry(record).opened_at = "2100"
    record.prs.append(sibling)
    tracking.save_record(record)
    selected = replace(sibling)
    sibling.head_sha = "b" * 40
    tracking.save_record(record)
    released: list[bool] = []

    class Finalization:
        def acquire(self) -> None:
            pass
        def release(self) -> None:
            released.append(True)

    lock = pr_authority.PublicationLock(Finalization(), record, selected, timeout=1)
    with pytest.raises(ValueError, match="authority changed"):
        lock.acquire()
    assert released == [True]
    assert lock.authority is None


def test_legacy_provider_backfill_persists_verified_publication(authority_state: AuthorityState) -> None:
    config, record, _root, _registry, _ledgers = authority_state
    config = replace(config, repos={config.repo_name: cfg.RepoConfig(
        anchor="unused", worktree_root="unused", pr=cfg.PRConfig(provider="github"),
    )})
    expected = replace(pr_entry(record))
    pr_entry(record).provider = "github"
    pr_entry(record).head_sha = "b" * 40
    pr_publish.persist_publication(config, record, pr_entry(record), expected=expected)
    fresh = tracking.load_record(record.yaml_path)
    assert pr_entry(fresh).head_sha == "b" * 40
    assert pr_entry(fresh).provider == "github"


def test_registration_reads_registry_under_authority_guard(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    _config, _record, root, _registry, _ledgers = authority_state
    repo = root.parent / "repository"
    repo.mkdir()
    original = installer.read_projects_registry
    reads: list[bool] = []

    def guarded_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert pr_authority._held.paths
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(installer, "read_projects_registry", guarded_read)
    installer.register_project("new-project", repo, "main")
    assert reads
    assert "new-project" in original()["projects"]


def test_concurrent_project_registration_preserves_both_ledgers(authority_state: AuthorityState) -> None:
    _config, _record, root, registry, _ledgers = authority_state
    repo = root.parent / "repository"
    repo.mkdir()
    script = """
import sys,time
from pathlib import Path
from agent_worktrees import config as cfg,installer
assert sys.flags.utf8_mode == 1
cfg.install_dir=lambda:Path(sys.argv[1])
installer.projects_yaml_path=lambda:Path(sys.argv[2])
read=installer.read_projects_registry
def delayed_read(*a,**k):
    result=read(*a,**k)
    time.sleep(0.2)
    return result
installer.read_projects_registry=delayed_read
installer.register_project(sys.argv[3], Path(sys.argv[4]), 'main')
"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "0"
    env["PYTHONPATH"] = str(Path(pr_publish.__file__).parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    children = [subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", script, str(root), str(registry), name, str(repo)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8",
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
    ) for name in ("first-project", "second-project")]
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stdout + stderr
    assert {"first-project", "second-project"} <= set(installer.read_projects_registry()["projects"])


def test_registry_install_manifest_git_probes_remain_headless(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import _command_binding

    _config, _record, root, _registry, _ledgers = authority_state
    monkeypatch.setattr(installer, "install_dir", lambda: root)
    monkeypatch.setattr(installer, "find_package_source", lambda *a: root / "plugin" / "src" / "agent_worktrees")
    monkeypatch.setattr(installer, "_source_kind", lambda *a: "local")
    monkeypatch.setattr(installer, "_payload_root", lambda *a: root / "missing-payload")
    observed: list[list[str]] = []
    manifests: list[dict[str, Any]] = []

    def publish(_primitive: Any, manifest: dict[str, Any], **kwargs: Any) -> dict[str, str]:
        manifests.append(manifest)
        return {"status": "unresolved", "reason": "isolated headless-probe fixture"}

    monkeypatch.setattr(_command_binding, "write_legacy_manifest", publish)
    monkeypatch.setenv("GIT_DIR", str(root / "another-repository" / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root / "another-repository"))

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        environment = kwargs.pop("env")
        assert "GIT_DIR" not in environment
        assert "GIT_WORK_TREE" not in environment
        assert kwargs == {"capture_output": True, "text": True, **no_window_kwargs()}
        observed.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    installer.write_deploy_manifest(root, "example-machine")
    assert len(observed) == 3
    assert len(manifests) == 1 and manifests[0]["source"]["kind"] == "local"


def test_doctor_repairs_hold_full_registry_authority(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import doctor

    calls: list[tuple[bool, bool]] = []

    def reconcile(*, fix: bool = False, plat: str | None = None) -> list[object]:
        calls.append((fix, bool(getattr(pr_authority._held, "paths", ()))))
        return []

    monkeypatch.setattr(doctor, "_reconcile", reconcile)
    assert doctor.reconcile(fix=True) == []
    assert doctor.reconcile() == []
    assert calls == [(True, True), (False, False)]


def test_unregister_preserves_legacy_active_pr(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import unregister_cli

    config, record, _root, _registry, _ledgers = authority_state
    pr_entry(record).state = "active"
    monkeypatch.setattr(tracking, "list_records", lambda *a, **k: [record])
    assert "1 open PR(s)" in unregister_cli._unregister_blockers(config.repo_name)


@pytest.mark.parametrize("status,expected", [
    ("active", "open"), ("completed", "merged"), ("abandoned", "closed"), ("future", None),
])
def test_azure_head_lookup_uses_canonical_lifecycle(monkeypatch: pytest.MonkeyPatch, status: str, expected: str | None) -> None:
    from agent_worktrees.providers import azure_devops
    from agent_worktrees.providers.base import ProviderError

    monkeypatch.setattr(azure_devops, "run_cli", lambda *a, **k: subprocess.CompletedProcess(
        ["az"], 0, stdout=json.dumps([{"status": status, "pullRequestId": 42}]), stderr="",
    ))
    provider = azure_devops.AzureDevOpsProvider()
    if expected is None:
        with pytest.raises(ProviderError):
            provider.find_pull_by_head("example/project", "feature", api_base="https://dev.azure.com/example")
    else:
        observed = provider.find_pull_by_head(
            "example/project", "feature", api_base="https://dev.azure.com/example",
        )
        assert observed is not None and observed.state == expected


def test_terminal_reconcile_preserves_concurrent_attribution_stamp(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import finalize_open_pr_gate, providers
    from agent_worktrees.providers.base import PullResult

    config, record, _root, _registry, _ledgers = authority_state
    config = replace(config, repos={config.repo_name: cfg.RepoConfig(
        anchor="unused", worktree_root="unused", pr=cfg.PRConfig(provider="github"),
    )})
    pr_entry(record).number = 42
    tracking.save_record(record)
    stale = tracking.load_record(record.yaml_path)

    class Provider:
        def get_pull(self, *args: Any, **kwargs: Any) -> PullResult:
            fresh = tracking.load_record(record.yaml_path)
            pr_entry(fresh).attribution_head = "b" * 40
            pr_entry(fresh).pr_revision += 1
            tracking.save_record(fresh)
            return PullResult(state="merged", merged=True)

    monkeypatch.setattr(finalize_open_pr_gate, "trusted_target", lambda *a: (Provider(), "org/repo"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)
    pr_ops._reconcile_active_pr(stale, config)
    fresh = tracking.load_record(record.yaml_path)
    assert pr_entry(fresh).state == pr_entry(stale).state == "merged"
    assert pr_entry(fresh).attribution_head == "b" * 40


@pytest.mark.parametrize("operation", ["stat", "iterdir"])
def test_pr_authority_refuses_unreadable_ledger(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    config, record, root, registry, ledgers = authority_state
    original = getattr(Path, operation)

    def denied(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == ledgers["alias"]:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, denied)
    with pr_authority.guard(), pytest.raises(ValueError, match="Cannot enumerate|Cannot inspect"):
        pr_authority.assert_exclusive(config, record, pr_entry(record))


@pytest.mark.parametrize("operation", ["stat", "iterdir"])
def test_recovery_audits_unavailable_historical_ledger(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    config, record, _root, _registry, ledgers = authority_state
    original = getattr(Path, operation)

    def denied(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == ledgers["alias"]:
            raise PermissionError("historical ledger unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, denied)
    with pr_authority.guard():
        waived = pr_authority.assert_exclusive(
            config, record, pr_entry(record), recovery=True,
            destination_identity=pr_entry(record).rewrite_identity,
        )
    assert any("Historical ownership ledger unavailable" in item for item in waived)


@pytest.mark.parametrize("case", ["same-pr", "other-pr", "ambiguous", "malformed-registry"])
def test_recovery_distinguishes_history_from_shared_destination(authority_state: AuthorityState, case: str) -> None:
    config, record, _root, registry, ledgers = authority_state
    pr_entry(record).number, pr_entry(record).provider = 42, "github"
    tracking.save_record(record)
    other = create_new_record(
        "other", "worktree/other", "unavailable", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(pr_entry(record), pr_id="other-pr", number=42 if case == "same-pr" else 43)]
    if case == "ambiguous":
        pr_entry(other).rewrite_identity = ""
        pr_entry(other).head_identity = ""
    tracking.save_record(other)
    if case == "malformed-registry":
        registry.write_text("projects: []")
    with pr_authority.guard():
        if case == "other-pr":
            with pytest.raises(ValueError, match="shared history"):
                pr_authority.assert_exclusive(
                    config, record, pr_entry(record), recovery=True,
                    destination_identity=pr_entry(record).rewrite_identity,
                )
        else:
            waived = pr_authority.assert_exclusive(
                config, record, pr_entry(record), recovery=True,
                destination_identity=pr_entry(record).rewrite_identity,
            )
            assert waived


def run_writer(root: Path, ledger: Path, wid: str, operation: str) -> dict[str, Any]:
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
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    response: dict[str, Any] = json.loads(result.stdout)
    return response


@pytest.fixture
def competing_record(authority_state: AuthorityState) -> tracking.WorktreeRecord:
    _config, record, _root, _registry, ledgers = authority_state
    other = create_new_record(
        "other", "worktree/other", "elsewhere", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(pr_entry(record), branch="pr/other-aaaa", pr_id="other-pr")]
    tracking.save_record(other)
    return other


@pytest.mark.parametrize("operation", ["set", "save", "locked", "registry"])
def test_pr_authority_cross_process_writers(
    authority_state: AuthorityState, competing_record: tracking.WorktreeRecord, operation: str,
) -> None:
    config, record, root, _registry, ledgers = authority_state
    with pr_authority.guard():
        pr_authority.assert_exclusive(config, record, pr_entry(record))
        result = run_writer(root, ledgers["alias"], "other", operation)
        assert not result["success"] and "timed out" in result["error"].lower()
        assert pr_entry(tracking.load_record(competing_record.yaml_path)).branch == "pr/other-aaaa"


def test_pr_authority_cross_process_unrelated_stamps(
    authority_state: AuthorityState, competing_record: tracking.WorktreeRecord,
) -> None:
    config, record, root, _registry, ledgers = authority_state
    foreign_ledger = root.parent / "foreign-ledger"
    foreign_ledger.mkdir()
    foreign = create_new_record(
        "foreign", "worktree/foreign", "elsewhere", "foreign", "test", "linux", foreign_ledger,
    )
    foreign.prs = [replace(pr_entry(competing_record), pr_id="foreign-pr")]
    tracking.save_record(foreign)
    with pr_authority.guard():
        pr_authority.assert_exclusive(config, record, pr_entry(record))
        assert run_writer(root, ledgers["alias"], "other", "stamp")["success"]
        assert pr_entry(tracking.load_record(competing_record.yaml_path)).branch == "pr/other-aaaa"
        assert run_writer(root.parent / "another-installation", foreign_ledger, "foreign", "save")["success"]


def test_pr_authority_cross_process_writer_after_release(
    authority_state: AuthorityState, competing_record: tracking.WorktreeRecord,
) -> None:
    config, record, root, _registry, ledgers = authority_state
    with pr_authority.guard():
        pr_authority.assert_exclusive(config, record, pr_entry(record))
    assert run_writer(root, ledgers["alias"], "other", "set")["success"]
    with pr_authority.guard(), pytest.raises(ValueError, match="destination|Another worktree"):
        pr_authority.assert_exclusive(config, record, pr_entry(record))


def test_publication_generation_fresh_rmw_and_stale_refusal(authority_state: AuthorityState) -> None:
    config, record, root, registry, ledgers = authority_state
    stale = tracking.load_record(record.yaml_path)
    competing_publisher = tracking.load_record(record.yaml_path)
    pr_entry(record).head_sha = "b" * 40
    pr_publish.persist_publication(config, record, pr_entry(record))
    current = tracking.load_record(record.yaml_path)
    assert pr_entry(current).head_sha == "b" * 40
    assert pr_entry(current).pr_revision == pr_entry(stale).pr_revision + 1
    tracking.save_record(stale)
    assert pr_entry(tracking.load_record(record.yaml_path)).head_sha == "b" * 40
    pr_entry(competing_publisher).head_sha = "c" * 40
    with pytest.raises(ValueError, match="tracking authority changed"):
        pr_publish.persist_publication(config, competing_publisher, pr_entry(competing_publisher))
    assert pr_entry(tracking.load_record(record.yaml_path)).head_sha == "b" * 40


def test_pr_authority_legacy_candidate_uses_its_project_remote(authority_state: AuthorityState, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config, record, root, registry, ledgers = authority_state
    checkout = tmp_path / "legacy-checkout"
    checkout.mkdir()
    other = create_new_record(
        "other", "worktree/other", str(checkout), "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(pr_entry(record), pr_id="legacy", rewrite_identity="", rewrite_owner="")]
    tracking.save_record(other)
    project = tmp_path / "alias-config"
    project.mkdir()
    (project / "config.yaml").write_text("remote: upstream\n")
    alias = replace(config, repo_name="alias", repos={"alias": cfg.RepoConfig(
        anchor=str(checkout), worktree_root=str(tmp_path), remote="upstream",
    )})
    monkeypatch.setattr(cfg, "project_dir", lambda name: project)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: alias)
    observed: list[str] = []
    def identity(remote: str, **kwargs: Any) -> str:
        observed.append(remote)
        return pr_entry(record).rewrite_identity if remote == "upstream" else "different/repo"
    monkeypatch.setattr(pr_publish, "push_identity", identity)
    with pr_authority.guard(), pytest.raises(ValueError, match="Another worktree"):
        pr_authority.assert_exclusive(config, record, pr_entry(record))
    assert observed == ["upstream"]


@pytest.mark.parametrize("state,conflicts", [("active", True), ("completed", False)])
def test_pr_authority_native_provider_states(authority_state: AuthorityState, state: str, conflicts: bool) -> None:
    config, record, root, registry, ledgers = authority_state
    other = create_new_record(
        "other", "worktree/other", "elsewhere", "alias", "test", "linux", ledgers["alias"],
    )
    other.prs = [replace(pr_entry(record), pr_id="other", state=state)]
    tracking.save_record(other)
    with pr_authority.guard():
        if conflicts:
            with pytest.raises(ValueError, match="Another worktree"):
                pr_authority.assert_exclusive(config, record, pr_entry(record))
        else:
            pr_authority.assert_exclusive(config, record, pr_entry(record))


@pytest.mark.parametrize("writer", ["set-pr", "save", "locked-without-handoff-merge"])
def test_state_correction_generation_survives_stale_save(authority_state: AuthorityState, writer: str) -> None:
    config, record, root, registry, ledgers = authority_state
    stale = tracking.load_record(record.yaml_path)
    if writer == "set-pr":
        assert pr_ops.set_pr(record.worktree_id, config=config, state="closed")["success"]
    else:
        fresh = tracking.load_record(record.yaml_path)
        pr_entry(fresh).state = "closed"
        if writer == "save":
            tracking.save_record(fresh)
        else:
            with tracking._RecordLock(fresh.yaml_path, require_sidecar=True):
                tracking._save_record_unlocked(fresh, preserve_handoff_reservations=False)
    closed = tracking.load_record(record.yaml_path)
    assert pr_entry(closed).state == "closed" and pr_entry(closed).pr_revision > pr_entry(stale).pr_revision
    stale.title = "unrelated stamp"
    tracking.save_record(stale)
    assert pr_entry(tracking.load_record(record.yaml_path)).state == "closed"
