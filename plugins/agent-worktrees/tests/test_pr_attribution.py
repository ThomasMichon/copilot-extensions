"""PR attribution auditing, head-leak safety, refresh, and frozen provenance."""

from __future__ import annotations

import argparse
import pytest
from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.attribution")


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


class TestPRAttributionRefresh:
    def test_push_changes_refreshes_marker_and_preserves_body(
        self, pr_repo, monkeypatch
    ):
        import dataclasses

        from agent_worktrees import finalize as fin
        from agent_worktrees.providers import attribution

        config, wid, wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution=True),
                )
            },
        )
        created = pr_ops.create_pr(
            wid, config, title="Add feature", target_repo="example/project",
        )
        assert created["success"], created
        associated = pr_ops.set_pr(
            wid,
            url="https://gitea.example.com/example/project/pulls/42",
            number=42,
            provider="gitea",
            config=config,
        )
        assert associated["success"], associated
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert (record.pr.repo, record.pr.number, record.pr.provider) == (
            "example/project", 42, "gitea",
        )
        assert record.pr.head_sha == created["head_sha"]
        captured: dict[str, str] = {}
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                captured["marker"] = marker
                publishes["count"] += 1
                return ""

        provider = FakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: captured.setdefault("provider", name) and provider,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda slug, prcfg: None,
        )
        wt_path.joinpath("c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        assert fin.push_changes(wid, config) is True

        fields = attribution.parse_marker(captured["marker"])
        assert fields is not None
        assert captured["provider"] == "gitea"
        assert fields["head"] == _git("rev-parse", "HEAD", cwd=wt_path)
        assert "old-head" not in captured["marker"]
        refreshed = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert refreshed.pr.attribution_head == fields["head"]
        assert pr_ops.refresh_source_attribution(
            wid,
            config,
            refreshed,
            refreshed.pr,
            fields["head"],
        ) == ""
        assert publishes["count"] == 1
        refreshed.pr.provider = "github"
        refreshed.pr.attribution_head = ""
        mismatch = pr_ops.refresh_source_attribution(
            wid,
            config,
            refreshed,
            refreshed.pr,
            fields["head"],
        )
        assert "credentials cannot be resolved safely" in mismatch
        assert publishes["count"] == 1

    def test_refresh_source_attribution_codename_mode_encrypts_raw_fields(
        self, pr_repo, monkeypatch, keyed_identity_payload,
    ):
        """Refresh publishes identity through the cipher, never as plaintext fields."""
        import dataclasses

        from agent_worktrees.providers import attribution

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution="codename"),
                )
            },
        )
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "harbor-lattice"
        # codename-attribution-by-default: may_publish_codename requires a
        # known codename_source before publishing -- this test is about
        # marker content/format, not provenance, so stamp a safe "built-in".
        record.codename_source = "built-in"
        tracking.save_record(record)
        pr = tracking.PRRecord(state="open", branch="feature/x", provider="gitea",
                       repo="example/project", number=42)
        record.prs = [pr]
        tracking.save_record(record)
        captured: dict[str, str] = {}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                captured["marker"] = marker
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda slug, prcfg: None,
        )

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )

        assert error == ""
        fields = attribution.parse_marker(captured["marker"])
        assert fields["codename"] == "harbor-lattice"
        assert set(fields) == {"codename", "enc"}
        assert keyed_identity_payload["worktree_id"] == wid
        assert keyed_identity_payload["machine"] == record.machine
        assert keyed_identity_payload["head"] == "deadbeef" * 5

    def test_refresh_source_attribution_codename_mode_skips_without_codename(
        self, pr_repo, monkeypatch,
    ):
        """No assigned codename -> skip the refresh publish entirely, never
        downgrade to the raw marker."""
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution="codename"),
                )
            },
        )
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert record.codename is None
        pr = tracking.PRRecord(state="open", branch="feature/x", provider="gitea",
                       repo="example/project", number=42)
        record.prs = [pr]
        tracking.save_record(record)
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes["count"] += 1
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )

        assert error == ""
        assert publishes["count"] == 0

    def test_refresh_source_attribution_codename_mode_skips_for_malformed_codename(
        self, pr_repo, monkeypatch,
    ):
        """A tampered/corrupted codename must never be interpolated into
        the refreshed marker as-is -- skip publishing instead."""
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, source_attribution="codename"),
                )
            },
        )
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "not a handle --> <script>"
        pr = tracking.PRRecord(state="open", branch="feature/x", provider="gitea",
                       repo="example/project", number=42)
        record.prs = [pr]
        tracking.save_record(record)
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes["count"] += 1
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )

        assert error == ""
        assert publishes["count"] == 0

    def test_refresh_source_attribution_codename_mode_provenance_gating(
        self, pr_repo, monkeypatch,
    ):
        """codename-attribution-by-default: the refresh path must apply the
        SAME provenance gating as the initial create-pr publish (round-17
        finding -- prior to this effort only one of the two paths checked
        codename_source at all)."""
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(source_attribution_configured: bool):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr, source_attribution="codename",
                            source_attribution_configured=(
                                source_attribution_configured
                            ),
                        ),
                    )
                },
            )

        _pr_counter = {"n": 0}

        def _refresh(codename_source, *, source_attribution_configured):
            _pr_counter["n"] += 1
            # A distinct SYNTHETIC worktree id per scenario -- each call
            # simulates an INDEPENDENT worktree/PR from scratch. Reusing
            # the same on-disk record across scenarios would let the
            # codename-provenance merge (correctly) preserve an
            # already-known codename_source from an earlier scenario,
            # which is exactly this effort's point but defeats this
            # test's intent to exercise each provenance value in
            # isolation.
            scenario_wid = f"{wid}-scenario-{_pr_counter['n']}"
            tracking.create_new_record(
                scenario_wid, f"worktree/{scenario_wid}", "/tmp/" + scenario_wid,
                "ext", "test", "linux", cfg.tracking_dir(),
            )
            record = tracking.load_record(cfg.tracking_dir() / f"{scenario_wid}.yaml")
            record.codename = "harbor-lattice"
            record.codename_source = codename_source
            pr = tracking.PRRecord(
                state="open", branch=f"feature/x-{_pr_counter['n']}",
                provider="gitea", repo="example/project",
                number=42 + _pr_counter["n"],
            )
            record.prs = [pr]
            tracking.save_record(record)
            publishes = {"count": 0}

            class FakeProvider:
                def publish_source_marker(
                    self, repo, number, marker, *, api_base="", token=None
                ):
                    publishes["count"] += 1
                    return ""

            monkeypatch.setattr(
                "agent_worktrees.providers.get_provider",
                lambda name: FakeProvider(),
            )
            error = pr_ops.refresh_source_attribution(
                scenario_wid, _config_with(source_attribution_configured),
                record, pr,
                "deadbeef" * 5,
            )
            assert error == ""
            return publishes["count"]

        # (a) codename_source "custom" under the IMPLICIT default fails
        # closed -- the legacy-drift scenario (round-8 finding).
        assert _refresh(
            "custom", source_attribution_configured=False,
        ) == 0
        # (b) an EXPLICIT opt-in publishes a KNOWN "custom" record.
        assert _refresh(
            "custom", source_attribution_configured=True,
        ) == 1
        # (b2) that SAME explicit opt-in does NOT publish for a MISSING
        # codename_source (round-32 finding: explicit opt-in narrows the
        # allocation-vs-publish distinction, it does not bypass provenance
        # verification).
        assert _refresh(
            None, source_attribution_configured=True,
        ) == 0
        # (c) codename_source "built-in" publishes under the IMPLICIT
        # default.
        assert _refresh(
            "built-in", source_attribution_configured=False,
        ) == 1
        # (d) an unrecognized stored value fails closed exactly like
        # "custom" would.
        assert _refresh(
            "not-a-real-value", source_attribution_configured=True,
        ) == 0

    def test_refresh_source_attribution_freezes_before_metadata_validation(
        self, pr_repo,
    ):
        # PR #3037 review finding: the freeze-on-first-touch must run
        # BEFORE the number/repo metadata-validation early return too, not
        # only before the live-config guard -- a legacy PR with missing
        # provider metadata (e.g. before `set-pr` has supplied it) must
        # still be frozen on this touch; if `set-pr` supplies the metadata
        # LATER after config has changed, that later touch must not be
        # treated as the true first one.
        config, wid, _wt_path, _ = pr_repo
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = tracking.PRRecord(state="creating", branch="feature/x")
        assert pr.number is None and not pr.repo  # incomplete metadata
        assert pr.attribution_mode == ""
        record.prs = [pr]
        tracking.save_record(record)

        error = pr_ops.refresh_source_attribution(
            wid, config, record, pr, "deadbeef" * 5,
        )
        assert error == "active PR has no provider repo/number"

        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        # Frozen despite the incomplete metadata -- pr_repo's bare
        # PRConfig() resolves the implicit "codename" default.
        assert frozen_pr.attribution_mode == "codename"

    def test_refresh_source_attribution_legacy_freeze_runs_before_live_config_guard(
        self, pr_repo, monkeypatch,
    ):
        # round-37 finding: the freeze-on-first-touch must run BEFORE any
        # early return that depends on live config -- a legacy PR (no
        # attribution_mode stored) touched while source_attribution is
        # LIVE `False` must be frozen to the false state on THAT touch
        # (not left unmigrated because the function used to return early
        # before ever reaching the freeze), and a LATER touch under a
        # DIFFERENT live config must still honor the ORIGINAL frozen
        # state, not the newer one.
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(source_attribution):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr, source_attribution=source_attribution,
                        ),
                    )
                },
            )

        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
        )
        assert pr.attribution_mode == ""  # genuinely legacy/unset
        record.prs = [pr]
        tracking.save_record(record)
        publishes = {"count": 0}

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes["count"] += 1
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        # First touch under a LIVE False config.
        error = pr_ops.refresh_source_attribution(
            wid, _config_with(False), record, pr, "deadbeef" * 5,
        )
        assert error == ""
        assert publishes["count"] == 0
        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        assert frozen_pr.attribution_mode == "false"  # frozen, not still ""

        # Second touch (a NEW head, so publish would proceed if the mode
        # allowed it) under a config that has since changed to "codename".
        # The ORIGINAL frozen "false" must still govern -- no marker.
        error2 = pr_ops.refresh_source_attribution(
            wid, _config_with("codename"), reloaded, frozen_pr,
            "cafebabe" * 5,
        )
        assert error2 == ""
        assert publishes["count"] == 0
        assert frozen_pr.attribution_mode == "false"

    def test_frozen_codename_marker_survives_config_flipping_to_false(
        self, pr_repo, monkeypatch,
    ):
        # Validation Plan (round-26/27 findings): the REVERSE direction of
        # the test above -- a PR frozen under "codename" (built-in
        # provenance, so it publishes) must KEEP publishing on later
        # pushes even after the repo's live config changes to "false".
        # attribution_explicit must also stay frozen (False, an implicit
        # decision), never re-derived from the live config.
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(source_attribution):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr, source_attribution=source_attribution,
                        ),
                    )
                },
            )

        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "steady-anchor"
        record.codename_source = "built-in"
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
        )
        assert pr.attribution_mode == ""  # genuinely legacy/unset
        record.prs = [pr]
        tracking.save_record(record)
        publishes: list[str] = []

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes.append(marker)
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        # First touch under a LIVE "codename" (implicit) config -- freezes
        # to codename mode, publishes the marker.
        error = pr_ops.refresh_source_attribution(
            wid, _config_with("codename"), record, pr, "deadbeef" * 5,
        )
        assert error == ""
        assert len(publishes) == 1
        assert "steady-anchor" in publishes[0]
        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        assert frozen_pr.attribution_mode == "codename"
        assert frozen_pr.attribution_explicit is False

        # Second touch (a NEW head) after the live config flips to False.
        # The ORIGINAL frozen "codename" pair must still govern -- the
        # marker keeps publishing, not suppressed by the newer config.
        error2 = pr_ops.refresh_source_attribution(
            wid, _config_with(False), reloaded, frozen_pr, "cafebabe" * 5,
        )
        assert error2 == ""
        assert len(publishes) == 2
        assert "steady-anchor" in publishes[1]
        assert frozen_pr.attribution_mode == "codename"
        assert frozen_pr.attribution_explicit is False

    def test_explicit_config_addition_does_not_unsuppress_a_frozen_custom_source(
        self, pr_repo, monkeypatch,
    ):
        # Validation Plan (round-27 finding): a PR opened under an
        # IMPLICIT "codename" default against a "custom"-sourced record is
        # correctly suppressed at open (round-22 gate: an implicit default
        # never publishes a custom-vocabulary codename). Adding an
        # EXPLICIT `source_attribution: codename` to the repo's config
        # afterward must NOT retroactively unsuppress it -- `PRRecord.
        # attribution_explicit` was frozen False at open time and is never
        # re-derived from the live, now-True `source_attribution_configured`.
        import dataclasses

        config, wid, _wt_path, _ = pr_repo
        repo = config.repos["ext"]

        def _config_with(*, configured: bool):
            return dataclasses.replace(
                config,
                repos={
                    "ext": dataclasses.replace(
                        repo,
                        pr=dataclasses.replace(
                            repo.pr,
                            source_attribution="codename",
                            source_attribution_configured=configured,
                        ),
                    )
                },
            )

        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.codename = "quiet-harbor"
        record.codename_source = "custom"  # unreviewed custom vocabulary
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
        )
        record.prs = [pr]
        tracking.save_record(record)
        publishes: list[str] = []

        class FakeProvider:
            def publish_source_marker(
                self, repo, number, marker, *, api_base="", token=None
            ):
                publishes.append(marker)
                return ""

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider",
            lambda name: FakeProvider(),
        )

        # First touch: implicit codename default (not configured) against
        # a custom-sourced codename -- suppressed, freezes explicit=False.
        error = pr_ops.refresh_source_attribution(
            wid, _config_with(configured=False), record, pr, "deadbeef" * 5,
        )
        assert error == ""
        assert publishes == []
        reloaded = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        frozen_pr = reloaded.active_pr()
        assert frozen_pr is not None
        assert frozen_pr.attribution_mode == "codename"
        assert frozen_pr.attribution_explicit is False

        # Second touch (a NEW head): the repo now EXPLICITLY sets
        # source_attribution: codename. The frozen attribution_explicit
        # must stay False -- the marker stays suppressed.
        error2 = pr_ops.refresh_source_attribution(
            wid, _config_with(configured=True), reloaded, frozen_pr,
            "cafebabe" * 5,
        )
        assert error2 == ""
        assert publishes == []
        assert frozen_pr.attribution_explicit is False

    def test_finish_auto_open_rerun_always_invokes_refresh_regardless_of_live_config(
        self, pr_repo, monkeypatch,
    ):
        # round-38 finding: the CALLER GATE deciding whether to invoke
        # refresh_source_attribution at all must not itself branch on live
        # config -- a PR frozen under "true" whose live config later flips
        # to "false" must still reach the frozen-pair logic inside the
        # function on a create-pr re-run.
        config, wid, _wt_path, _ = pr_repo
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        pr = tracking.PRRecord(
            state="open", branch="feature/x", provider="gitea",
            repo="example/project", number=42,
            attribution_mode="true", attribution_explicit=True, pr_id="abc",
            pr_revision=1,
        )
        record.prs = [pr]
        tracking.save_record(record)
        calls = {"count": 0}
        real_refresh = pr_ops.refresh_source_attribution

        def _spy(*a, **k):
            calls["count"] += 1
            return real_refresh(*a, **k)

        monkeypatch.setattr(pr_ops, "refresh_source_attribution", _spy)

        result: dict = {}
        # Live config now says False -- the caller gate must NOT skip the
        # call just because this snapshot says publication is off.
        import dataclasses
        repo = config.repos["ext"]
        false_config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(
                        repo.pr, source_attribution=False, auto_open=True,
                    ),
                )
            },
        )
        pr_ops._finish_auto_open(
            result, false_config, record, pr, title="Add feature", body=None,
            worktree_id=wid, head_sha="deadbeef" * 5, open_pr=None,
            draft=False, attribution=None,
            prcfg=false_config.default_repo.pr,
        )
        assert calls["count"] == 1


        # New primary flow: create-pr leaves HEAD on worktree/<id> at the
        # squashed commit, so feedback commits land there. push-changes rebases
        # worktree/<id>, re-snapshots the feature branch to its tip, and pushes
        # it -- HEAD never leaves the worktree branch.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == \
            f"worktree/{wid}"
        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)

        # Feedback commit directly on worktree/<id> (no checkout).
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote feature branch advanced
        # HEAD stayed on the worktree branch; the pushed head is its tip.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == \
            f"worktree/{wid}"
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == \
            _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_sha == _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        assert rec.pr.state == "open"


class TestCreatePRBranchLeakGuard:
    """pr-attribution-codenames Phase 5: hard-block a leaking effective head."""

    def _config(self, config, **pr_overrides):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, **pr_overrides)
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def test_explicit_branch_with_raw_worktree_id_is_blocked(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.create_pr(
            wid, config, title="Add feature", branch=f"worktree/{wid}",
        )
        assert res["success"] is False
        assert "leak" in res["error"]
        assert wid in res["error"]
        # Nothing was pushed -- the branch never exists on the remote.
        assert not git_ops.remote_branch_exists(
            "origin", f"worktree/{wid}", cwd=str(wt_path)
        )

    def test_head_pattern_with_machine_token_is_blocked(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        config = self._config(config, head_pattern="user/{machine}/{slug}")
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is False
        assert "leak" in res["error"]
        assert "machine name" in res["error"]

    def test_source_attribution_true_allows_the_raw_head(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        config = self._config(config, source_attribution=True)
        res = pr_ops.create_pr(
            wid, config, title="Add feature", branch=f"worktree/{wid}",
        )
        assert res["success"] is True, res

    def test_codename_mode_still_blocks_a_leaking_branch(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        config = self._config(config, source_attribution="codename")
        res = pr_ops.create_pr(
            wid, config, title="Add feature", branch=f"worktree/{wid}",
        )
        assert res["success"] is False
        assert "leak" in res["error"]

    def test_dry_run_still_reports_the_block(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        res = pr_ops.create_pr(
            wid, config, title="Add feature", branch=f"worktree/{wid}",
            dry_run=True,
        )
        assert res["success"] is False
        assert "leak" in res["error"]

    def test_safe_default_head_pattern_is_unaffected(self, pr_repo):
        # Regression: the ordinary snapshot/refspec default patterns never
        # embed a private identifier, so they must still succeed unchanged.
        config, wid, _wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True, res

    def test_renamed_machine_still_blocks_the_recorded_identity(self, pr_repo):
        # config.machine (live) can differ from record.machine (frozen at
        # registration, e.g. after a machine rename/migration). An explicit
        # --branch embedding the OLD recorded machine name must still be
        # blocked even though the live config machine no longer matches it.
        config, wid, _wt_path, _ = pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.machine == "test"
        import dataclasses
        renamed_config = dataclasses.replace(config, machine="new-machine-name")
        res = pr_ops.create_pr(
            wid, renamed_config, title="Add feature", branch="user/test/reused-head",
        )
        assert res["success"] is False
        assert "machine name" in res["error"]
        assert "'test'" in res["error"]
