"""Tests for agent_worktrees.codename: built-in generator, external hook
(bounded, validated, fail-closed), and collision-avoiding assignment."""

from __future__ import annotations

import random

from agent_worktrees.codename import (
    CODENAME_ADJECTIVES,
    CODENAME_NOUNS,
    MAX_HANDLE_LENGTH,
    assign_codename,
    generate_handle,
    generate_via_hook,
    is_valid_handle,
)


class TestBuiltinGenerator:
    def test_handle_is_well_formed(self) -> None:
        rng = random.Random(1)
        for _ in range(50):
            handle = generate_handle(rng=rng)
            assert is_valid_handle(handle)
            assert handle == handle.lower()
            assert " " not in handle
            assert len(handle) <= MAX_HANDLE_LENGTH

    def test_one_word_form_is_a_lone_noun(self) -> None:
        rng = random.Random(2)
        handle = generate_handle(words=1, rng=rng)
        assert handle in CODENAME_NOUNS
        assert "-" not in handle

    def test_two_word_form_is_adjective_noun(self) -> None:
        rng = random.Random(3)
        handle = generate_handle(words=2, rng=rng)
        adjective, _, noun = handle.partition("-")
        assert adjective in CODENAME_ADJECTIVES
        assert noun in CODENAME_NOUNS

    def test_deterministic_with_seeded_rng(self) -> None:
        a = generate_handle(rng=random.Random(42))
        b = generate_handle(rng=random.Random(42))
        assert a == b

    def test_no_product_theming_in_word_lists(self) -> None:
        """The built-in vocabulary must stay organization-neutral -- no
        product/franchise/brand terms, per CONTRIBUTING.md's contribution
        boundary. This is a narrow denylist smoke check, not exhaustive."""
        banned_substrings = (
            "aperture", "portal", "glados", "wheatley", "copilot", "github",
            "microsoft", "borealis",
        )
        for word in (*CODENAME_NOUNS, *CODENAME_ADJECTIVES):
            for banned in banned_substrings:
                assert banned not in word.lower()


class TestHandleValidation:
    def test_rejects_empty(self) -> None:
        assert not is_valid_handle("")

    def test_rejects_uppercase(self) -> None:
        assert not is_valid_handle("Some-Handle")

    def test_rejects_spaces(self) -> None:
        assert not is_valid_handle("some handle")

    def test_rejects_leading_or_trailing_hyphen(self) -> None:
        assert not is_valid_handle("-handle")
        assert not is_valid_handle("handle-")

    def test_rejects_double_hyphen(self) -> None:
        assert not is_valid_handle("some--handle")

    def test_rejects_path_separators(self) -> None:
        assert not is_valid_handle("some/handle")
        assert not is_valid_handle("some\\handle")

    def test_rejects_shell_metacharacters(self) -> None:
        for candidate in ("handle;rm -rf", "handle$(whoami)", "handle`id`"):
            assert not is_valid_handle(candidate)

    def test_rejects_oversized(self) -> None:
        assert not is_valid_handle("a" * (MAX_HANDLE_LENGTH + 1))

    def test_accepts_well_formed(self) -> None:
        assert is_valid_handle("quiet-gizmo")
        assert is_valid_handle("gizmo")
        assert is_valid_handle("gizmo123")


class TestGenerateViaHook:
    def test_empty_command_returns_none(self) -> None:
        assert generate_via_hook("") is None

    def test_valid_output_is_accepted(self) -> None:
        assert generate_via_hook("printf 'quiet-gizmo'") == "quiet-gizmo"

    def test_strips_trailing_newline(self) -> None:
        assert generate_via_hook("echo quiet-gizmo") == "quiet-gizmo"

    def test_malformed_output_is_rejected(self) -> None:
        # Uppercase and a space -- syntactically invalid, fails closed.
        assert generate_via_hook("printf 'Not A Handle'") is None

    def test_nonzero_exit_fails_closed(self) -> None:
        assert generate_via_hook("false") is None

    def test_timeout_fails_closed(self) -> None:
        assert generate_via_hook("sleep 5", timeout=0.05) is None

    def test_nonexistent_command_fails_closed(self) -> None:
        assert generate_via_hook("this-command-does-not-exist-anywhere") is None

    def test_semantically_identifying_but_syntactically_valid_output_still_passes(
        self,
    ) -> None:
        """Format validation is a SYNTAX check only (see the module
        docstring's trust-scope note) -- a hook printing a well-formed but
        identifying handle is accepted here. The public-safety guarantee
        for this path depends on the hook owner, not this function."""
        assert generate_via_hook("printf 'machine-20260917'") == "machine-20260917"


class TestAssignCodename:
    def test_returns_a_valid_handle_with_no_collisions(self) -> None:
        rng = random.Random(5)
        handle = assign_codename([], rng=rng)
        assert is_valid_handle(handle)

    def test_avoids_existing_handles(self) -> None:
        # Force determinism: same seed each call would repeat the same
        # handle, so seed fresh per call and assert the taken set is honored
        # by checking many draws never collide with a large "taken" set.
        taken = {generate_handle(rng=random.Random(i)) for i in range(20)}
        handle = assign_codename(taken, rng=random.Random(999), max_attempts=200)
        assert handle not in taken

    def test_raises_when_exhausted(self) -> None:
        # Only two possible one-word handles are "reachable" if we starve
        # the word list via a rng that always returns the same choice --
        # simulate exhaustion by taking every noun as a 1-word handle.
        taken = set(CODENAME_NOUNS)
        try:
            assign_codename(taken, words=1, max_attempts=10)
        except ValueError as exc:
            assert "could not assign" in str(exc)
        else:
            raise AssertionError("expected ValueError when the space is exhausted")

    def test_hook_success_short_circuits_builtin(self) -> None:
        handle = assign_codename(
            [], hook_command="printf 'from-the-hook'", rng=random.Random(1)
        )
        assert handle == "from-the-hook"

    def test_hook_failure_falls_back_to_builtin(self) -> None:
        handle = assign_codename(
            [], hook_command="false", rng=random.Random(1)
        )
        assert is_valid_handle(handle)
        assert handle != ""

    def test_hook_collision_retries_into_builtin(self) -> None:
        # The hook always returns the same (already-taken) handle; assignment
        # must retry with the builtin generator rather than loop forever
        # returning a taken value.
        handle = assign_codename(
            {"from-the-hook"},
            hook_command="printf 'from-the-hook'",
            rng=random.Random(3),
        )
        assert handle != "from-the-hook"
        assert is_valid_handle(handle)
