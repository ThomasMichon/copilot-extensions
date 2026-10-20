"""Every publication guard retains physical and execution identity privacy."""

from dataclasses import replace

import pytest
import yaml

from agent_worktrees import config as cfg, finalize, git_ops, pr_ops
from agent_worktrees import pr_incremental_publish, pr_rewrite, tracking
from agent_worktrees.providers.attribution import BranchLeakError
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity


@pytest.mark.parametrize("attribution", [False, "codename"])
@pytest.mark.parametrize("private_name", ["desk", "guest-space"])
@pytest.mark.parametrize("flow", ["create", "snapshot", "refspec", "rewrite"])
def test_publication_guards_reject_both_tuple_names(
    pr_repo, monkeypatch, capsys, attribution, private_name, flow,
):
    config, wid, wt_path, _remote = pr_repo
    repo = replace(config.default_repo, pr=replace(
        config.default_repo.pr, source_attribution=attribution,
    ))
    config = replace(config, machine="guest-space", repos={config.repo_name: repo})
    (wt_path.parent.parent / "machines.yaml").write_text(
        "machines:\n  guest-space: {execution_platform: linux, physical_host: desk}\n",
    )
    path = cfg.tracking_dir() / f"{wid}.yaml"
    document = yaml.safe_load(path.read_text())
    document.update(WorktreeMachineIdentity("desk", "linux", "guest-space").to_record_fields())
    path.write_text(yaml.safe_dump(document))
    record = tracking.load_record(path)
    branch = f"feature/{private_name}-fix-aaaa"
    if flow == "create":
        result = pr_ops.create_pr(wid, config, title="Feature", branch=branch, dry_run=True)
        assert not result["success"]
        error = result["error"]
    else:
        record.prs = [tracking.PRRecord(branch=branch, state="open", head_sha="a" * 40)]
        if flow == "rewrite":
            record.pr.number, record.pr.repo = 1, "owner/repository"
            monkeypatch.setattr(git_ops, "remote_slug", lambda *args, **kwargs: "owner/repository")
            with pytest.raises(BranchLeakError) as rejected:
                pr_rewrite._validate(wid, config, record, str(wt_path), recovery=True)
            error = str(rejected.value)
        elif flow == "snapshot":
            assert not finalize._push_changes_pr(wid, config, record, dry_run=True)
            error = capsys.readouterr().out
        else:
            assert not pr_incremental_publish.push_refspec(
                wid, config, record, f"worktree/{wid}", str(wt_path),
                wt_path.parent / ".finalize.lock", dry_run=True,
            )
            error = capsys.readouterr().out
    assert "machine name" in error
    assert private_name in error
