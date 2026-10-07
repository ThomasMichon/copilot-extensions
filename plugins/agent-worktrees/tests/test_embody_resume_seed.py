"""Unit tests for ``embody_resume``'s pure launch-argv composition helpers."""

from __future__ import annotations

from agent_worktrees import embody_resume


class TestWithResume:
    def test_appends_resume_flag_when_target_given(self):
        assert embody_resume.with_resume(["copilot"], "abc123") == [
            "copilot", "--resume=abc123",
        ]

    def test_leaves_cmd_unchanged_without_a_target(self):
        assert embody_resume.with_resume(["copilot"], None) == ["copilot"]

    def test_returns_a_new_list_not_the_same_object(self):
        original = ["copilot"]
        result = embody_resume.with_resume(original, None)
        assert result == original
        assert result is not original


class TestWithSeed:
    def test_appends_the_full_interactive_flag_when_seed_given(self):
        """Never the short -i: PowerShell's own argument parser can
        intercept it before it ever reaches the exec'd copilot process on
        Windows (resume-prompt-durable-seed-and-mux-fix)."""
        result = embody_resume.with_seed(["copilot"], "do the thing")
        assert result == ["copilot", "--interactive", "do the thing"]
        assert "-i" not in result

    def test_leaves_cmd_unchanged_without_a_seed(self):
        assert embody_resume.with_seed(["copilot"], None) == ["copilot"]

    def test_leaves_cmd_unchanged_for_an_empty_seed(self):
        assert embody_resume.with_seed(["copilot"], "") == ["copilot"]

    def test_returns_a_new_list_not_the_same_object(self):
        original = ["copilot"]
        result = embody_resume.with_seed(original, None)
        assert result == original
        assert result is not original

    def test_composes_with_with_resume_for_resume_plus_prompt_in_one_breath(self):
        """The validated design this effort is named for: resuming a
        session's history AND delivering a new prompt via a SINGLE process
        launch, with no mux pane to target either way."""
        cmd = embody_resume.with_resume(["copilot"], "abc123")
        cmd = embody_resume.with_seed(cmd, "what did I ask you to remember?")
        assert cmd == [
            "copilot", "--resume=abc123", "--interactive",
            "what did I ask you to remember?",
        ]
