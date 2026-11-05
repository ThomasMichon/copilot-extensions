"""Provider-returned target identity survives the existing native snapshot seam."""

import json
from types import SimpleNamespace

import pytest

from agent_worktrees.pr_snapshot import PRSnapshot
from agent_worktrees.pr_snapshot_payload import snapshot_payload
from agent_worktrees.providers import azure_devops, github, gitea
from test_azure_pr_observation import Reader, read

HEAD, TARGET = "a" * 40, "c" * 40


@pytest.mark.parametrize("provider", ["github", "gitea"])
def test_rest_snapshot_preserves_target_from_the_same_pr_object(monkeypatch, provider):
    pr = {
        "state": "open",
        "head": {"sha": HEAD},
        "base": {"sha": TARGET, "ref": "main"},
    }
    calls = []
    if provider == "github":

        def request(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps(pr), stderr="")

        monkeypatch.setattr(github, "run_cli", request)
        selected = github.GitHubProvider()
        from agent_worktrees.pr_checks import ChecksReport

        monkeypatch.setattr(selected, "_checks_report", lambda *a, **kw: ChecksReport())
    else:
        selected = gitea.GiteaProvider()

        def request(*args, **kwargs):
            calls.append(args)
            return 200, json.dumps(pr)

        monkeypatch.setattr(selected, "_curl", request)
        monkeypatch.setattr(selected, "_combined_status_state", lambda *a, **kw: "")
    monkeypatch.setattr(selected, "_all_review_objs", lambda *a, **kw: ())
    snap = selected.get_snapshot(
        "example/repo", 7, api_base="https://forge.example.test", token="synthetic"
    )
    assert snap.head_sha == HEAD and snap.base_sha == TARGET
    assert len(calls) == 1
    pr["base"]["sha"] = "d" * 40
    moved = selected.get_snapshot(
        "example/repo", 7, api_base="https://forge.example.test", token="synthetic"
    )
    assert moved.head_sha == HEAD and moved.base_sha != snap.base_sha


@pytest.mark.parametrize("terminal", [False, True])
def test_native_ado_target_is_not_the_merge_commit(terminal):
    reader = Reader()
    reader.data["lastMergeTargetCommit"] = {"commitId": TARGET}
    if terminal:
        reader.data["status"] = "completed"
    snap = read(reader)
    assert snap.base_sha == TARGET
    assert snap.base_sha != reader.data["lastMergeCommit"]["commitId"]


def test_legacy_ado_preserves_target_without_another_command(monkeypatch):
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "active",
                    "lastMergeSourceCommit": {"commitId": HEAD},
                    "lastMergeTargetCommit": {"commitId": TARGET},
                    "targetRefName": "refs/heads/main",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(azure_devops, "run_cli", request)
    snap = azure_devops.AzureDevOpsProvider().get_snapshot(
        "Project/repository",
        7,
        api_base="https://dev.azure.com/example",
    )
    assert snap.base_sha == TARGET and len(calls) == 1


@pytest.mark.parametrize("target", ["", None, "main", "abc", "g" * 40, TARGET, "e" * 64])
def test_wire_target_is_a_full_oid_or_explicitly_unknown(target):
    snap = PRSnapshot(head_sha=HEAD, base_ref="main", base_sha=target)
    value = snapshot_payload(snap, "example/repo", 7, "github", "https://api.github.com")
    assert value["base_sha"] == (target if target in (TARGET, "e" * 64) else "")
    assert value["head_sha"] == HEAD and value["base_ref"] == "main"
