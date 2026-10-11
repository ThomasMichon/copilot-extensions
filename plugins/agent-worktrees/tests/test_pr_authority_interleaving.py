"""Authority transitions survive attribution-only stamps and CLI contention."""

from contextlib import contextmanager
import json

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg, finalize_cli, pr_authority, tracking, pr_ops, providers, finalize_open_pr_gate
from agent_worktrees.providers.base import PullResult

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.authority_interleaving")


@pytest.mark.parametrize("state", ["merged", "closed"])
@pytest.mark.parametrize("pr_id", ["", "stable"])
def test_authority_transition_survives_newer_attribution_stamp(tmp_path, state, pr_id):
    rec = tracking.create_new_record("test", "worktree/test", str(tmp_path), "ext", "test", "linux", tmp_path)
    rec.prs = [tracking.PRRecord(state="open", branch="pr/test-aaaa", repo="example/project", number=42, pr_id=pr_id)]
    tracking.save_record(rec)
    transition = tracking.load_record(rec.yaml_path)
    stamp = tracking.load_record(rec.yaml_path)
    tracking.stamp_frozen_attribution(stamp.pr, attribution=False, explicit=True, assign_pr_id=False)
    tracking.save_record(stamp)
    transition.pr.state = state
    tracking.save_record(transition)
    current = tracking.load_record(rec.yaml_path)
    assert current.prs[-1].state == state
    assert current.prs[-1].attribution_mode == "false"
    assert current.prs[-1].pr_revision > stamp.pr.pr_revision
    stamp.title = "stale attribution writer"
    tracking.save_record(stamp)
    assert tracking.load_record(rec.yaml_path).prs[-1].state == state


@pytest.mark.parametrize("json_mode", [False, True])
def test_create_pr_contention_public_cli_error(pr_repo, monkeypatch, capfd, json_mode):
    config, wid, wt, remote = pr_repo
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.chdir(wt)

    @contextmanager
    def busy(**kwargs):
        raise TimeoutError("Timed out waiting for PR rewrite authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    args = cli.build_parser().parse_args(["create-pr", wid, "--title", "Feature"] + (["--json"] if json_mode else []))
    assert finalize_cli.cmd_create_pr(args) == 1
    out, err = capfd.readouterr()
    assert "Traceback" not in out + err
    if json_mode:
        assert "Timed out" in json.loads(out)["error"]
    else:
        assert "Timed out" in out + err


def test_fresh_reassociation_survives_but_stale_writer_cannot_restore_prior_pr(tmp_path):
    rec = tracking.create_new_record("test", "worktree/test", str(tmp_path), "ext", "test", "linux", tmp_path)
    rec.prs = [tracking.PRRecord(state="open", branch="pr/test-aaaa",
                                 repo="example/project", number=42, pr_revision=1)]
    tracking.save_record(rec)
    stale = tracking.load_record(rec.yaml_path)
    fresh = tracking.load_record(rec.yaml_path)
    before = fresh.pr.pr_revision
    fresh.pr.number = 43
    tracking.save_record(fresh)
    current = tracking.load_record(rec.yaml_path)
    assert current.pr.number == 43
    assert current.pr.pr_revision > before
    stale.title = "unrelated stale update"
    tracking.save_record(stale)
    assert tracking.load_record(rec.yaml_path).pr.number == 43


def test_terminal_reconcile_preserves_concurrent_attribution_stamp(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    assert pr_ops.create_pr(wid, config, title="Feature")["success"]
    path = cfg.tracking_dir() / f"{wid}.yaml"
    record = tracking.load_record(path)
    record.pr.number = 42
    tracking.save_record(record)
    stale = tracking.load_record(path)

    class Provider:
        def get_pull(self, *args, **kwargs):
            fresh = tracking.load_record(path)
            fresh.pr.attribution_head = "b" * 40
            fresh.pr.pr_revision += 1
            tracking.save_record(fresh)
            return PullResult(state="merged", merged=True)

    monkeypatch.setattr(finalize_open_pr_gate, "trusted_target", lambda *a: (Provider(), "org/repo"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)
    pr_ops._reconcile_active_pr(stale, config)
    fresh = tracking.load_record(path)
    assert fresh.pr.state == stale.pr.state == "merged"
    assert fresh.pr.attribution_head == "b" * 40


def test_legacy_id_backfill_attribution_write_does_not_take_authority(tmp_path, monkeypatch):
    import yaml
    from agent_worktrees import pr_publication_state

    rec = tracking.create_new_record("legacy", "worktree/legacy", str(tmp_path), "ext", "test", "linux", tmp_path)
    rec.prs = [tracking.PRRecord(state="open", branch="pr/test-aaaa", repo="example/project", number=42)]
    tracking.save_record(rec)
    data = yaml.safe_load(rec.yaml_path.read_text())
    data["prs"][0].pop("pr_id", None)
    rec.yaml_path.write_text(yaml.safe_dump(data))
    legacy = tracking.load_record(rec.yaml_path)
    before = pr_publication_state.authority(legacy.pr)
    tracking.stamp_frozen_attribution(legacy.pr, attribution=False, explicit=True, assign_pr_id=False)

    @contextmanager
    def forbidden(**kwargs):
        pytest.fail("attribution-only legacy backfill acquired publication authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", forbidden)
    tracking.save_record(legacy)
    current = tracking.load_record(rec.yaml_path)
    assert current.pr.pr_id
    assert current.pr.attribution_mode == "false"
    assert pr_publication_state.authority(current.pr) == before


def test_unregister_checks_blockers_only_after_authority_admission(monkeypatch):
    from types import SimpleNamespace
    from agent_worktrees import unregister_cli

    state = {"held": False, "publication": False, "locks": []}

    @contextmanager
    def binstub(name):
        state["locks"].append(name)
        try:
            yield
        finally:
            state["locks"].pop()

    @contextmanager
    def admit(**kwargs):
        assert state["locks"] == ["__registries__", "example"]
        state.update(held=True, publication=True)
        try:
            yield
        finally:
            state["held"] = False

    def blockers(project):
        assert state["held"] and state["publication"]
        return ["1 open PR(s)"]

    monkeypatch.setattr(pr_authority, "guard", admit)
    monkeypatch.setattr(unregister_cli.inst, "_binstub_lock", binstub)
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    monkeypatch.setattr(unregister_cli, "_unregister_blockers", blockers)
    monkeypatch.setattr(unregister_cli.inst, "remove_project_binstub", lambda *a: pytest.fail("removed blocked project"))
    assert unregister_cli.cmd_unregister(SimpleNamespace(force=False, keep_repo_entry=True)) == 1
    assert not state["held"]


def test_copy_pr_preserves_destination_loaded_snapshot():
    from agent_worktrees import pr_publication_state as state

    destination = tracking.PRRecord(state="open", _loaded_authority=("destination",))
    source = tracking.PRRecord(state="merged", _loaded_authority=("source",))
    state.copy_pr(destination, source)
    assert destination.state == "merged"
    assert destination._loaded_authority == ("destination",)


def test_unregister_blockers_use_named_ledger(tmp_path, monkeypatch):
    from agent_worktrees import unregister_cli

    selected = tmp_path / "selected"
    ambient = tmp_path / "ambient"
    selected.mkdir()
    ambient.mkdir()
    rec = tracking.create_new_record("selected", "worktree/selected", str(selected), "selected", "test", "linux", selected)
    rec.prs = [tracking.PRRecord(state="active", branch="pr/example", number=42)]
    tracking.save_record(rec)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: selected if name == "selected" else ambient)
    assert "1 open PR(s)" in unregister_cli._unregister_blockers("selected")
    assert unregister_cli._unregister_blockers("ambient") == []


@pytest.mark.parametrize("field,value", [
    ("worktree_path", "relocated"), ("repo", "other"), ("branch", "worktree/other"),
])
def test_publication_revalidates_owning_record_after_wait(tmp_path, field, value):
    from agent_worktrees import pr_publication_state

    rec = tracking.create_new_record("owner", "worktree/owner", str(tmp_path), "ext", "test", "linux", tmp_path)
    rec.prs = [tracking.PRRecord(state="open", branch="pr/example", number=42)]
    tracking.save_record(rec)
    stale = tracking.load_record(rec.yaml_path)
    setattr(rec, field, value)
    tracking.save_record(rec)
    with pytest.raises(ValueError, match="Owning worktree identity changed"):
        pr_publication_state.require_current(stale, stale.active_pr())


@pytest.mark.parametrize("field,value", [
    ("worktree_path", "relocated"), ("repo", "other"), ("branch", "worktree/other"),
])
def test_owner_identity_change_requires_authority_during_publication(tmp_path, monkeypatch, field, value):
    rec = tracking.create_new_record("owner", "worktree/owner", str(tmp_path), "ext", "test", "linux", tmp_path)
    rec.prs = [tracking.PRRecord(state="open", branch="pr/example", number=42)]
    tracking.save_record(rec)
    stale = tracking.load_record(rec.yaml_path)
    setattr(stale, field, value)

    @contextmanager
    def occupied(**kwargs):
        raise TimeoutError("publication owns authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", occupied)
    with pytest.raises(TimeoutError, match="publication owns authority"):
        tracking.save_record(stale)
    current = tracking.load_record(rec.yaml_path)
    assert getattr(current, field) == getattr(rec, field)
