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

    def test_env_is_transitional_fallback(self, monkeypatch):
        # With no active project resolved, the ambient env is honored only as a
        # transitional bridge (for internal/import-time callers).
        cfg.set_active_project(None)
        monkeypatch.setenv("WORKTREE_PROJECT", "test-project")
        assert cfg.project_name() == "test-project"

    def test_raises_when_unset(self, monkeypatch):
        cfg.set_active_project(None)
        monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
        with pytest.raises(RuntimeError, match="No active project"):
            cfg.project_name()

    def test_raises_on_invalid_name(self, monkeypatch):
        cfg.set_active_project(None)
        monkeypatch.setenv("WORKTREE_PROJECT", "invalid name with spaces!")
        with pytest.raises(ValueError, match="Invalid"):
            cfg.project_name()

    def test_accepts_valid_names(self, monkeypatch):
        for name in ["my-project", "dotfiles", "sample_project", "test.123"]:
            monkeypatch.setenv("WORKTREE_PROJECT", name)
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

    def test_tracking_dir(self, monkeypatch):
        monkeypatch.setenv("WORKTREE_PROJECT", "test-proj")
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
        # Auto-complete completion defaults.
        assert pr.approval_required is True
        assert pr.squash is True
        assert pr.delete_source_branch is True
        assert pr.bypass_policy is False
        assert pr.bypass_reason == ""


# ---------------------------------------------------------------------------
# pr-workflow config parsing
# ---------------------------------------------------------------------------

class TestPRConfigParsing:
    def _write(self, path: Path, pr_block: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
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
            "      branch_prefix: pr\n",
        )
        conf = cfg.load_config(cfgfile)
        pr = conf.repos["ext"].pr
        assert pr.enabled is True
        assert pr.required is False
        assert pr.provider == "github"
        assert pr.strategy == "keep-alive"
        assert pr.branch_prefix == "pr"

    def test_pr_autocomplete_block_parsed(self, tmp_path: Path):
        cfgfile = tmp_path / "config.yaml"
        self._write(
            cfgfile,
            "    pr:\n"
            "      enabled: true\n"
            "      required: true\n"
            "      provider: azure-devops\n"
            "      api_base: https://onedrive.visualstudio.com\n"
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

    def test_review_vocabulary_binding_defaults_empty(self, tmp_path: Path):
        # Binding-absent: the pr-* family fields default empty (no-op / no crash).
        cfgfile = tmp_path / "config.yaml"
        self._write(cfgfile, "    pr:\n      enabled: true\n")
        pr = cfg.load_config(cfgfile).repos["ext"].pr
        assert pr.automerge_label == ""
        assert pr.hold_labels == ()
        assert pr.wip_title_prefixes == ()

    def test_review_vocabulary_binding_parsed(self, tmp_path: Path):
        # The facility hook: the pr: block supplies the review vocabulary.
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
            "machine: lambda-core\n"
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


class TestLayeredConfig:
    """Three-tier merge: global < in-repo < machine-local; optional machine file."""

    def _machine(self, path: Path, anchor: Path, *, extra: str = "", pr: str = ""):
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
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
            "repo_name: ext\nmachine: lambda-core\nplatform: wsl\n"
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
            "repo_name: ext\nsrcroot: /machine/src\nmachine: lambda-core\n"
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
        monkeypatch.setenv("WORKTREE_PROJECT", "ext")

        missing = tmp_path / "no-machine-config.yaml"  # does not exist
        conf = cfg.load_config(missing)
        repo = conf.repos["ext"]
        assert repo.anchor == str(anchor)
        assert repo.pr.required is True
        assert repo.pr.provider == "gitea"

    def test_no_repo_resolvable_raises(self, tmp_path: Path, monkeypatch):
        # No machine-local repos, empty registry -> cannot resolve any repo.
        monkeypatch.setenv("WORKTREE_PROJECT", "ext")
        missing = tmp_path / "absent.yaml"
        with pytest.raises(ValueError, match="No repo could be resolved"):
            cfg.load_config(missing)

    def test_foreign_repo_machine_local_only(self, tmp_path: Path):
        # A foreign repo with no in-repo config loads purely from machine-local.
        anchor = tmp_path / "work-product"
        anchor.mkdir()  # no .agent-worktrees config in the repo
        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            "repo_name: ext\nmachine: lambda-core\nplatform: wsl\n"
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
            "machine: lambda-core\n"
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
            "machine: lambda-core\n"
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
# auto_fast_forward parsing
# ---------------------------------------------------------------------------

class TestAutoFastForwardConfig:
    def _write(self, path: Path, extra_line: str = "") -> None:
        path.write_text(
            "repo_name: ext\n"
            "srcroot: /tmp/src\n"
            "machine: lambda-core\n"
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

    def test_no_match_returns_none(self):
        assert cfg.find_machine_entry(self._entries(), "other") is None

