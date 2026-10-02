"""Tests for agent_worktrees.related_briefing -- generated per-repo briefings."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from dropin_registry import EntryStatus, ScanAuthority
from plugin_activation import ActivationReport

from agent_worktrees import related, related_briefing, state_root
from agent_worktrees.related import Locus, RelatedConfig, RelatedEntry


def _session_state_root() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    return Path(home) / ".copilot" / "session-state"


class TestRenderBriefing:
    def test_includes_role_summary_and_resolution_fields(self):
        entry = RelatedEntry(
            name="example-repo",
            role="product",
            summary="An example product repo.",
            locus=Locus(preferred="local"),
            delegate="agent-bridge",
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="worktree",
            repo_path="D:/Src/example-repo", adopted=True,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "# example-repo -- generated operating guide" in text
        assert "**Role:** product" in text
        assert "**Delegate:** agent-bridge" in text
        assert "**Editing model:** worktree" in text
        assert "An example product repo." in text
        # build_resolution's own step prose is reused verbatim, not reinvented.
        assert any(
            "example-repo create --json" in line for line in text.splitlines()
        )

    def test_registered_but_unmapped_class_is_reported_honestly(self):
        """A registered repo class this planner has no editing-model prose
        for (e.g. 'knowledge') must never be reported as an unregistered
        local gap -- registration succeeded, there's just no per-class
        detail to show."""
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="knowledge",
            repo_path="D:/Src/example-repo", adopted=True,
        )

        text = related_briefing.render_briefing(
            entry, resolution, doc_relpath=None, registered=True,
        )

        assert "**Editing model:** registered (no generated editing-model" in text
        assert "not registered locally" not in text

    def test_registered_unmapped_class_on_a_remote_locus_is_still_remote(self):
        """A registered repo (e.g. 'knowledge' class) whose preferred locus
        is a remote venue must still render the remote-venue label --
        registration never overrides locus-kind when the two disagree."""
        entry = RelatedEntry(
            name="example-repo", locus=Locus(preferred="codespace"),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="knowledge",
            repo_path="D:/Src/example-repo", adopted=True,
        )

        text = related_briefing.render_briefing(
            entry, resolution, doc_relpath=None, registered=True,
        )

        assert "**Editing model:** n/a (remote venue" in text
        assert "registered (no generated editing-model" not in text

    def test_unregistered_remote_venue_editing_model_is_not_a_gap(self):
        """A codespace/container/machine-locus repo is never edited from a
        local checkout -- an unregistered local repos.yaml entry there is
        expected, not a problem worth flagging as 'unknown'."""
        entry = RelatedEntry(
            name="example-repo",
            locus=Locus(preferred="codespace"),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Editing model:** n/a (remote venue" in text

    def test_unregistered_local_editing_model_is_a_real_gap(self):
        """A local-locus repo with no repos.yaml registration is a genuine
        gap -- the label must say so, not 'n/a (remote venue ...)'."""
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Editing model:** unknown (not registered locally" in text
        assert "repos find example-repo" in text

    def test_machine_locus_matching_current_machine_is_a_local_gap(self):
        """A 'machine:<target>' locus whose target IS the current machine is
        effectively a local checkout (build_resolution already emits
        local-edit steps for it) -- an unregistered repos.yaml entry there is
        a real gap, not the remote-venue 'n/a' case."""
        entry = RelatedEntry(
            name="example-repo", locus=Locus(preferred="machine:host-test"),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Editing model:** unknown (not registered locally" in text

    def test_machine_locus_on_a_different_machine_is_remote(self):
        """A 'machine:<target>' locus whose target is NOT this machine is a
        genuine remote-venue case."""
        entry = RelatedEntry(
            name="example-repo", locus=Locus(preferred="machine:other-host"),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Editing model:** n/a (remote venue" in text

    def test_local_locus_checked_out_elsewhere_is_not_a_local_gap(self):
        """A 'local' locus whose locus.machines names only other hosts is
        NOT available on this machine -- build_resolution sets
        available_here=False. Flagging that as an 'unknown (not registered
        locally)' gap alongside 'Not available on this machine' would be a
        second, contradictory story about the same remote checkout."""
        entry = RelatedEntry(
            name="example-repo",
            locus=Locus(preferred="local", machines=["other-host"]),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Editing model:** n/a (remote venue" in text
        assert "**Not available on this machine.**" in text

    def test_current_checkout_override_still_counts_as_local(self):
        """current_checkout_path means this entry IS the session's own
        checkout, overriding a resolved-config availability lag -- the
        editing-model label must follow that override too, not just the
        separate 'Not available' line."""
        entry = RelatedEntry(
            name="example-repo",
            locus=Locus(preferred="local", machines=["other-host"]),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(
            entry, resolution, doc_relpath=None,
            current_checkout_path="D:/Src/example-repo",
        )

        assert "**Editing model:** unknown (not registered locally" in text
        assert "**Not available on this machine.**" not in text

    def test_no_narrative_doc_suggests_scaffolding_it(self):
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="reference",
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "No hand-authored narrative doc exists yet" in text
        assert "related doc example-repo" in text

    def test_existing_narrative_doc_is_linked(self):
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="reference",
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(
            entry, resolution, doc_relpath="/anchor/.agent-worktrees/related/example-repo.md",
        )

        assert "A hand-authored narrative doc exists" in text
        assert "/anchor/.agent-worktrees/related/example-repo.md" in text

    def test_using_skill_is_cross_referenced_when_provided(self):
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="reference",
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(
            entry, resolution, doc_relpath=None,
            using_skills=["using-example-repo-codespaces"],
        )

        assert "## Richer venue skill" in text
        assert "`using-example-repo-codespaces`" in text

    def test_no_richer_venue_skill_section_when_none_found(self):
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        resolution = related.build_resolution(
            entry, current_machine="host-test", repo_class="reference",
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "Richer venue skill" not in text

    def test_unavailable_here_is_flagged(self):
        entry = RelatedEntry(
            name="example-repo",
            locus=Locus(preferred="container", container={"machines": ["dev6"]}),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-other", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Not available on this machine.**" in text

    def test_current_checkout_overrides_stale_unavailability(self):
        """A self-referential entry (this session's own repo) must never
        contradict the session's own observable checkout, even when the
        resolved config (e.g. a stale `locus.machines` list) says the repo
        is unavailable on this machine."""
        entry = RelatedEntry(
            name="example-repo",
            locus=Locus(preferred="local", machines=["dev6", "cloud1"]),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-book2", repo_class=None,
            repo_path=None, adopted=False,
        )
        assert resolution.available_here is False  # the stale config fact

        text = related_briefing.render_briefing(
            entry, resolution, doc_relpath=None,
            current_checkout_path="D:/Src/example-repo.worktrees/wt-1",
        )

        assert "**Not available on this machine.**" not in text
        assert "currently working in" in text
        assert "D:/Src/example-repo.worktrees/wt-1" in text
        assert "not checked out on" not in text.lower()
        assert "already in this repo's checkout" in text

    def test_current_checkout_without_override_still_shows_unavailable(self):
        """Confirms the override is additive: a normal (non-self) entry with
        the exact same stale-config shape is unaffected."""
        entry = RelatedEntry(
            name="some-other-repo",
            locus=Locus(preferred="local", machines=["dev6", "cloud1"]),
        )
        resolution = related.build_resolution(
            entry, current_machine="host-book2", repo_class=None,
            repo_path=None, adopted=False,
        )

        text = related_briefing.render_briefing(entry, resolution, doc_relpath=None)

        assert "**Not available on this machine.**" in text
        assert "currently working in" not in text


class TestFindUsingSkills:
    def _make_skill(
        self, root: Path, plugin: str, skill_name: str, *, nested: str | None = None,
    ) -> None:
        plugin_dir = root / plugin if nested is None else root / nested / plugin
        skill_dir = plugin_dir / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")

    def test_finds_flat_layout_match(self, tmp_path: Path):
        self._make_skill(tmp_path, "example-harness", "using-example-repo-codespaces")

        found = related_briefing._find_using_skills("example-repo", root=tmp_path)

        assert found == ["using-example-repo-codespaces"]

    def test_finds_nested_layout_match(self, tmp_path: Path):
        self._make_skill(
            tmp_path, "example-harness", "using-example-repo-containers",
            nested="example-harness",
        )

        found = related_briefing._find_using_skills("example-repo", root=tmp_path)

        assert found == ["using-example-repo-containers"]

    def test_no_match_for_unrelated_skill(self, tmp_path: Path):
        self._make_skill(tmp_path, "example-harness", "using-other-repo-codespaces")

        found = related_briefing._find_using_skills("example-repo", root=tmp_path)

        assert found == []

    def test_requires_skill_md_present(self, tmp_path: Path):
        skill_dir = tmp_path / "example-harness" / "skills" / "using-example-repo-codespaces"
        skill_dir.mkdir(parents=True)

        found = related_briefing._find_using_skills("example-repo", root=tmp_path)

        assert found == []

    def test_empty_repo_name_is_a_no_op(self, tmp_path: Path):
        assert related_briefing._find_using_skills("", root=tmp_path) == []

    def test_missing_root_is_a_no_op(self, tmp_path: Path):
        assert related_briefing._find_using_skills(
            "example-repo", root=(tmp_path / "does-not-exist"),
        ) == []

    def test_production_path_scans_only_active_plugin_live_roots(
        self, tmp_path: Path
    ):
        """No explicit root / env override -- the production path must walk
        the identity-verified active-plugin graph (never a raw filesystem
        scan that would also surface a disabled/stale/wrong-marketplace
        plugin's leftover payload)."""
        import json

        market = tmp_path / ".copilot" / ".ai"
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({
                "name": "local",
                "plugins": [
                    {"name": "active-harness", "source": "./active-harness"},
                    {"name": "inactive-harness", "source": "./inactive-harness"},
                ],
            }),
            encoding="utf-8",
        )
        self._make_skill(market, "active-harness", "using-example-repo-codespaces")
        self._make_skill(market, "inactive-harness", "using-example-repo-containers")
        for plugin_name in ("active-harness", "inactive-harness"):
            plugin_dir = market / plugin_name
            (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": plugin_name}), encoding="utf-8",
            )
        (tmp_path / ".copilot" / "settings.json").write_text(
            json.dumps({
                "extraKnownMarketplaces": {
                    "local": {"source": {"source": "directory", "path": "./.ai"}},
                },
                # Only active-harness is enabled -- inactive-harness's
                # payload is present on disk but must never be scanned.
                "enabledPlugins": {"active-harness@local": True},
            }),
            encoding="utf-8",
        )

        found = related_briefing._find_using_skills("example-repo", home=tmp_path)

        assert found == ["using-example-repo-codespaces"]

    def test_production_path_is_a_no_op_when_authority_is_indeterminate(
        self, tmp_path: Path
    ):
        # An unreadable settings.json (directory where a file is expected)
        # drives the activation scan to INDETERMINATE.
        (tmp_path / ".copilot" / "settings.json").mkdir(parents=True)

        assert related_briefing._find_using_skills("example-repo", home=tmp_path) == []

    def test_scoped_plugin_requires_matching_project(self, tmp_path: Path):
        """resolve_active_plugins() returns the full machine-wide graph
        across every registered project -- a plugin/skill enabled only
        under an unrelated project's scope must not be advertised to a
        session running in a different (or no) project."""
        from plugin_activation.resolver import ActivePlugin, ActivePluginRoot

        skill_root = tmp_path / "scoped-harness"
        self._make_skill(tmp_path, "scoped-harness", "using-example-repo-codespaces")
        report = ActivationReport(
            authority=ScanAuthority.COMPLETE,
            decisions={
                "scoped@local": SimpleNamespace(
                    status=EntryStatus.ACTIVE,
                    value=ActivePlugin(
                        source="local", name="scoped", marketplace="local",
                        root=skill_root, scopes=("project:other-repo",),
                        roots=(
                            ActivePluginRoot(
                                root=skill_root, scopes=("project:other-repo",),
                                kind="directory",
                            ),
                        ),
                    ),
                ),
            },
        )

        assert related_briefing._find_using_skills(
            "example-repo", active_report=report,
        ) == []
        assert related_briefing._find_using_skills(
            "example-repo", active_report=report, project_name="other-repo",
        ) == ["using-example-repo-codespaces"]


class TestPointerLine:
    def test_empty_list_renders_nothing(self):
        assert related_briefing.pointer_line([]) == ""

    def test_names_are_sorted_and_named(self):
        line = related_briefing.pointer_line(["zeta-repo", "alpha-repo"])
        assert "alpha-repo, zeta-repo" in line
        assert "files/related-briefings/<name>.md" in line


class TestAugmentSessionMessage:
    def test_appends_pointer_when_briefings_written(self, monkeypatch):
        monkeypatch.setattr(
            related_briefing, "write_related_briefings",
            lambda *a, **k: ["example-repo"],
        )

        result = related_briefing.augment_session_message(
            "base message", object(), object(), cwd=".", session_id="sid",
        )

        assert result.startswith("base message\n")
        assert "example-repo" in result

    def test_unchanged_when_no_briefings_written(self, monkeypatch):
        monkeypatch.setattr(
            related_briefing, "write_related_briefings", lambda *a, **k: [],
        )

        result = related_briefing.augment_session_message(
            "base message", object(), object(), cwd=".", session_id="sid",
        )

        assert result == "base message"

    def test_never_raises_on_internal_failure(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(related_briefing, "write_related_briefings", _boom)

        result = related_briefing.augment_session_message(
            "base message", object(), object(), cwd=".", session_id="sid",
        )

        assert result == "base message"


class TestWriteRelatedBriefings:
    def _patch_topology(self, monkeypatch, tmp_path: Path, topology: RelatedConfig):
        monkeypatch.setattr(
            state_root,
            "config_source_anchors",
            lambda *_a, **_k: [SimpleNamespace(anchor=str(tmp_path), origin="harness")],
        )
        monkeypatch.setattr(related, "installed_plugin_related_anchors", lambda **_k: [])
        # Keep write_related_briefings' own active-plugin resolution (used
        # for using-skill discovery, independent of installed_plugin_related
        # _anchors above) fast and hermetic -- most tests in this class don't
        # exercise that behavior and must never hit this machine's real
        # ~/.copilot state.
        monkeypatch.setattr(
            related, "resolve_active_plugins",
            lambda **_k: ActivationReport(authority=ScanAuthority.ABSENT, decisions={}),
        )
        monkeypatch.setattr(related, "read_related_grafted", lambda _anchors: topology)
        monkeypatch.setattr(related_briefing.repos, "find_repo", lambda _name: None)
        monkeypatch.setattr(related_briefing.doctor, "_read_projects", lambda: {})

    def _config_and_record(self, tmp_path: Path) -> tuple[SimpleNamespace, SimpleNamespace]:
        config = SimpleNamespace(
            machine="host-test",
            default_repo=SimpleNamespace(anchor=str(tmp_path)),
        )
        record = SimpleNamespace(worktree_path=str(tmp_path))
        return config, record

    def test_writes_one_file_per_related_repo(self, tmp_path: Path, monkeypatch):
        entry = RelatedEntry(
            name="example-repo", role="product",
            summary="An example product repo.", locus=Locus(preferred="local"),
        )
        topology = RelatedConfig(primary="example-repo", related={"example-repo": entry})
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        session_id = "test-session-0001"
        (self._session_dir(session_id)).mkdir(parents=True)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=session_id,
        )

        assert written == ["example-repo"]
        briefing = (
            self._session_dir(session_id)
            / "files" / "related-briefings" / "example-repo.md"
        )
        assert briefing.is_file()
        content = briefing.read_text(encoding="utf-8")
        assert "example-repo -- generated operating guide" in content
        assert "An example product repo." in content

    def test_self_referential_entry_gets_current_checkout_override(
        self, tmp_path: Path, monkeypatch,
    ):
        """When the related entry's name matches the worktree record's own
        repo, write_related_briefings must pass the real checkout path
        through so render_briefing never contradicts it."""
        entry = RelatedEntry(
            name="example-repo",
            locus=Locus(preferred="local", machines=["dev6", "cloud1"]),
        )
        topology = RelatedConfig(
            primary="example-repo", related={"example-repo": entry},
        )
        self._patch_topology(monkeypatch, tmp_path, topology)
        config = SimpleNamespace(
            machine="host-book2",
            default_repo=SimpleNamespace(anchor=str(tmp_path)),
        )
        record = SimpleNamespace(repo="example-repo", worktree_path=str(tmp_path))

        session_id = "test-session-0003"
        self._session_dir(session_id).mkdir(parents=True)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=session_id,
        )

        assert written == ["example-repo"]
        content = (
            self._session_dir(session_id)
            / "files" / "related-briefings" / "example-repo.md"
        ).read_text(encoding="utf-8")
        assert "**Not available on this machine.**" not in content
        assert "currently working in" in content

    def test_resolves_active_plugins_once_for_every_related_repo(
        self, tmp_path: Path, monkeypatch,
    ):
        """The active-plugin graph is resolved once for the whole write, not
        once per related repo -- that resolution re-verifies every
        registered project with Git subprocesses, so repeating it per entry
        would scale session-start latency with topology size."""
        entries = {
            f"repo-{i}": RelatedEntry(name=f"repo-{i}", locus=Locus(preferred="local"))
            for i in range(5)
        }
        topology = RelatedConfig(primary="repo-0", related=entries)
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        calls: list[object] = []
        real_resolve = related.resolve_active_plugins

        def _counting_resolve(**kwargs):
            calls.append(kwargs)
            return real_resolve(**kwargs)

        monkeypatch.setattr(related, "resolve_active_plugins", _counting_resolve)

        session_id = "test-session-0004"
        self._session_dir(session_id).mkdir(parents=True)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=session_id,
        )

        assert len(written) == 5
        assert len(calls) == 1

    def test_does_not_retry_resolution_after_a_failed_scan(
        self, tmp_path: Path, monkeypatch,
    ):
        """A failed activation scan must not be retried via
        installed_plugin_related_anchors' own "resolve if report is None"
        fallback -- that would repeat the same expensive, just-failed
        resolution a second time in the same write."""
        entries = {
            f"repo-{i}": RelatedEntry(name=f"repo-{i}", locus=Locus(preferred="local"))
            for i in range(3)
        }
        topology = RelatedConfig(primary="repo-0", related=entries)
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        calls: list[object] = []

        def _failing_resolve(**kwargs):
            calls.append(kwargs)
            raise OSError("boom")

        monkeypatch.setattr(related, "resolve_active_plugins", _failing_resolve)

        session_id = "test-session-0005"
        self._session_dir(session_id).mkdir(parents=True)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=session_id,
        )

        assert len(written) == 3
        assert len(calls) == 1

    def test_skips_resolution_entirely_under_filesystem_override(
        self, tmp_path: Path, monkeypatch,
    ):
        """Under AGENT_WORKTREES_INSTALLED_PLUGINS_DIR, both
        installed_plugin_related_anchors and _find_using_skills take the
        filesystem-scan branch and never consult a report -- resolving one
        here would be pure waste, never consumed by either."""
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        topology = RelatedConfig(primary="example-repo", related={"example-repo": entry})
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        calls: list[object] = []
        monkeypatch.setattr(
            related, "resolve_active_plugins",
            lambda **kwargs: calls.append(kwargs),
        )
        monkeypatch.setenv(related.INSTALLED_PLUGINS_ENV, str(tmp_path / "plugins"))

        session_id = "test-session-0006"
        self._session_dir(session_id).mkdir(parents=True)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=session_id,
        )

        assert written == ["example-repo"]
        assert calls == []

    def test_missing_session_id_returns_empty(self, tmp_path: Path, monkeypatch):
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        topology = RelatedConfig(primary="example-repo", related={"example-repo": entry})
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        assert related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=None,
        ) == []

    def test_missing_session_directory_fails_closed(self, tmp_path: Path, monkeypatch):
        entry = RelatedEntry(name="example-repo", locus=Locus(preferred="local"))
        topology = RelatedConfig(primary="example-repo", related={"example-repo": entry})
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id="never-created-session",
        )

        assert written == []

    def test_one_bad_entry_does_not_block_the_rest(self, tmp_path: Path, monkeypatch):
        good = RelatedEntry(name="good-repo", locus=Locus(preferred="local"))
        bad = RelatedEntry(name="bad-repo", locus=Locus(preferred="local"))
        topology = RelatedConfig(
            primary="good-repo", related={"good-repo": good, "bad-repo": bad},
        )
        self._patch_topology(monkeypatch, tmp_path, topology)
        config, record = self._config_and_record(tmp_path)

        real_build_resolution = related.build_resolution

        def _flaky_build_resolution(entry, **kwargs):
            if entry.name == "bad-repo":
                raise RuntimeError("boom")
            return real_build_resolution(entry, **kwargs)

        monkeypatch.setattr(related, "build_resolution", _flaky_build_resolution)

        session_id = "test-session-0002"
        self._session_dir(session_id).mkdir(parents=True)

        written = related_briefing.write_related_briefings(
            config, record, cwd=str(tmp_path), session_id=session_id,
        )

        assert written == ["good-repo"]

    @staticmethod
    def _session_dir(session_id: str) -> Path:
        return _session_state_root() / session_id
