"""Consent wakes are state updates, not fabricated review transitions."""

from dataclasses import replace
import json
import subprocess

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import pr_cli, pr_config
from agent_worktrees.pr_consent import consent_update_key
from agent_worktrees import pr_contract as pc
from agent_worktrees import pr_merge as pm
from agent_worktrees import pr_merge_cli, pr_watch as prw
from agent_worktrees import worktree_identity
from agent_worktrees.providers import ProviderError

pytestmark = pytest.mark.contract("agent_worktrees.pr_consent.updates")


def _approved(**kwargs):
    return replace(pc.PRSnapshot(
        author="author", head_sha="head", base_ref="dev", mergeable=True,
        checks_state="success",
        reviews=(pc.Review(7, "APPROVED", "reviewer", commit_id="head"),),
    ), **kwargs)


def _wait(snap, *, baseline=None, until=None, **kwargs):
    times = iter([0.0, 1.0])
    return prw.run_wait(
        repo="owner/repo", pr=42, until=until or ["merged"],
        baseline=baseline, fetch=lambda: snap, timeout=0.5, interval=1,
        automerge_label="auto-merge", default_branch="dev",
        now=lambda: next(times),
        sleep=lambda _: pytest.fail("an actionable first poll must not sleep"),
        **kwargs,
    )


def test_approved_baselines_wake_even_merged_only_and_deduplicate():
    snap = _approved()
    for until in (["merged"], list(pc.DEFAULT_UNTIL), ["any"]):
        for baseline in (None, pc.Baseline.from_snapshot(snap),
                         pc.Baseline.from_cursor("r7..hhead.ksuccess")):
            result = _wait(snap, baseline=baseline, until=until)
            assert result.matched
            assert result.payload["events"] == []
            assert result.payload["transitions"] == []
            assert result.payload["updates"] == ["consent_required"]
            assert result.payload["merge"]["needs_consent"] is True
            assert result.payload["merge"]["consent_action"] == "apply"
            assert result.payload["merge"]["consent_command"] == (
                "agent-worktrees pr-merge owner/repo 42"
            )
            assert result.payload["merge"]["consent_argv"] == [
                "agent-worktrees", "pr-merge", "owner/repo", "42",
            ]
            cursor = pc.Baseline.from_cursor(result.payload["cursor"])
            assert cursor.consent_update
            repeated = _wait(snap, baseline=cursor, until=until)
            assert not repeated.matched
            assert repeated.payload["timed_out"]
            assert "updates" not in repeated.payload
            assert repeated.payload["cursor"] == result.payload["cursor"]
    new_head = _approved(
        head_sha="next", reviews=(pc.Review(8, "APPROVED", "reviewer", commit_id="next"),),
    )
    assert _wait(new_head, baseline=cursor).payload["updates"] == ["consent_required"]


@pytest.mark.parametrize("changes", [
    {"reviews": ()},
    {"reviews": (pc.Review(7, "APPROVED", "reviewer", commit_id="old"),)},
    {"reviews": (pc.Review(7, "CHANGES_REQUESTED", "reviewer", commit_id="head"),)},
    {"reviews": (pc.Review(7, "APPROVED", "author", commit_id="head"),)},
    {"reviews": (pc.Review(7, "APPROVED", "reviewer", commit_id="head", dismissed=True),)},
    {"mergeable": False}, {"mergeable": None},
    {"checks_state": "pending"}, {"checks_state": "failure"},
    {"draft": True}, {"title": "WIP: change"}, {"labels": ("hold",)},
    {"labels": ("AUTO-MERGE",)}, {"base_ref": "other"}, {"head_sha": ""},
    {"checks_state": ""}, {"author": ""},
    {"reviews": (pc.Review(7, "APPROVED", "", commit_id="head"),)},
    {"reviews": (pc.Review(7, "APPROVED", "reviewer", commit_id=""),)},
])
def test_blocked_or_already_consented_does_not_bounce(changes):
    result = _wait(_approved(**changes), hold_labels=("hold",),
                   wip_title_prefixes=("wip:",))
    assert not result.matched
    assert "updates" not in result.payload
    assert "consent_command" not in result.payload["merge"]


@pytest.mark.parametrize("profile", [
    pc.PROFILE_PR_SELF_MERGE, pc.PROFILE_PR_HUMAN_MERGE, pc.PROFILE_DIRECT,
])
def test_non_agent_merge_profiles_never_bounce(profile):
    assert not _wait(_approved(), flow_profile=profile).matched


def test_real_approval_event_is_preserved_not_manufactured():
    result = _wait(_approved(), baseline=pc.Baseline.from_cursor("r0"), until=["approved"])
    assert result.payload["transitions"] == ["approved"]
    assert result.payload["events"][0]["review"]["id"] == 7
    assert result.payload["updates"] == ["consent_required"]


def test_no_checks_reported_and_terminal_states_preserve_existing_semantics():
    assert _wait(_approved(checks_state="none")).matched
    merged = _wait(_approved(pr_state="closed", merged=True))
    assert merged.payload["transitions"] == ["merged"]
    assert "updates" not in merged.payload
    for old_cursor in ("r7", "7", "r7..hhead", "r7..hhead.ksuccess"):
        assert pc.Baseline.from_cursor(old_cursor).consent_update == ""
    acknowledged = pc.Baseline(consent_update="state")
    assert pc.Baseline.from_cursor(acknowledged.to_cursor()).consent_update == "state"


def test_cursor_clears_acknowledgement_on_block_and_reprompts_afterward():
    snap = _approved()
    cursor = pc.Baseline.from_cursor(_wait(snap).payload["cursor"])
    blocked = _wait(replace(snap, labels=("hold",)), baseline=cursor, hold_labels=("hold",))
    cleared = pc.Baseline.from_cursor(blocked.payload["cursor"])
    assert cleared.consent_update == ""
    assert _wait(snap, baseline=cleared).matched


def test_ignored_review_activity_does_not_repeat_consent_update():
    snap = _approved()
    cursor = pc.Baseline.from_cursor(_wait(snap).payload["cursor"])
    for review in (
        pc.Review(8, "COMMENTED", "reviewer", commit_id="head"),
        pc.Review(8, "PENDING", "reviewer", commit_id="head"),
        pc.Review(8, "APPROVED", "reviewer", commit_id="head", dismissed=True),
        pc.Review(8, "APPROVED", "author", commit_id="head"),
        pc.Review(6, "APPROVED", "older-reviewer", commit_id="head"),
    ):
        result = _wait(replace(snap, reviews=(*snap.reviews, review)), baseline=cursor)
        assert not result.matched
        assert "updates" not in result.payload
        assert pc.Baseline.from_cursor(result.payload["cursor"]).consent_update == (
            cursor.consent_update
        )


def test_missing_authoritative_approval_evidence_never_raises():
    snap = _approved(reviews=())
    readiness = {
        "needs_consent": True, "verdict": "APPROVED", "consent_label": "auto-merge",
    }
    assert consent_update_key(
        snap, readiness, flow_profile=pc.PROFILE_PR_AGENT_MERGE,
    ) == ""
    unknown_identity = _approved(
        author="", reviews=(pc.Review(7, "APPROVED", "", commit_id="head"),),
    )
    assert not _wait(unknown_identity).matched
    lowercase = _approved(
        reviews=(pc.Review(7, "approved", "reviewer", commit_id="head"),),
    )
    assert _wait(lowercase).matched


def test_provider_partial_checks_failures_do_not_authorize_wake(monkeypatch):
    from agent_worktrees.providers import github, gitea

    github_provider = github.GitHubProvider()
    for broken_endpoint in ("status", "check-runs"):
        for bad in (
            subprocess.CompletedProcess([], 1, "", "unavailable"),
            subprocess.CompletedProcess([], 0, "{", ""),
            subprocess.CompletedProcess([], 0, "{}", ""),
        ):
            def run(args, **kwargs):
                if args[-1].endswith("/" + broken_endpoint):
                    return bad
                data = {"state": "success", "statuses": [{"id": 1}]} if (
                    args[-1].endswith("/status")
                ) else {"check_runs": []}
                return subprocess.CompletedProcess([], 0, json.dumps(data), "")

            monkeypatch.setattr(github, "run_cli", run)
            state = github_provider._combined_checks_state("owner/repo", "head", "")
            assert state == ""
            assert not _wait(_approved(checks_state=state)).matched
    gitea_provider = gitea.GiteaProvider()
    for response in (
        (500, ""), (200, "{"), (200, "[]"), (200, "{}"),
        (200, '{"state":"success"}'),
    ):
        monkeypatch.setattr(gitea_provider, "_curl", lambda *a, **k: response)
        state = gitea_provider._combined_status_state(
            "owner/repo", "head", "https://example.com", "",
        )
        assert state == ""
        assert not _wait(_approved(checks_state=state)).matched


def test_incomplete_review_pages_and_unknown_conclusions_fail_closed(monkeypatch):
    from agent_worktrees.providers import github
    provider = github.GitHubProvider()
    page = [{"id": n, "state": "APPROVED", "user": {"login": "reviewer"},
             "commit_id": "head"} for n in range(100)]
    for second in (
        subprocess.CompletedProcess([], 1, "", "unavailable"),
        subprocess.CompletedProcess([], 0, "{", ""),
        subprocess.CompletedProcess([], 0, "{}", ""),
    ):
        responses = iter([subprocess.CompletedProcess([], 0, json.dumps(page), ""), second])
        monkeypatch.setattr(github, "run_cli", lambda *a, **k: next(responses))
        with pytest.raises(ProviderError) as error:
            provider._all_review_objs("owner/repo", 42, "")
        if second.returncode == 0:
            assert error.value.transient
    for conclusion in (None, "", "unrecognized"):
        responses = iter([
            subprocess.CompletedProcess([], 0, '{"statuses":[]}', ""),
            subprocess.CompletedProcess([], 0, json.dumps({"check_runs": [
                {"status": "completed", "conclusion": conclusion},
            ]}), ""),
        ])
        monkeypatch.setattr(github, "run_cli", lambda *a, **k: next(responses))
        assert provider._combined_checks_state("owner/repo", "head", "") == ""


@pytest.mark.parametrize("detail,transient", [
    ("HTTP 401: Bad credentials", False),
    ("HTTP 403: Resource not accessible", False),
    ("HTTP 404: Not Found", False),
    ("HTTP 503: Service Unavailable", True),
    ("connection reset", True),
])
def test_review_page_errors_preserve_provider_retry_policy(monkeypatch, detail, transient):
    from agent_worktrees.providers import github
    monkeypatch.setattr(
        github, "run_cli",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "", detail),
    )
    with pytest.raises(ProviderError) as error:
        github.GitHubProvider()._all_review_objs("owner/repo", 42, "")
    assert error.value.transient is transient
    assert detail in str(error.value)


def test_paginated_check_runs_include_late_failures_and_require_complete_pages(monkeypatch):
    from agent_worktrees.providers import github
    first = json.dumps({"total_count": 31, "check_runs": [
        {"status": "completed", "conclusion": "success"} for _ in range(30)
    ]})
    for conclusion, expected in (("failure", "failure"), ("success", "success")):
        last = json.dumps({"total_count": 31, "check_runs": [
            {"status": "completed", "conclusion": conclusion},
        ]})
        def run(args, **kwargs):
            if args[-1].endswith("/status"):
                return subprocess.CompletedProcess([], 0, '{"statuses":[]}', "")
            assert "--paginate" in args
            return subprocess.CompletedProcess([], 0, first + "\n" + last, "")
        monkeypatch.setattr(github, "run_cli", run)
        assert github.GitHubProvider()._combined_checks_state("owner/repo", "head", "") == expected
    from agent_worktrees.providers.github_check_pages import check_runs_from_pages
    assert check_runs_from_pages(first) is None
    assert check_runs_from_pages(first + "\n{") is None


def test_fetch_errors_do_not_invent_actionable_state():
    def fetch():
        raise ProviderError("unavailable", transient=True)

    errors = []
    times = iter([0.0, 1.0])
    result = prw.run_wait(
        repo="owner/repo", pr=42, until=["merged"], baseline=None, fetch=fetch,
        timeout=0.5, interval=1, automerge_label="auto-merge",
        now=lambda: next(times), on_error=errors.append,
    )
    assert result.payload == {"repo": "owner/repo", "pr": 42, "timed_out": True}
    assert len(errors) == 1
    with pytest.raises(ProviderError):
        prw.run_wait(
            repo="owner/repo", pr=42, until=["merged"], baseline=None,
            fetch=lambda: (_ for _ in ()).throw(ProviderError("denied")),
            timeout=1, interval=1, automerge_label="auto-merge",
        )


@pytest.mark.parametrize("json_mode", [True, False])
def test_cli_wakes_with_action_and_exit_zero(monkeypatch, capsys, json_mode):
    repo = cfg.RepoConfig(
        anchor="/anchor", worktree_root="/worktrees", default_branch="dev",
        pr=cfg.PRConfig(enabled=True, required=True, automerge_label="auto-merge"),
    )
    config = cfg.Config(srcroot="/src", machine="test", platform="linux",
                        repo_name="repo", repos={"repo": repo})
    monkeypatch.setattr(cfg, "load_config", lambda *a: config)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id_from_cwd", lambda **k: "wt")
    monkeypatch.setattr(
        pr_config, "resolve_repo_config_for_slug",
        lambda *a: pr_config.ForeignRepoResolution(repo, "repo"),
    )
    monkeypatch.setattr(
        pr_config, "resolve_actor_pr_flow",
        lambda *a, **k: pr_config.ActorPRFlow(
            pr_config=repo.pr,
            configured_flow=pc.classify_pr_flow(enabled=True, required=True,
                                                automerge_label="auto-merge"),
            flow=pc.classify_pr_flow(enabled=True, required=True, automerge_label="auto-merge"),
        ),
    )
    monkeypatch.setattr(pr_cli, "_tracked_pr_head_evidence", lambda *a: ("", ""))
    monkeypatch.setattr(prw, "build_fetch", lambda *a, **k: _approved)
    args = ["wait", "owner/repo", "42", "--until", "merged", "--timeout", "0.5"]
    code = pr_cli.cmd_pr_watch_dispatch(args + (["--json"] if json_mode else []))
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["updates"] == ["consent_required"]
    assert payload["transitions"] == []
    if not json_mode:
        assert "NEXT -> agent-worktrees pr-merge owner/repo 42" in captured.err
        assert captured.err.count("NEXT ->") == 1
        assert "add label" not in captured.err


def test_merge_preview_and_apply_share_readiness_without_waiting(capsys):
    class Provider:
        applied = []

        def get_snapshot(self, *a, **k):
            return _approved()

        def request_auto_complete(self, repo, number, **kwargs):
            self.applied.append((repo, number, kwargs["automerge_label"]))
            return ""

    provider = Provider()
    prcfg = cfg.PRConfig(automerge_label="auto-merge")
    preview = pm.merge_one(prcfg, "owner/repo", 42, token="", provider=provider)
    assert preview["merge"]["needs_consent"]
    assert preview["merge"]["consent_action"] == "apply"
    assert provider.applied == []
    pr_merge_cli._pr_merge_print_human({
        "repo": "owner/repo", "open": 1, "eligible": 1, "apply": False, "decisions": [preview],
    })
    assert "NEXT -> agent-worktrees pr-merge owner/repo 42" in capsys.readouterr().err
    applied = pm.merge_one(prcfg, "owner/repo", 42, token="", provider=provider, apply=True)
    assert applied["applied"]
    assert not applied["merge"]["needs_consent"]
    assert applied["merge"]["consent_action"] == "already"
    assert applied["merge"]["occupancy"] == applied["occupancy"] == "needs-merge"
    assert provider.applied == [("owner/repo", 42, "auto-merge")]
