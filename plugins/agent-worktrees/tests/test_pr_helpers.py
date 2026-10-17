"""Pure PR naming, body-validation, and patch-identity helpers."""

from __future__ import annotations

import pytest
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops
from pr_test_helpers import _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.helpers")


class TestSlugify:
    def test_basic(self):
        assert pr_ops.slugify("Fix the auth bug") == "fix-the-auth-bug"

    def test_strips_special_chars(self):
        assert pr_ops.slugify("Fix: handle #42 & more!") == "fix-handle-42-more"

    def test_collapses_and_trims_dashes(self):
        assert pr_ops.slugify("  --Hello---World--  ") == "hello-world"

    def test_truncates(self):
        s = pr_ops.slugify("a" * 100, max_len=10)
        assert len(s) <= 10

    def test_empty_falls_back(self):
        assert pr_ops.slugify("!!!") == "change"


class TestRequiredBodySections:
    def test_requires_visible_content_under_each_heading(self):
        body = (
            "## Intent\nShip the change.\n\n"
            "## Changes\n<!-- placeholder -->\n\n"
            "## Validation\nTests pass.\n"
        )
        assert pr_ops.missing_required_body_sections(
            body,
            ("Intent", "Changes", "Validation"),
        ) == ["Changes"]

    def test_accepts_case_insensitive_markdown_headings(self):
        body = (
            "# intent\nShip the change.\n"
            "### CHANGES\nUpdated behavior.\n"
            "## Validation ##\nTests pass.\n"
        )
        assert pr_ops.missing_required_body_sections(
            body,
            ("Intent", "Changes", "Validation"),
        ) == []

    def test_multiline_comments_are_not_visible_content(self):
        body = "## Changes\n<!--\nTODO\n-->\n"
        assert pr_ops.missing_required_body_sections(
            body,
            ("Changes",),
        ) == ["Changes"]

    def test_unterminated_comment_is_hidden_through_eof(self):
        body = "## Changes\n<!-- TODO"
        assert pr_ops.missing_required_body_sections(
            body,
            ("Changes",),
        ) == ["Changes"]

    def test_nested_headings_remain_inside_required_section(self):
        body = (
            "## Changes\n"
            "### Added\n"
            "Implemented the feature.\n"
            "## Validation\n"
            "Tests pass.\n"
        )
        assert pr_ops.missing_required_body_sections(
            body,
            ("Changes", "Validation"),
        ) == []

    def test_fenced_markdown_does_not_supply_required_heading(self):
        body = "```markdown\n## Changes\nFake content.\n```\n"
        assert pr_ops.missing_required_body_sections(
            body,
            ("Changes",),
        ) == ["Changes"]

    def test_fence_with_trailing_text_does_not_close_block(self):
        body = (
            "```markdown\n"
            "```not-a-close\n"
            "## Changes\n"
            "Fake content.\n"
            "```\n"
        )
        assert pr_ops.missing_required_body_sections(
            body,
            ("Changes",),
        ) == ["Changes"]

    def test_html_comment_literal_inside_fence_does_not_hide_following_sections(self):
        body = (
            "```html\n"
            "<!-- literal example\n"
            "```\n"
            "## Changes\n"
            "Implemented the feature.\n"
        )
        assert pr_ops.missing_required_body_sections(
            body,
            ("Changes",),
        ) == []


class TestFeatureBranchName:
    def test_uses_suffix_and_slug(self):
        name = pr_ops.feature_branch_name(
            "feature", "Fix auth", "anomalous-potato-win-20260618-173440-ac0d"
        )
        assert name == "feature/fix-auth-ac0d"

    def test_default_prefix(self):
        name = pr_ops.feature_branch_name("", "Title", "wt-abcd")
        assert name.startswith("feature/")
        assert name.endswith("-abcd")


class TestWorktreeSuffixHelper:
    """`_worktree_suffix` (used for branch-name suffixes) must never surface
    the raw worktree_id -- which embeds the authoring machine name and
    creation timestamp -- since branch names can reach a public repo.
    """

    def test_worktree_suffix_matches_git_ops(self):
        worktree_id = "example-host-20260917-125245-3a94"
        assert pr_ops._worktree_suffix(worktree_id) == \
            git_ops.worktree_suffix(worktree_id) == "3a94"

    def test_no_dash_worktree_id_never_returned_verbatim(self):
        """A worktree id with no dash has no trailing token to extract; the
        suffix must still never be the raw id itself (a legacy/malformed
        tracking id is exactly the kind of unusual input `create_pr` may
        still see, so this can't be assumed away)."""
        worktree_id = "nodashesatall"
        suffix = pr_ops._worktree_suffix(worktree_id)
        assert suffix != worktree_id
        # Deterministic: the same input always yields the same digest.
        assert pr_ops._worktree_suffix(worktree_id) == suffix


class TestResolveHeadPattern:
    def test_azure_devops_defaults_to_user_namespace_under_refspec(self):
        prcfg = cfg.PRConfig(enabled=True, provider="azure-devops", head_scheme="refspec")
        assert pr_ops.resolve_head_pattern(prcfg) == "user/{username}/{slug}-{suffix}"

    def test_azure_devops_defaults_to_user_namespace_under_snapshot(self):
        prcfg = cfg.PRConfig(enabled=True, provider="azure-devops", head_scheme="snapshot")
        assert pr_ops.resolve_head_pattern(prcfg) == "user/{username}/{slug}-{suffix}"

    def test_explicit_head_pattern_still_wins_for_azure_devops(self):
        prcfg = cfg.PRConfig(
            enabled=True,
            provider="azure-devops",
            head_scheme="snapshot",
            head_pattern="submit/{slug}-{suffix}",
        )
        assert pr_ops.resolve_head_pattern(prcfg) == "submit/{slug}-{suffix}"

    def test_github_and_gitea_keep_existing_scheme_defaults(self):
        assert pr_ops.resolve_head_pattern(
            cfg.PRConfig(enabled=True, provider="github", head_scheme="refspec")
        ) == "pr/{slug}-{suffix}"
        assert pr_ops.resolve_head_pattern(
            cfg.PRConfig(enabled=True, provider="gitea", head_scheme="snapshot")
        ) == "{prefix}/{slug}-{suffix}"


class TestPRHeadName:
    def test_snapshot_default_matches_feature_branch_name(self):
        prcfg = cfg.PRConfig(enabled=True, branch_prefix="feature", head_scheme="snapshot")
        assert pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa") == \
            pr_ops.feature_branch_name("feature", "Add auth", "wt-x-aaaa")
        assert pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa") == "feature/add-auth-aaaa"

    def test_refspec_default_is_pr_namespace(self):
        prcfg = cfg.PRConfig(enabled=True, head_scheme="refspec")
        assert pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa") == "pr/add-auth-aaaa"

    def test_explicit_user_pattern_resolves_username(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        _git("init", cwd=repo)
        _git("config", "user.email", "contributor_user@example.com", cwd=repo)
        prcfg = cfg.PRConfig(enabled=True, head_pattern="user/{username}/{slug}-{suffix}")
        name = pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa", cwd=str(repo))
        assert name == "user/contributor-user/add-auth-aaaa"

    def test_azure_devops_default_renders_username_tokens(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        _git("init", cwd=repo)
        _git("config", "user.email", "operator@example.com", cwd=repo)
        prcfg = cfg.PRConfig(enabled=True, provider="azure-devops", head_scheme="refspec")
        name = pr_ops.pr_head_name(prcfg, "Some title", "wt-x-a1b2", cwd=str(repo))
        assert name == "user/operator/some-title-a1b2"

    def test_snapshot_default_inserts_topic_when_supplied(self):
        prcfg = cfg.PRConfig(enabled=True, branch_prefix="feature", head_scheme="snapshot")
        name = pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa", topic="Hot Fix!!")
        assert name == "feature/add-auth-hot-fix-aaaa"

    def test_refspec_default_inserts_topic_when_supplied(self):
        prcfg = cfg.PRConfig(enabled=True, provider="github", head_scheme="refspec")
        name = pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa", topic="Mini Task")
        assert name == "pr/add-auth-mini-task-aaaa"

    def test_azure_devops_default_inserts_topic_when_supplied(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        _git("init", cwd=repo)
        _git("config", "user.email", "operator@example.com", cwd=repo)
        prcfg = cfg.PRConfig(enabled=True, provider="azure-devops", head_scheme="refspec")
        name = pr_ops.pr_head_name(
            prcfg, "Some title", "wt-x-a1b2", cwd=str(repo), topic="Topic!! Name"
        )
        assert name == "user/operator/some-title-topic-name-a1b2"

    def test_blank_topic_preserves_existing_default_output(self):
        prcfg = cfg.PRConfig(enabled=True, provider="github", head_scheme="refspec")
        assert pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa", topic="") == \
            "pr/add-auth-aaaa"
        assert pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa", topic="   ") == \
            "pr/add-auth-aaaa"

    def test_explicit_pattern_can_reference_topic(self):
        prcfg = cfg.PRConfig(enabled=True, head_pattern="submit/{slug}-{topic}-{suffix}")
        name = pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa", topic="Hot Fix")
        assert name == "submit/add-auth-hot-fix-aaaa"

    def test_sanitizes_unresolved_segments(self):
        # No cwd -> username falls back to "user"; no empty // segments.
        prcfg = cfg.PRConfig(enabled=True, head_pattern="user/{username}/{slug}-{suffix}")
        name = pr_ops.pr_head_name(prcfg, "X", "wt-aaaa")
        assert "//" not in name
        assert name == "user/user/x-aaaa"

    def test_malformed_pattern_falls_back(self):
        prcfg = cfg.PRConfig(enabled=True, head_pattern="{nope}/{slug}")
        name = pr_ops.pr_head_name(prcfg, "Add auth", "wt-x-aaaa")
        assert name == "feature/add-auth-aaaa"


class TestPatchId:
    """#898: squash-invariant patch-id for durable downstream references."""

    def _git(self, cwd, *args):
        import subprocess
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True)

    def _repo(self, tmp_path):
        import subprocess
        d = tmp_path / "r"
        d.mkdir()
        self._git(d, "init", "-q")
        self._git(d, "config", "user.email", "t@t")
        self._git(d, "config", "user.name", "t")
        (d / "a.txt").write_text("one\n")
        self._git(d, "add", "-A")
        self._git(d, "commit", "-qm", "base")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                              capture_output=True, text=True).stdout.strip()
        return d, base

    def test_patch_id_is_squash_invariant(self, tmp_path):
        # The same change content yields the same patch-id whether committed as
        # two commits or squashed into one -- so a recorder survives the squash.
        d, base = self._repo(tmp_path)
        # Two commits carrying the change.
        (d / "a.txt").write_text("one\ntwo\n")
        self._git(d, "commit", "-aqm", "c1")
        (d / "b.txt").write_text("bee\n")
        self._git(d, "add", "-A")
        self._git(d, "commit", "-qm", "c2")
        pid_multi = pr_ops._patch_id(base, "HEAD", cwd=str(d))
        assert pid_multi  # non-empty

        # Reset to base and apply the SAME net change as one squashed commit.
        self._git(d, "reset", "--hard", base, "-q")
        (d / "a.txt").write_text("one\ntwo\n")
        (d / "b.txt").write_text("bee\n")
        self._git(d, "add", "-A")
        self._git(d, "commit", "-qm", "squashed")
        pid_squash = pr_ops._patch_id(base, "HEAD", cwd=str(d))
        assert pid_squash == pid_multi  # squash-invariant

    def test_patch_id_empty_on_no_diff_or_bad_base(self, tmp_path):
        d, base = self._repo(tmp_path)
        assert pr_ops._patch_id(base, "HEAD", cwd=str(d)) == ""  # no diff
        assert pr_ops._patch_id("", "HEAD", cwd=str(d)) == ""    # no base

    def test_commit_patch_ids_batches_range(self, tmp_path, monkeypatch):
        d, base = self._repo(tmp_path)
        (d / "a.txt").write_text("one\ntwo\n")
        self._git(d, "commit", "-aqm", "c1")
        first = _git("rev-parse", "HEAD", cwd=d)
        (d / "b.txt").write_text("bee\n")
        self._git(d, "add", "-A")
        self._git(d, "commit", "-qm", "c2")
        second = _git("rev-parse", "HEAD", cwd=d)
        expected = {
            pr_ops._patch_id(base, first, cwd=str(d)): {first},
            pr_ops._patch_id(first, second, cwd=str(d)): {second},
        }
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong.git"))
        monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "wrong-index"))

        patch_ids = pr_ops._commit_patch_ids(base, "HEAD", cwd=str(d))

        assert patch_ids == expected
