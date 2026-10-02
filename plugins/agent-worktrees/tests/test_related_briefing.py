"""Tests for agent_worktrees.related_briefing -- generated per-repo briefings."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

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
        monkeypatch.setattr(related, "installed_plugin_related_anchors", lambda: [])
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
