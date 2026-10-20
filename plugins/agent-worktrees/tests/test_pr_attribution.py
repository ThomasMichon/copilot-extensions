"""PR attribution audit contracts."""

from __future__ import annotations

import argparse

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import pr_ops


class TestAuditAttributionRisk:
    """pr-attribution-codenames Phase 5: audit_attribution_risk (config-only)."""

    def _config(self, config, **pr_overrides):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, **pr_overrides)
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def test_no_findings_for_default_config(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        assert pr_ops.audit_attribution_risk(config) == []

    def test_no_findings_when_attribution_true(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        config = self._config(
            config, source_attribution=True, head_pattern="user/{machine}/{slug}",
            source_attribution_configured=True,
        )
        assert pr_ops.audit_attribution_risk(config) == []

    def test_finding_for_risky_head_pattern_with_explicit_false(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        config = self._config(
            config, head_pattern="{machine}/{slug}",
            source_attribution_configured=True,
        )
        findings = pr_ops.audit_attribution_risk(config)
        assert len(findings) == 1
        assert "{machine}" in findings[0]
        assert "absent" not in findings[0]

    def test_finding_for_risky_head_pattern_with_genuinely_absent_key(self, pr_repo):
        # A raw config that omits `source_attribution` entirely is parsed the
        # same as an explicit `false` (`prcfg.source_attribution is False`),
        # but `source_attribution_configured` distinguishes the two so the
        # audit's finding text says "absent", not "False", for this case.
        config, _wid, _wt, _ = pr_repo
        config = self._config(
            config, head_pattern="{machine}/{slug}",
            source_attribution_configured=False,
        )
        findings = pr_ops.audit_attribution_risk(config)
        assert len(findings) == 1
        assert "absent" in findings[0]

    def test_finding_under_codename_mode(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        config = self._config(
            config, source_attribution="codename", head_pattern="{machine}/{slug}",
            source_attribution_configured=True,
        )
        findings = pr_ops.audit_attribution_risk(config)
        assert len(findings) == 1
        assert "true" in findings[0]


class TestAttributionAuditCLI:
    """CLI-level regression tests for `cmd_attribution_audit`."""

    def _args(self, *, use_json: bool = False) -> argparse.Namespace:
        return argparse.Namespace(json=use_json, config=None)

    def test_plain_mode_no_findings_ok(self, pr_repo, monkeypatch, capsys):
        config, _wid, _wt, _ = pr_repo
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
        rc = m.cmd_attribution_audit(self._args())
        assert rc == 0
        assert "No branch-name leak-class risk" in capsys.readouterr().out

    def test_plain_mode_warns_and_returns_1_on_findings(
        self, pr_repo, monkeypatch, capsys,
    ):
        import dataclasses
        config, _wid, _wt, _ = pr_repo
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, head_pattern="{machine}/{slug}")
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
        rc = m.cmd_attribution_audit(self._args())
        assert rc == 1
        assert "{machine}" in capsys.readouterr().out

    def test_json_mode_reports_findings_but_exits_0(
        self, pr_repo, monkeypatch, capfd,
    ):
        import dataclasses
        import json
        config, _wid, _wt, _ = pr_repo
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, head_pattern="{machine}/{slug}")
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
        rc = m.cmd_attribution_audit(self._args(use_json=True))
        assert rc == 0
        payload = json.loads(capfd.readouterr().out)
        assert payload["success"] is True
        assert len(payload["findings"]) == 1
        assert "{machine}" in payload["findings"][0]

    def test_config_load_failure_reported(self, monkeypatch, capsys):
        def _raise(*a, **k):
            raise ValueError("no active project")
        monkeypatch.setattr(cfg, "load_config", _raise)
        rc = m.cmd_attribution_audit(self._args())
        assert rc == 1
        assert "no active project" in capsys.readouterr().out

    def test_config_load_failure_reported_json(self, monkeypatch, capfd):
        import json
        def _raise(*a, **k):
            raise ValueError("no active project")
        monkeypatch.setattr(cfg, "load_config", _raise)
        rc = m.cmd_attribution_audit(self._args(use_json=True))
        assert rc == 1
        payload = json.loads(capfd.readouterr().out)
        assert payload["error"] == "no active project"
