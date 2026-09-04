"""Tests for agent_worktrees.config — platform detection and path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg


@pytest.fixture(autouse=True)
def _isolate_config_layers(tmp_path_factory, monkeypatch):
    """Make layered config hermetic across the module.

    Points the global config tier at a non-existent path and stubs the repos
    registry to empty, so unit tests never pick up this machine's real
    ``~/.agent-worktrees/config.yaml`` or ``repos.yaml``. Tests that exercise
    those tiers override these within the test.
    """
    missing_global = tmp_path_factory.mktemp("noglobal") / "config.yaml"
    monkeypatch.setattr(cfg, "global_config_path", lambda: missing_global)
    from agent_worktrees import repos as repos_mod

    monkeypatch.setattr(
        repos_mod, "read_registry", lambda: repos_mod.ReposRegistry()
    )

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    def test_returns_string(self):
        result = cfg.detect_platform()
        assert result in ("windows", "wsl", "linux")

    def test_wsl_detection(self, tmp_path: Path, monkeypatch):
        """If /proc/version contains 'microsoft', detect as WSL."""
        proc_version = tmp_path / "proc_version"
        proc_version.write_text("Linux version 5.15.0-microsoft-standard")

        import io
        real_open = open

        def fake_open(f, *args, **kwargs):
            if str(f) == "/proc/version":
                return io.StringIO(proc_version.read_text())
            return real_open(f, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        assert cfg.detect_platform() == "wsl"


# ---------------------------------------------------------------------------
# project_name
# ---------------------------------------------------------------------------

class TestProjectName:
    def test_reads_active_project(self, monkeypatch):
        # The in-process active project (set by main() from CWD/--project) is
        # authoritative -- read ahead of any ambient env.
        monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
        cfg.set_active_project("test-project")
        assert cfg.project_name() == "test-project"

    def test_active_project_wins_over_env(self, monkeypatch):
        # CWD/flag resolution beats the transitional env fallback -- this is the
        # anti-contamination guarantee.
        monkeypatch.setenv("WORKTREE_PROJECT", "stale-env-project")
        cfg.set_active_project("resolved-project")
        assert cfg.project_name() == "resolved-project"

    def test_env_not_honored_when_unresolved(self, monkeypatch):
        # The transitional $WORKTREE_PROJECT fallback was retired (cwd-resolution
        # Phase 3): with no active project resolved, ambient env is NOT honored --
        # project_name() raises rather than silently trusting the environment.
        cfg.set_active_project(None)
        monkeypatch.setenv("WORKTREE_PROJECT", "stale-env-project")
        with pytest.raises(RuntimeError, match="No active project"):
            cfg.project_name()

    def test_raises_when_unset(self, monkeypatch):
        cfg.set_active_project(None)
        monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
        with pytest.raises(RuntimeError, match="No active project"):
            cfg.project_name()

    def test_raises_on_invalid_name(self):
        cfg.set_active_project("invalid name with spaces!")
        with pytest.raises(ValueError, match="Invalid"):
            cfg.project_name()

    def test_accepts_valid_names(self):
        for name in ["my-project", "dotfiles", "sample_project", "test.123"]:
            cfg.set_active_project(name)
            assert cfg.project_name() == name


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_install_dir(self):
        result = cfg.install_dir()
        assert result.name == ".agent-worktrees"

    def test_project_dir_with_name(self):
        result = cfg.project_dir("my-project")
        assert result.name == ".my-project"

    def test_agent_home_override_relocates_state_root(self, tmp_path, monkeypatch):
        # AGENT_HOME relocates the whole ~/.agent-* state tree to a sandbox.
        monkeypatch.setenv("AGENT_HOME", str(tmp_path))
        assert cfg._home() == tmp_path
        assert cfg.install_dir() == tmp_path / ".agent-worktrees"
        assert cfg.project_dir("proj") == tmp_path / ".proj"

    def test_agent_home_unset_uses_real_home(self, monkeypatch):
        monkeypatch.delenv("AGENT_HOME", raising=False)
        # Falls back to USERPROFILE/home -- not the sandbox.
        assert cfg._home() == cfg._home()          # stable
        assert cfg.install_dir().name == ".agent-worktrees"

    def test_tracking_dir(self, monkeypatch):
        cfg.set_active_project("test-proj")
        result = cfg.tracking_dir()
        assert result.name == "worktrees"
        assert ".test-proj" in str(result)


# ---------------------------------------------------------------------------
# Data model basics
# ---------------------------------------------------------------------------

class TestDataModels:
    def test_copilot_profile_defaults(self):
        profile = cfg.CopilotProfile(name="test", label="Test")
        assert profile.name == "test"
        assert profile.label == "Test"

    def test_session_backend_defaults_to_direct(self):
        backend = cfg.SessionBackendConfig()
        assert backend.kind == "direct"
        assert backend.is_ahp is False

    def test_repo_config(self):
        repo = cfg.RepoConfig(
            anchor="/tmp/repo",
            worktree_root="/tmp/worktrees",
            remote="origin",
            default_branch="main",
        )
        assert repo.anchor == "/tmp/repo"
        assert repo.remote == "origin"

    def test_repo_config_pr_defaults_disabled(self):
        repo = cfg.RepoConfig(anchor="/tmp/repo", worktree_root="/tmp/wt")
        assert repo.pr.enabled is False
        assert repo.pr.provider == "gitea"
        assert repo.pr.strategy == "detach"
        assert repo.pr.branch_prefix == "feature"

    def test_pr_config_defaults(self):
        pr = cfg.PRConfig()
        assert pr.enabled is False
        assert pr.provider == "gitea"
        assert pr.source_attribution is False
        # Auto-complete completion defaults.
        assert pr.approval_required is True
        assert pr.squash is True
        assert pr.delete_source_branch is True
        assert pr.bypass_policy is False
        assert pr.bypass_reason == ""
        # Merge/update policy defaults (#225).
        assert pr.branch_update_strategy == "rebase"
        assert pr.merge_strategy == "squash"
        assert pr.prefer_auto_merge is True


class TestSessionBackendConfig:
    def test_parses_explicit_ahp_backend(self):
        backend = cfg._parse_session_backend({
            "kind": "ahp",
            "endpoint_url": "ws://127.0.0.1:8765",
            "github_account": "octocat",
            "protocol_versions": ["0.7.0"],
        })
        assert backend.is_ahp
        assert backend.endpoint_url == "ws://127.0.0.1:8765"
        assert backend.github_account == "octocat"
        assert backend.protocol_versions == ("0.7.0",)

    @pytest.mark.parametrize(
        "raw, message",
        [
            ({"kind": "ahp"}, "endpoint_url"),
            ({"kind": "other"}, "kind"),
            (
                {
                    "kind": "ahp",
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "protocol_versions": [],
                },
                "protocol_versions",
            ),
            (
                {
                    "kind": "ahp",
                    "endpoint_url": "ws://127.0.0.1:8765",
                    "connect_timeout_seconds": True,
                },
                "connect_timeout_seconds",
            ),
        ],
    )
    def test_rejects_invalid_backend(self, raw, message):
        with pytest.raises(ValueError, match=message):
            cfg._parse_session_backend(raw)


# ---------------------------------------------------------------------------
# pr-workflow config parsing
# ---------------------------------------------------------------------------

class TestPRConfigParsing:
    def _write(self, path: Path, pr_block: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
            f"{pr_block}"
        )

    def test_pr_absent_defaults_disabled(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.repos["ext"].pr.enabled is False

    def test_pr_block_parsed(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      provider: github\n"
            "      strategy: keep-alive\n"
            "      branch_prefix: pr\n"
            "      source_attribution: true\n"
            "      required_body_sections: [Intent, Changes, Validation]\n",
        )
        conf = cfg.load_config(cfgfile)
        pr = conf.repos["ext"].pr
        assert pr.enabled is True
        assert pr.required is False
        assert pr.provider == "github"
        assert pr.strategy == "keep-alive"
        assert pr.branch_prefix == "pr"
        assert pr.source_attribution is True
        assert pr.required_body_sections == ("Intent", "Changes", "Validation")

    def test_pr_autocomplete_block_parsed(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      required: true\n"
            "      provider: azure-devops\n"
            "      api_base: https://your-org.visualstudio.com\n"
            "      automerge_label: auto-complete\n"
            "      approval_required: false\n"
            "      bypass_policy: true\n"
            "      bypass_reason: self-serve\n"
            "      squash: true\n"
            "      delete_source_branch: false\n",
        )
        conf = cfg.load_config(cfgfile)
        pr = conf.repos["ext"].pr
        assert pr.provider == "azure-devops"
        assert pr.automerge_label == "auto-complete"
        assert pr.approval_required is False
        assert pr.bypass_policy is True
        assert pr.bypass_reason == "self-serve"
        assert pr.squash is True
        assert pr.delete_source_branch is False

    def test_pr_required_parsed(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      required: true\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is True
        assert pr.required is True

    def test_pr_required_implies_enabled(self, tmp_path: Path):
        # ``required: true`` alone turns PR mode on even without ``enabled``.
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      required: true\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.required is True
        assert pr.enabled is True

    def test_pr_required_defaults_false(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.required is False

    def test_merge_update_policy_parsed(self, tmp_path: Path):
        # #225: the merge/update policy defaults are repo-overridable.
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      branch_update_strategy: merge\n"
            "      merge_strategy: merge\n"
            "      prefer_auto_merge: false\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.branch_update_strategy == "merge"
        assert pr.merge_strategy == "merge"
        assert pr.prefer_auto_merge is False

    def test_merge_update_policy_normalizes_bad_values(self, tmp_path: Path):
        # An unrecognized enum falls back to the default (no crash, no garbage).
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      branch_update_strategy: bogus\n"
            "      merge_strategy: FANCY\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.branch_update_strategy == "rebase"
        assert pr.merge_strategy == "squash"
        assert pr.prefer_auto_merge is True  # absent -> default

    def test_review_vocabulary_binding_defaults_empty(self, tmp_path: Path):
        # Binding-absent: the pr-* family fields default empty (no-op / no crash).
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "    pr:\n      enabled: true\n")
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.automerge_label == ""
        assert pr.hold_labels == ()
        assert pr.wip_title_prefixes == ()

    def test_review_vocabulary_binding_parsed(self, tmp_path: Path):
        # The multi-machine system hook: the pr: block supplies the review vocabulary.
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      required: true\n"
            "      automerge_label: auto-merge\n"
            "      hold_labels: [do-not-merge, needs-rebase, wip]\n"
            "      wip_title_prefixes: ['wip:', '[wip]', 'draft:']\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.automerge_label == "auto-merge"
        assert pr.hold_labels == ("do-not-merge", "needs-rebase", "wip")
        assert pr.wip_title_prefixes == ("wip:", "[wip]", "draft:")

    def test_review_vocabulary_scalar_and_blanks_coerced(self, tmp_path: Path):
        # A lone scalar becomes a 1-tuple; blank/whitespace entries are dropped
        # so a stray "" can't become a match-everything token.
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      hold_labels: do-not-merge\n"
            "      wip_title_prefixes: ['wip:', '', '  ']\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.hold_labels == ("do-not-merge",)
        assert pr.wip_title_prefixes == ("wip:",)


class TestInRepoPRPolicy:
    """In-repo config is the BASE for repo settings; machine-local overrides it."""

    def _write_machine(self, path: Path, anchor: Path, pr_block: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            f"    anchor: {anchor}\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: master\n"
            "    remote: origin\n"
            f"{pr_block}"
        )

    def test_inrepo_provides_base_when_no_machine_pr(self, tmp_path: Path):
        # In-repo policy applies when the machine-local file says nothing.
        anchor = tmp_path / "ext"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text(
            "pr:\n  enabled: true\n  required: true\n  provider: gitea\n"
        )
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(cfgfile, anchor)
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is True
        assert pr.required is True
        assert pr.provider == "gitea"

    def test_machine_local_overrides_inrepo_per_key(self, tmp_path: Path):
        # New precedence: machine-local wins per key over the in-repo base.
        anchor = tmp_path / "ext"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text(
            "pr:\n  required: true\n  provider: gitea\n  branch_prefix: feature\n"
        )
        cfgfile = tmp_path / "config.yaml"
        # Machine overrides provider only; required stays from the in-repo base.
        self._write_machine(
            cfgfile, anchor,
            "    pr:\n      provider: github\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.provider == "github"      # machine-local override wins
        assert pr.required is True          # in-repo base preserved
        assert pr.branch_prefix == "feature"

    def test_machine_local_used_when_no_inrepo(self, tmp_path: Path):
        anchor = tmp_path / "ext"
        anchor.mkdir()  # no in-repo config
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(
            cfgfile, anchor,
            "    pr:\n      enabled: true\n      provider: github\n",
        )
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is True
        assert pr.required is False
        assert pr.provider == "github"

    def test_malformed_inrepo_falls_back(self, tmp_path: Path):
        anchor = tmp_path / "ext"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text("pr: [not, a, mapping]\n")
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(
            cfgfile, anchor,
            "    pr:\n      enabled: true\n",
        )
        # Malformed in-repo -> ignored, machine-local used, no crash.
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is True


class TestControlPlaneRelatedPRTier:
    """A control-plane ``related.yaml`` (or ``<repo>-harness`` plugin) may carry a
    foreign repo's ``pr:`` block. It layers ABOVE the foreign repo's own in-repo
    ``pr`` and BELOW a machine-local ``repos.<name>.pr`` override."""

    def _write_machine(self, path: Path, anchor: Path, pr_block: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            f"    anchor: {anchor}\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
            f"{pr_block}"
        )

    def test_cp_related_pr_applies_when_no_inrepo_or_machine(self, tmp_path, monkeypatch):
        anchor = tmp_path / "ext"
        anchor.mkdir()  # no in-repo config
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(cfgfile, anchor)  # no machine-local pr
        monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {
            "ext": {
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
                "api_base": "https://your-org.visualstudio.com",
            },
        })
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is True
        assert pr.required is True
        assert pr.provider == "azure-devops"
        assert pr.api_base == "https://your-org.visualstudio.com"

    def test_cp_related_pr_overrides_inrepo_per_key(self, tmp_path, monkeypatch):
        anchor = tmp_path / "ext"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text(
            "pr:\n  enabled: false\n  provider: gitea\n  branch_prefix: feature\n"
        )
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(cfgfile, anchor)  # no machine-local pr
        # Control plane drives the workflow: overrides provider + enabled, but the
        # in-repo key it does not set (branch_prefix) is preserved.
        monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {
            "ext": {"enabled": True, "provider": "azure-devops"},
        })
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is True                 # cp over in-repo
        assert pr.provider == "azure-devops"      # cp over in-repo
        assert pr.branch_prefix == "feature"      # in-repo base preserved

    def test_machine_local_overrides_cp_related_per_key(self, tmp_path, monkeypatch):
        anchor = tmp_path / "ext"
        anchor.mkdir()  # no in-repo config
        cfgfile = tmp_path / "config.yaml"
        # Machine-local overrides provider only; cp's other keys survive.
        self._write_machine(
            cfgfile, anchor,
            "    pr:\n      provider: github\n",
        )
        monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {
            "ext": {
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
            },
        })
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.provider == "github"    # machine-local override wins
        assert pr.enabled is True         # cp preserved
        assert pr.required is True        # cp preserved

    def test_cp_related_pr_absent_is_noop(self, tmp_path, monkeypatch):
        anchor = tmp_path / "ext"
        anchor.mkdir()
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(cfgfile, anchor)
        # No cp entry for this repo -> unchanged (disabled default).
        monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {})
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.enabled is False

    def test_cp_related_pr_map_is_failsafe(self, monkeypatch):
        # Any error in control-plane discovery degrades to {} (never breaks a load).
        from agent_worktrees import related as _related

        def _boom(*a, **k):
            raise RuntimeError("registry blew up")

        monkeypatch.setattr(_related, "installed_plugin_related_anchors", _boom)
        monkeypatch.setattr(_related, "find_control_plane_anchor", _boom)
        assert cfg._control_plane_related_pr_map() == {}

    def test_load_config_can_skip_control_plane_related_pr(
        self, tmp_path, monkeypatch
    ):
        def _boom():
            raise AssertionError("control-plane PR overlay should be skipped")

        anchor = tmp_path / "ext"
        anchor.mkdir()
        cfgfile = tmp_path / "config.yaml"
        self._write_machine(cfgfile, anchor)
        monkeypatch.setattr(cfg, "_control_plane_related_pr_map", _boom)
        loaded = cfg.load_config(
            cfgfile,
            include_control_plane_related_pr=False,
        )
        assert isinstance(loaded, cfg.Config)

    def test_cp_related_pr_map_discovers_from_registry_e2e(self, tmp_path, monkeypatch):
        # End-to-end wiring: a registered control-plane repo whose related.yaml
        # carries a foreign repo's pr: block is discovered and surfaced.
        from agent_worktrees import related as _related
        from agent_worktrees import repos

        cp = tmp_path / "dotfiles"
        cp.mkdir()
        (cp / "machines.yaml").write_text(
            "control_plane:\n  project: dotfiles\nmachines: {}\n", encoding="utf-8")
        _related.write_related(cp, _related.RelatedConfig(related={
            "ext": _related.RelatedEntry(name="ext", role="tooling", pr={
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
                "api_base": "https://your-org.visualstudio.com",
            }),
            "no-pr-repo": _related.RelatedEntry(name="no-pr-repo", role="docs"),
        }))

        def _paths(p):
            s = str(p)
            return {"windows": s, "linux": s, "wsl": s}

        monkeypatch.setattr(repos, "list_repos", lambda class_filter=None: [
            repos.RepoEntry(name="dotfiles", repo_class="worktree", paths=_paths(cp)),
        ])
        monkeypatch.setattr(_related, "installed_plugin_related_anchors", lambda *a, **k: [])

        got = cfg._control_plane_related_pr_map()
        assert got == {
            "ext": {
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
                "api_base": "https://your-org.visualstudio.com",
            },
        }  # entries without a pr block are omitted

    def test_cp_related_pr_map_includes_knowledge_overlay(
        self, tmp_path, monkeypatch
    ):
        from agent_worktrees import related as _related
        from agent_worktrees import repos
        from agent_worktrees import state_root

        cp = tmp_path / "harness"
        knowledge = tmp_path / "knowledge"
        cp.mkdir()
        knowledge.mkdir()
        _related.write_related(cp, _related.RelatedConfig(related={
            "ext": _related.RelatedEntry(name="ext", role="tooling", pr={
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
            }),
        }))
        _related.write_related(knowledge, _related.RelatedConfig(related={
            "ext": _related.RelatedEntry(name="ext", role="tooling", pr={
                "enabled": True,
                "required": True,
                "provider": "azure-devops",
                "merge_actor": "submitter-direct",
            }),
        }))

        def _paths(path):
            value = str(path)
            return {"windows": value, "linux": value, "wsl": value}

        monkeypatch.setattr(repos, "list_repos", lambda class_filter=None: [
            repos.RepoEntry(name="harness", repo_class="worktree", paths=_paths(cp)),
        ])
        monkeypatch.setattr(_related, "find_control_plane_anchor", lambda: str(cp))
        monkeypatch.setattr(
            _related, "installed_plugin_related_anchors", lambda *a, **k: [])
        monkeypatch.setattr(
            state_root,
            "config_source_anchors",
            lambda config, **kwargs: [
                state_root.ConfigSource(anchor=str(cp), origin="harness"),
                state_root.ConfigSource(anchor=str(knowledge), origin="knowledge"),
            ],
        )
        seen = {}

        def _load_config(*args, **kwargs):
            seen.update(kwargs)
            return object()

        monkeypatch.setattr(cfg, "load_config", _load_config)

        got = cfg._control_plane_related_pr_map()

        assert seen == {
            "include_control_plane_related_pr": False,
            "project": "harness",
        }
        assert got["ext"]["merge_actor"] == "submitter-direct"

    def test_cp_related_pr_map_does_not_load_unproven_project(
        self, tmp_path, monkeypatch
    ):
        from agent_worktrees import related as _related
        from agent_worktrees import repos

        cp = tmp_path / "unregistered-control-plane"
        cp.mkdir()
        _related.write_related(cp, _related.RelatedConfig(related={
            "ext": _related.RelatedEntry(name="ext", role="tooling", pr={
                "enabled": True,
                "merge_actor": "submitter-direct",
            }),
        }))
        monkeypatch.setattr(_related, "find_control_plane_anchor", lambda: str(cp))
        monkeypatch.setattr(
            _related, "installed_plugin_related_anchors", lambda *a, **k: [])
        monkeypatch.setattr(repos, "list_repos", lambda class_filter=None: [])
        monkeypatch.setattr(
            cfg,
            "load_config",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("unproven project must not be loaded")
            ),
        )

        got = cfg._control_plane_related_pr_map()

        assert got["ext"]["merge_actor"] == "submitter-direct"


class TestLayeredConfig:
    """Three-tier merge: global < in-repo < machine-local; optional machine file."""

    def _machine(self, path: Path, anchor: Path, *, extra: str = "", pr: str = ""):
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            f"    anchor: {anchor}\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            f"{extra}{pr}"
        )

    def test_inrepo_dir_form_read(self, tmp_path: Path):
        # Preferred location: <anchor>/.agent-worktrees/config.yaml (dir form).
        anchor = tmp_path / "ext"
        (anchor / cfg.INREPO_CONFIG_DIRNAME).mkdir(parents=True)
        cfg.inrepo_config_path(anchor).write_text(
            "default_branch: main\nremote: upstream\n"
            "pr:\n  required: true\n  strategy: keep-alive\n"
        )
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.default_branch == "main"
        assert repo.remote == "upstream"
        assert repo.pr.required is True
        assert repo.pr.strategy == "keep-alive"

    def test_dir_form_wins_over_legacy_single_file(self, tmp_path: Path):
        anchor = tmp_path / "ext"
        (anchor / cfg.INREPO_CONFIG_DIRNAME).mkdir(parents=True)
        cfg.inrepo_config_path(anchor).write_text("pr:\n  provider: github\n")
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text("pr:\n  provider: gitea\n")
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.pr.provider == "github"  # dir form takes precedence

    def test_legacy_single_file_backcompat(self, tmp_path: Path):
        # Old .agent-worktrees.yaml (pr-only) still honored when no dir form.
        anchor = tmp_path / "ext"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text(
            "pr:\n  required: true\n  provider: gitea\n"
        )
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.pr.required is True
        assert repo.pr.provider == "gitea"

    # -- config.d drop-ins (service-contributed machine-local config) --------

    def test_config_d_dropin_merges_session_env(self, tmp_path: Path):
        # A config.d drop-in contributes repos.ext.session_env WITHOUT clobbering
        # the in-repo session_env (deep-merge), so both keys reach the session --
        # the vault-owns-SUDO_ASKPASS pattern.
        anchor = tmp_path / "ext"
        (anchor / cfg.INREPO_CONFIG_DIRNAME).mkdir(parents=True)
        cfg.inrepo_config_path(anchor).write_text(
            "session_env:\n  COPILOT_FEATURE_FLAGS: extensions\n"
        )
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)
        cdir = tmp_path / "config.d"
        cdir.mkdir()
        (cdir / "vault.yaml").write_text(
            "repos:\n  ext:\n    session_env:\n"
            "      SUDO_ASKPASS: /h/.local/bin/vault-askpass\n"
        )
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.session_env["COPILOT_FEATURE_FLAGS"] == "extensions"
        assert repo.session_env["SUDO_ASKPASS"] == "/h/.local/bin/vault-askpass"

    def test_config_yaml_wins_over_dropin(self, tmp_path: Path):
        # config.yaml (operator) overrides a drop-in on a conflicting scalar.
        anchor = tmp_path / "ext"
        anchor.mkdir()
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor, extra="    remote: from-config-yaml\n")
        cdir = tmp_path / "config.d"
        cdir.mkdir()
        (cdir / "z.yaml").write_text("repos:\n  ext:\n    remote: from-dropin\n")
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.remote == "from-config-yaml"

    def test_config_d_dropins_sorted_last_wins(self, tmp_path: Path):
        anchor = tmp_path / "ext"
        anchor.mkdir()
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)
        cdir = tmp_path / "config.d"
        cdir.mkdir()
        (cdir / "10-a.yaml").write_text("repos:\n  ext:\n    remote: a\n")
        (cdir / "20-b.yaml").write_text("repos:\n  ext:\n    remote: b\n")
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.remote == "b"

    def test_no_config_d_dir_is_fine(self, tmp_path: Path):
        anchor = tmp_path / "ext"
        anchor.mkdir()
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)  # no config.d dir alongside
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.remote == "origin"

    def test_global_carries_no_per_repo_settings(self, tmp_path: Path, monkeypatch):
        # The global tier holds only machine-wide top-level settings; any
        # per-repo keys placed there (e.g. repo_defaults) are NOT applied.
        gpath = tmp_path / "global.yaml"
        gpath.write_text(
            "repo_defaults:\n  remote: upstream\n  pr:\n    provider: github\n"
        )
        monkeypatch.setattr(cfg, "global_config_path", lambda: gpath)
        anchor = tmp_path / "ext"
        anchor.mkdir()  # no in-repo config -> repo defaults come from dataclass
        cfgfile = tmp_path / "config.yaml"
        self._machine(cfgfile, anchor)
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.remote == "origin"            # repo_defaults NOT applied
        assert repo.pr.provider == "gitea"        # default, not the global block

    def test_global_provides_toplevel_defaults(self, tmp_path: Path, monkeypatch):
        gpath = tmp_path / "global.yaml"
        gpath.write_text("srcroot: /global/src\nplatform: wsl\n")
        monkeypatch.setattr(cfg, "global_config_path", lambda: gpath)
        anchor = tmp_path / "ext"
        anchor.mkdir()
        # Machine-local omits srcroot -> falls back to global.
        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            "repo_name: ext\nmachine: anomalous-potato\nplatform: wsl\n"
            "repos:\n  ext:\n"
            f"    anchor: {anchor}\n"
            "    worktree_root: /tmp/wt\n"
        )
        conf = cfg.load_config(cfgfile)
        assert conf.srcroot == "/global/src"

    def test_machine_local_toplevel_overrides_global(self, tmp_path: Path, monkeypatch):
        gpath = tmp_path / "global.yaml"
        gpath.write_text("srcroot: /global/src\n")
        monkeypatch.setattr(cfg, "global_config_path", lambda: gpath)
        anchor = tmp_path / "ext"
        anchor.mkdir()
        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            "repo_name: ext\nsrcroot: /machine/src\nmachine: anomalous-potato\n"
            "platform: wsl\nrepos:\n  ext:\n"
            f"    anchor: {anchor}\n    worktree_root: /tmp/wt\n"
        )
        assert cfg.load_config(cfgfile).srcroot == "/machine/src"

    def test_convention_repo_no_machine_local_uses_registry(
        self, tmp_path: Path, monkeypatch
    ):
        # No machine-local file: anchor comes from the repos registry,
        # settings from the repo's own in-repo config.
        anchor = tmp_path / "ext"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text(
            "pr:\n  required: true\n  provider: gitea\n"
        )
        from agent_worktrees import repos as repos_mod

        registry = repos_mod.ReposRegistry(
            repos={
                "ext": repos_mod.RepoEntry(
                    name="ext", repo_class="worktree",
                    # All-platform paths so the anchor resolves regardless of
                    # the host's detected platform (no machine-local file here
                    # means platform = detection, which varies by CI host).
                    paths={"windows": str(anchor), "wsl": str(anchor),
                           "linux": str(anchor)},
                )
            }
        )
        monkeypatch.setattr(repos_mod, "read_registry", lambda: registry)
        cfg.set_active_project("ext")

        missing = tmp_path / "no-machine-config.yaml"  # does not exist
        conf = cfg.load_config(missing)
        repo = conf.repos["ext"]
        assert repo.anchor == str(anchor)
        assert repo.pr.required is True
        assert repo.pr.provider == "gitea"

    def test_no_repo_resolvable_raises(self, tmp_path: Path, monkeypatch):
        # No machine-local repos, empty registry -> cannot resolve any repo.
        cfg.set_active_project("ext")
        missing = tmp_path / "absent.yaml"
        with pytest.raises(ValueError, match="No repo could be resolved"):
            cfg.load_config(missing)

    def test_load_project_config_uses_named_project_without_machine_file(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        anchor = tmp_path / "owner"
        anchor.mkdir()
        (anchor / cfg.INREPO_CONFIG_FILENAME).write_text(
            "stateless: true\nrequires_external_state_root: true\n",
            encoding="utf-8",
        )
        from agent_worktrees import repos as repos_mod

        registry = repos_mod.ReposRegistry(
            repos={
                "owner": repos_mod.RepoEntry(
                    name="owner",
                    repo_class="worktree",
                    paths={
                        "windows": str(anchor),
                        "wsl": str(anchor),
                        "linux": str(anchor),
                    },
                )
            }
        )
        monkeypatch.setattr(repos_mod, "read_registry", lambda: registry)
        monkeypatch.setattr(
            cfg,
            "project_dir",
            lambda name=None: tmp_path / f".{name or cfg.project_name()}",
        )
        global_config = tmp_path / "global.yaml"
        global_config.write_text(
            "repo_name: provider\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cfg, "global_config_path", lambda: global_config
        )
        cfg.set_active_project("provider")

        conf = cfg.load_project_config("owner")

        assert conf.repo_name == "owner"
        assert conf.default_repo.stateless is True
        assert cfg.active_project() == "provider"

    def test_foreign_repo_machine_local_only(self, tmp_path: Path):
        # A foreign repo with no in-repo config loads purely from machine-local.
        anchor = tmp_path / "work-product"
        anchor.mkdir()  # no .agent-worktrees config in the repo
        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            "repo_name: ext\nmachine: anomalous-potato\nplatform: wsl\n"
            "repos:\n  ext:\n"
            f"    anchor: {anchor}\n    worktree_root: /tmp/wt\n"
            "    default_branch: develop\n"
            "    pr:\n      required: true\n"
        )
        repo = cfg.load_config(cfgfile).repos["ext"]
        assert repo.default_branch == "develop"
        assert repo.pr.required is True


class TestGlobalConfigUserOwned:
    """The global config is user-owned: scaffold-if-missing, never overwritten."""

    def test_scaffold_then_never_overwrite(self, tmp_path: Path, monkeypatch):
        from agent_worktrees import __main__ as m

        gpath = tmp_path / "global.yaml"
        monkeypatch.setattr(cfg, "global_config_path", lambda: gpath)

        m._write_global_config("mach", "wsl", "/src")
        assert gpath.exists()

        # User edits it (adds profiles); a subsequent install must NOT clobber.
        edited = gpath.read_text() + "\ncopilot_profiles:\n  - name: mine\n    label: x\n"
        gpath.write_text(edited)
        m._write_global_config("mach", "wsl", "/src")
        assert gpath.read_text() == edited  # untouched, profiles preserved


# ---------------------------------------------------------------------------
# worktree_root derivation (Copilot-aligned <anchor>.worktrees layout)
# ---------------------------------------------------------------------------

class TestWorktreeRootDerivation:
    def test_derive_helper_posix(self):
        assert cfg.derive_worktree_root("/tmp/src/ext") == "/tmp/src/ext.worktrees"

    def test_derive_helper_windows(self):
        assert (
            cfg.derive_worktree_root(r"D:\Src\dotfiles")
            == r"D:\Src\dotfiles.worktrees"
        )

    def test_derive_helper_strips_trailing_separator(self):
        assert cfg.derive_worktree_root("/tmp/src/ext/") == "/tmp/src/ext.worktrees"

    def _write(self, path: Path, worktree_root_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            f"{worktree_root_line}"
            "    default_branch: main\n"
            "    remote: origin\n"
        )

    def test_worktree_root_derived_when_absent(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.repos["ext"].worktree_root == "/tmp/src/ext.worktrees"

    def test_worktree_root_explicit_overrides(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "    worktree_root: /custom/wt/ext\n")
        conf = cfg.load_config(cfgfile)
        assert conf.repos["ext"].worktree_root == "/custom/wt/ext"


# ---------------------------------------------------------------------------
# headless project parsing
# ---------------------------------------------------------------------------

class TestHeadlessConfig:
    def _write(self, path: Path, headless_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            f"{headless_line}"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
        )

    def test_headless_true(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "headless: true\n")
        conf = cfg.load_config(cfgfile)
        assert conf.headless is True

    def test_headless_absent_defaults_false(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.headless is False


# ---------------------------------------------------------------------------
# E1e config.yaml knowledge overlay (#947)
# ---------------------------------------------------------------------------

class TestKnowledgeConfigOverlay:
    """The knowledge repo's config.yaml grafts portable operator prefs for a
    stateless harness -- a tier between the in-repo base and machine-local."""

    def _registry(self, monkeypatch, **name_to_anchor):
        from agent_worktrees import repos as repos_mod
        registry = repos_mod.ReposRegistry(repos={
            name: repos_mod.RepoEntry(
                name=name, repo_class="worktree",
                paths={"windows": str(a), "wsl": str(a), "linux": str(a)},
            )
            for name, a in name_to_anchor.items()
        })
        monkeypatch.setattr(repos_mod, "read_registry", lambda: registry)

    def _mk_harness(self, tmp_path, *, stateless=True):
        h = tmp_path / "harness"
        h.mkdir()
        body = "pr:\n  enabled: true\n"
        if stateless:
            body = "stateless: true\n" + body
        (h / cfg.INREPO_CONFIG_DIRNAME).mkdir(parents=True)
        (h / cfg.INREPO_CONFIG_DIRNAME / "config.yaml").write_text(body, encoding="utf-8")
        return h

    def _mk_knowledge(self, tmp_path, body):
        k = tmp_path / "knowledge"
        k.mkdir()
        (k / cfg.INREPO_CONFIG_DIRNAME).mkdir(parents=True)
        (k / cfg.INREPO_CONFIG_DIRNAME / "config.yaml").write_text(body, encoding="utf-8")
        return k

    def test_overlay_grafts_prefs_for_stateless_harness(self, tmp_path, monkeypatch):
        h = self._mk_harness(tmp_path, stateless=True)
        k = self._mk_knowledge(tmp_path, "headless: true\nnew_picker: false\n")
        self._registry(monkeypatch, harness=h, knowledge=k)
        # machine-local: binds the knowledge repo, does NOT set the prefs.
        mfile = tmp_path / "machine.yaml"
        mfile.write_text(
            "repo_name: harness\nmachine: m\nplatform: wsl\n"
            "knowledge_repo: knowledge\n"
            f"repos:\n  harness:\n    anchor: {h}\n    worktree_root: /tmp/wt\n",
            encoding="utf-8")
        conf = cfg.load_config(mfile)
        assert conf.headless is True        # from the knowledge overlay
        assert conf.new_picker is False     # from the knowledge overlay

    def test_overlay_may_arm_profile_assignment(self, tmp_path, monkeypatch):
        h = self._mk_harness(tmp_path, stateless=True)
        k = self._mk_knowledge(
            tmp_path,
            "copilot_profiles:\n"
            "  - name: p1\n"
            "  - name: p2\n"
            "profile_assignment:\n"
            "  name: portable-policy\n"
            "  mode: balanced-random\n"
            "  armed: true\n"
            "  profiles: [p1, p2]\n",
        )
        self._registry(monkeypatch, harness=h, knowledge=k)
        mfile = tmp_path / "machine.yaml"
        mfile.write_text(
            "repo_name: harness\nmachine: m\nplatform: wsl\n"
            "knowledge_repo: knowledge\n"
            f"repos:\n  harness:\n    anchor: {h}\n    worktree_root: /tmp/wt\n",
            encoding="utf-8",
        )

        policy = cfg.load_config(mfile).profile_assignment

        assert policy is not None
        assert policy.armed is True
        assert policy.name == "portable-policy"
        assert policy.profiles == ("p1", "p2")

    def test_machine_local_wins_over_overlay(self, tmp_path, monkeypatch):
        h = self._mk_harness(tmp_path, stateless=True)
        k = self._mk_knowledge(tmp_path, "headless: true\n")
        self._registry(monkeypatch, harness=h, knowledge=k)
        mfile = tmp_path / "machine.yaml"
        mfile.write_text(
            "repo_name: harness\nmachine: m\nplatform: wsl\n"
            "knowledge_repo: knowledge\nheadless: false\n"
            f"repos:\n  harness:\n    anchor: {h}\n    worktree_root: /tmp/wt\n",
            encoding="utf-8")
        # machine-local headless:false beats the knowledge overlay's true.
        assert cfg.load_config(mfile).headless is False

    def test_non_stateless_harness_ignores_overlay(self, tmp_path, monkeypatch):
        h = self._mk_harness(tmp_path, stateless=False)  # NOT stateless
        k = self._mk_knowledge(tmp_path, "headless: true\n")
        self._registry(monkeypatch, harness=h, knowledge=k)
        mfile = tmp_path / "machine.yaml"
        mfile.write_text(
            "repo_name: harness\nmachine: m\nplatform: wsl\n"
            "knowledge_repo: knowledge\n"
            f"repos:\n  harness:\n    anchor: {h}\n    worktree_root: /tmp/wt\n",
            encoding="utf-8")
        # Not stateless -> overlay never consulted -> default false.
        assert cfg.load_config(mfile).headless is False

    def test_overlay_excludes_machine_specifics(self, tmp_path, monkeypatch):
        # Even if the knowledge config.yaml sets srcroot, it must NOT graft.
        h = self._mk_harness(tmp_path, stateless=True)
        k = self._mk_knowledge(tmp_path, "srcroot: /knowledge/src\nheadless: true\n")
        self._registry(monkeypatch, harness=h, knowledge=k)
        mfile = tmp_path / "machine.yaml"
        mfile.write_text(
            "repo_name: harness\nmachine: m\nplatform: wsl\nsrcroot: /machine/src\n"
            "knowledge_repo: knowledge\n"
            f"repos:\n  harness:\n    anchor: {h}\n    worktree_root: /tmp/wt\n",
            encoding="utf-8")
        conf = cfg.load_config(mfile)
        assert conf.srcroot == "/machine/src"   # machine-specific, not grafted
        assert conf.headless is True            # a pref key, grafted

    def test_helper_fail_open_unbound(self, tmp_path, monkeypatch):
        # _load_knowledge_overlay_config returns {} when nothing is bound.
        self._registry(monkeypatch)
        assert cfg._load_knowledge_overlay_config({}, {}, "harness", "wsl") == {}


# ---------------------------------------------------------------------------
# auto_fast_forward parsing
# ---------------------------------------------------------------------------

class TestAutoFastForwardConfig:
    def _write(self, path: Path, extra_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: anomalous-potato\n"
            "platform: wsl\n"
            f"{extra_line}"
            "repos:\n"
            "  ext:\n"
            "    anchor: /tmp/src/ext\n"
            "    worktree_root: /tmp/src/.worktrees/ext\n"
            "    default_branch: main\n"
            "    remote: origin\n"
        )

    def test_defaults_true_when_absent(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile)
        conf = cfg.load_config(cfgfile)
        assert conf.auto_fast_forward is True

    def test_opt_out_false(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "auto_fast_forward: false\n")
        conf = cfg.load_config(cfgfile)
        assert conf.auto_fast_forward is False


# ---------------------------------------------------------------------------
# find_machine_entry -- hostnames are case-insensitive
# ---------------------------------------------------------------------------

class TestFindMachineEntry:
    def _entries(self):
        return {
            "CPC-tmich-OIXUI": cfg.MachineEntry(
                key="CPC-tmich-OIXUI",
                display_name="Dev Box",
                environment="Windows 11",
            ),
        }

    def test_exact_key(self):
        e = self._entries()
        assert cfg.find_machine_entry(e, "CPC-tmich-OIXUI") is not None

    def test_lowercased_key_matches(self):
        # register probes the hostname lowercased; it must still match a
        # mixed-case machines.yaml key.
        e = self._entries()
        assert cfg.find_machine_entry(e, "cpc-tmich-oixui") is not None

    def test_alias_case_insensitive(self):
        e = {
            "host1": cfg.MachineEntry(
                key="host1", display_name="H1", environment="x",
                alias="MyBox",
            ),
        }
        assert cfg.find_machine_entry(e, "mybox") is not None

    def test_decoupled_key_and_hostname(self):
        # A machine keyed by a friendly name declares its raw COMPUTERNAME via
        # `hostname:`; it must be findable by key, alias, hostname, or display_name.
        e = {
            "host-augloop1": cfg.MachineEntry(
                key="host-augloop1", display_name="augloop1",
                environment="Windows 11", alias="augloop1",
                hostname="cpc-tmich-oixui",
            ),
        }
        assert cfg.find_machine_entry(e, "host-augloop1") is not None   # key
        assert cfg.find_machine_entry(e, "augloop1") is not None           # alias/display
        assert cfg.find_machine_entry(e, "cpc-tmich-oixui") is not None    # hostname field
        assert cfg.find_machine_entry(e, "CPC-tmich-OIXUI") is not None    # hostname, case-insensitive

    def test_no_match_returns_none(self):
        assert cfg.find_machine_entry(self._entries(), "other") is None


# ---------------------------------------------------------------------------
# detect_machine -- COMPUTERNAME resolves via key, the hostname field, or alias
# ---------------------------------------------------------------------------

class TestDetectMachine:
    def _write(self, tmp_path: Path, body: str) -> Path:
        (tmp_path / "machines.yaml").write_text(body, encoding="utf-8")
        return tmp_path

    def test_detect_via_hostname_field(self, tmp_path: Path, monkeypatch):
        # Key is the friendly name; COMPUTERNAME is declared via `hostname:`.
        self._write(tmp_path, (
            "machines:\n"
            "  host-augloop1:\n"
            "    display_name: augloop1\n"
            "    alias: augloop1\n"
            "    hostname: cpc-tmich-oixui\n"
            "    environment: Windows 11\n"
        ))
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "CPC-tmich-OIXUI")
        assert cfg.detect_machine(tmp_path) == "augloop1"

    def test_detect_via_key(self, tmp_path: Path, monkeypatch):
        self._write(tmp_path, (
            "machines:\n"
            "  host-dev6:\n"
            "    display_name: dev6\n"
            "    environment: Windows 11\n"
        ))
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "host-dev6")
        assert cfg.detect_machine(tmp_path) == "host-dev6"

    def test_detect_falls_back_to_raw_hostname(self, tmp_path: Path, monkeypatch):
        self._write(tmp_path, (
            "machines:\n"
            "  host-dev6:\n"
            "    display_name: dev6\n"
            "    environment: Windows 11\n"
        ))
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "unknown-box")
        assert cfg.detect_machine(tmp_path) == "unknown-box"


# ---------------------------------------------------------------------------
# Registry fallback for adoption facts (minimal-overlay support)
# ---------------------------------------------------------------------------

def test_adoption_defaults_resolved_from_registries(monkeypatch):
    """default_branch from repos.yaml, base_repo from projects.yaml."""
    from agent_worktrees import installer
    from agent_worktrees import repos as repos_mod

    entry = repos_mod.RepoEntry(
        name="proj", repo_class="worktree", default_branch="main",
        paths={"linux": "/a"},
    )
    monkeypatch.setattr(
        repos_mod, "read_registry",
        lambda: repos_mod.ReposRegistry(repos={"proj": entry}),
    )
    monkeypatch.setattr(
        installer, "read_projects_registry",
        lambda: {"projects": {"proj": {"base_repo": True}}},
    )
    out = cfg._resolve_adoption_defaults_from_registry("proj", "linux")
    assert out == {"default_branch": "main", "base_repo": True}


def test_peek_base_repo_applies_machine_override(monkeypatch, tmp_path):
    machine = tmp_path / "machine.yaml"
    global_path = tmp_path / "global.yaml"
    machine.write_text(
        "repo_name: proj\nrepos:\n  proj:\n    base_repo: false\n",
        encoding="utf-8",
    )
    global_path.write_text("", encoding="utf-8")
    anchor = tmp_path / "repo"
    anchor.mkdir()
    monkeypatch.setattr(cfg, "default_config_path", lambda: machine)
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(
        cfg, "_resolve_anchor_from_registry", lambda _name, _platform: str(anchor)
    )
    monkeypatch.setattr(
        cfg,
        "_resolve_adoption_defaults_from_registry",
        lambda _name, _platform: {"base_repo": True},
    )
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")

    assert cfg.peek_base_repo() is False


def test_peek_base_repo_returns_none_when_project_is_unknown(monkeypatch, tmp_path):
    machine = tmp_path / "machine.yaml"
    global_path = tmp_path / "global.yaml"
    machine.write_text("", encoding="utf-8")
    global_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(cfg, "default_config_path", lambda: machine)
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(cfg, "_project_name_safe", lambda: "")

    assert cfg.peek_base_repo() is None


def test_load_config_fills_branch_and_base_repo_from_registry(
    tmp_path: Path, monkeypatch
):
    """A minimal overlay (no anchor/branch/base_repo) still resolves them from
    the registries -- so restating them in the overlay is redundant."""
    from agent_worktrees import installer
    from agent_worktrees import repos as repos_mod

    anchor = tmp_path / "proj"
    anchor.mkdir()
    entry = repos_mod.RepoEntry(
        name="proj", repo_class="worktree", default_branch="main",
        paths={"linux": str(anchor)},
    )
    monkeypatch.setattr(
        repos_mod, "read_registry",
        lambda: repos_mod.ReposRegistry(repos={"proj": entry}),
    )
    monkeypatch.setattr(
        installer, "read_projects_registry",
        lambda: {"projects": {"proj": {"base_repo": True}}},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")

    ml = tmp_path / "ml.yaml"
    ml.write_text(
        "repo_name: proj\nrepos:\n  proj:\n    env_script:\n      linux: p.sh\n",
        encoding="utf-8",
    )
    c = cfg.load_config(ml)
    repo = c.default_repo
    assert repo.anchor == str(anchor)      # from registry
    assert repo.default_branch == "main"   # registry fallback (not "master")
    assert repo.base_repo is True          # projects.yaml fallback


def test_overlay_branch_overrides_registry_fallback(
    tmp_path: Path, monkeypatch
):
    """An explicit overlay default_branch still wins over the registry."""
    from agent_worktrees import installer
    from agent_worktrees import repos as repos_mod

    anchor = tmp_path / "proj"
    anchor.mkdir()
    entry = repos_mod.RepoEntry(
        name="proj", repo_class="worktree", default_branch="main",
        paths={"linux": str(anchor)},
    )
    monkeypatch.setattr(
        repos_mod, "read_registry",
        lambda: repos_mod.ReposRegistry(repos={"proj": entry}),
    )
    monkeypatch.setattr(
        installer, "read_projects_registry", lambda: {"projects": {}},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")

    ml = tmp_path / "ml.yaml"
    ml.write_text(
        "repo_name: proj\nrepos:\n  proj:\n    default_branch: develop\n",
        encoding="utf-8",
    )
    c = cfg.load_config(ml)
    assert c.default_repo.default_branch == "develop"  # overlay wins
