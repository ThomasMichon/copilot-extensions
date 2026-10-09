"""Create-pr CLI argument and policy-error contracts."""

from __future__ import annotations

import pytest
from agent_worktrees import __main__ as m
from agent_worktrees import worktree_identity

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.create_cli")


class TestCreatePRCLIPolicyError:
    """Round-5 review finding: `cmd_create_pr --json` must serialize a
    `CodenameAttributionPolicyError` from `pr_ops.create_pr`'s allocation
    preflight as a clean JSON error, never a raw traceback."""

    def test_json_policy_error_is_a_clean_json_error(
        self, pr_repo, monkeypatch, capfd,
    ):
        import json

        config, wid, _wt_path, _ = pr_repo

        def _raise_policy_error(*_a, **_k):
            raise m.codename_tracking.CodenameAttributionPolicyError(
                "PR-active repo 'ext' has a custom codename wordlist "
                "configured but pr.source_attribution is not explicit"
            )

        monkeypatch.setattr(m.pr_ops, "create_pr", _raise_policy_error)
        args = m.build_parser().parse_args([
            "create-pr", "--json", wid,
        ])

        rc = m.cmd_create_pr(args)

        captured = capfd.readouterr()
        assert rc == 1
        assert "Traceback" not in captured.out + captured.err
        assert "custom codename wordlist" in json.loads(captured.out)["error"]


class TestCreatePRCLIArgs:
    def test_success_output_names_squash_action(self, pr_repo, monkeypatch, capfd):
        config, wid, _wt_path, _ = pr_repo

        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: candidate)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)

        args = m.build_parser().parse_args([
            "create-pr", wid, "--title", "Add feature",
        ])

        rc = m.cmd_create_pr(args)

        captured = capfd.readouterr()
        combined = captured.out + captured.err
        assert rc == 0
        assert "squashed 2 surviving commit(s) into one" in combined

    def test_topic_flag_is_parsed_and_forwarded(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        captured: dict[str, object] = {}

        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: candidate)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)

        def _fake_create_pr(worktree_id, passed_config, **kwargs):
            captured["worktree_id"] = worktree_id
            captured["config"] = passed_config
            captured["kwargs"] = kwargs
            return {
                "success": True,
                "branch": "feature/add-feature-hot-fix-aaaa",
                "remote": "origin",
                "provider": "gitea",
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "draft": False,
            }

        monkeypatch.setattr(m.pr_ops, "create_pr", _fake_create_pr)

        args = m.build_parser().parse_args([
            "create-pr", wid, "--title", "Add feature", "--topic", "Hot Fix",
        ])

        rc = m.cmd_create_pr(args)

        assert rc == 0
        assert captured["worktree_id"] == wid
        assert captured["config"] is config
        assert captured["kwargs"]["title"] == "Add feature"
        assert captured["kwargs"]["topic"] == "Hot Fix"

    def test_topic_flag_coexists_with_branch_override(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        captured: dict[str, object] = {}

        monkeypatch.setattr(m.cfg, "load_config", lambda *_a, **_k: config)
        monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda candidate, _config: candidate)
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda candidate: candidate)

        def _fake_create_pr(_worktree_id, _config, **kwargs):
            captured["kwargs"] = kwargs
            return {
                "success": True,
                "branch": kwargs["branch"],
                "remote": "origin",
                "provider": "gitea",
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "draft": False,
                "topic_note": "Ignoring --topic because --branch fully overrides the head name.",
            }

        monkeypatch.setattr(m.pr_ops, "create_pr", _fake_create_pr)

        args = m.build_parser().parse_args([
            "create-pr",
            wid,
            "--branch",
            "feature/manual-branch",
            "--topic",
            "Hot Fix",
        ])

        rc = m.cmd_create_pr(args)

        assert rc == 0
        assert captured["kwargs"]["branch"] == "feature/manual-branch"
        assert captured["kwargs"]["topic"] == "Hot Fix"
