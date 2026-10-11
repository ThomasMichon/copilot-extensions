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
