"""Tests for agent_worktrees.codename: built-in generator, declarative
wordlist file loading, and collision-avoiding assignment."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from agent_worktrees.codename import (
    CODENAME_ADJECTIVES,
    CODENAME_NOUNS,
    DEFAULT_WORDLIST,
    MAX_HANDLE_LENGTH,
    MAX_WORD_LENGTH,
    Wordlist,
    WordlistError,
    assign_codename,
    generate_handle,
    is_valid_handle,
    load_wordlist,
    load_wordlist_or_default,
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

    def test_rejects_trailing_newline(self) -> None:
        # `$` (unlike `\Z`) matches immediately before a final newline --
        # regression guard for that class of bug.
        assert not is_valid_handle("quiet-gizmo\n")

    def test_accepts_well_formed(self) -> None:
        assert is_valid_handle("quiet-gizmo")
        assert is_valid_handle("gizmo")
        assert is_valid_handle("gizmo123")


class TestWordlistTwoWordChoices:
    def test_default_is_full_cross_product(self) -> None:
        wl = Wordlist(nouns=("cube", "sphere"), adjectives=("red", "blue"))
        choices = wl.two_word_choices()
        assert len(choices) == 4
        assert ("red", "cube") in choices
        assert ("blue", "sphere") in choices

    def test_explicit_pairs_override_cross_product(self) -> None:
        wl = Wordlist(
            nouns=("cube", "sphere"),
            adjectives=("red", "blue"),
            pairs=(("red", "cube"),),
        )
        assert wl.two_word_choices() == (("red", "cube"),)


class TestLoadWordlist:
    def _write(self, tmp_path: Path, name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_valid_yaml_nouns_only(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: [cube, sphere]\n")
        wl = load_wordlist(path)
        assert wl.nouns == ("cube", "sphere")
        # No adjectives declared -- falls back to the built-in adjectives
        # for cross-product purposes.
        assert wl.adjectives == CODENAME_ADJECTIVES

    def test_loads_valid_yaml_with_adjectives(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "words.yaml",
            "nouns: [cube, sphere]\nadjectives: [red, blue]\n",
        )
        wl = load_wordlist(path)
        assert wl.nouns == ("cube", "sphere")
        assert wl.adjectives == ("red", "blue")
        assert len(wl.two_word_choices()) == 4

    def test_loads_valid_yaml_with_explicit_pairs(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "words.yaml",
            "nouns: [cube]\npairs:\n  - [red, cube]\n  - [blue, cube]\n",
        )
        wl = load_wordlist(path)
        assert wl.two_word_choices() == (("red", "cube"), ("blue", "cube"))

    def test_loads_valid_json(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "words.json",
            '{"nouns": ["cube", "sphere"], "adjectives": ["red"]}',
        )
        wl = load_wordlist(path)
        assert wl.nouns == ("cube", "sphere")
        assert wl.adjectives == ("red",)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(WordlistError):
            load_wordlist(tmp_path / "does-not-exist.yaml")

    def test_invalid_utf8_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "words.yaml"
        path.write_bytes(b"nouns: [\xff\xfe]\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: [unterminated\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.json", "{not valid json")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_non_mapping_top_level_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "- just\n- a\n- list\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_unknown_top_level_field_raises(self, tmp_path: Path) -> None:
        # A typo like 'adjectivs' must be rejected, not silently ignored
        # (which would otherwise fall back to the built-in adjectives
        # without ever surfacing the mistake).
        path = self._write(
            tmp_path, "words.yaml", "nouns: [cube]\nadjectivs: [red]\n"
        )
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_missing_nouns_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "adjectives: [red]\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_null_nouns_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: null\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_explicit_null_adjectives_raises(self, tmp_path: Path) -> None:
        # An explicit `adjectives: null` must be treated as malformed, not
        # silently equivalent to omitting the key (which falls back to the
        # built-in adjectives) -- data.get(key) can't tell the two apart,
        # only `key in data` can.
        path = self._write(
            tmp_path, "words.yaml", "nouns: [cube]\nadjectives: null\n"
        )
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_explicit_null_pairs_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: [cube]\npairs: null\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_empty_nouns_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: []\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_non_list_nouns_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: cube\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_non_string_noun_item_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: [cube, 7]\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_uppercase_noun_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: [Cube]\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_hyphenated_noun_raises(self, tmp_path: Path) -> None:
        # Words themselves must not contain hyphens -- the generator adds
        # exactly one hyphen itself; a word supplying its own would let a
        # file inject an extra, unaccounted-for handle segment.
        path = self._write(tmp_path, "words.yaml", "nouns: [not-a-word]\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_oversized_word_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "words.yaml", f"nouns: [{'a' * (MAX_WORD_LENGTH + 1)}]\n"
        )
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_empty_pairs_list_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "words.yaml", "nouns: [cube]\npairs: []\n")
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_malformed_pair_shape_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "words.yaml", "nouns: [cube]\npairs:\n  - [red]\n"
        )
        with pytest.raises(WordlistError):
            load_wordlist(path)

    def test_malformed_pair_word_raises(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "words.yaml", "nouns: [cube]\npairs:\n  - [Red, cube]\n"
        )
        with pytest.raises(WordlistError):
            load_wordlist(path)


class TestLoadWordlistOrDefault:
    def test_empty_path_returns_default(self) -> None:
        assert load_wordlist_or_default("") is DEFAULT_WORDLIST

    def test_missing_file_falls_back_to_default(self, tmp_path: Path) -> None:
        wl = load_wordlist_or_default(str(tmp_path / "nope.yaml"))
        assert wl is DEFAULT_WORDLIST

    def test_malformed_file_falls_back_to_default(self, tmp_path: Path) -> None:
        path = tmp_path / "words.yaml"
        path.write_text("nouns: []\n", encoding="utf-8")
        wl = load_wordlist_or_default(str(path))
        assert wl is DEFAULT_WORDLIST

    def test_invalid_utf8_file_falls_back_to_default(self, tmp_path: Path) -> None:
        path = tmp_path / "words.yaml"
        path.write_bytes(b"nouns: [\xff\xfe]\n")
        wl = load_wordlist_or_default(str(path))
        assert wl is DEFAULT_WORDLIST

    def test_valid_file_is_used(self, tmp_path: Path) -> None:
        path = tmp_path / "words.yaml"
        path.write_text("nouns: [cube]\n", encoding="utf-8")
        wl = load_wordlist_or_default(str(path))
        assert wl.nouns == ("cube",)


class TestAssignCodename:
    def test_returns_a_valid_handle_with_no_collisions(self) -> None:
        rng = random.Random(5)
        handle = assign_codename([], rng=rng)
        assert is_valid_handle(handle)

    def test_avoids_existing_handles(self) -> None:
        taken = {generate_handle(rng=random.Random(i)) for i in range(20)}
        handle = assign_codename(taken, rng=random.Random(999), max_attempts=200)
        assert handle not in taken

    def test_raises_when_exhausted(self) -> None:
        taken = set(CODENAME_NOUNS)
        with pytest.raises(ValueError, match="could not assign"):
            assign_codename(taken, words=1, max_attempts=10)

    def test_uses_the_provided_wordlist(self) -> None:
        wl = Wordlist(nouns=("cube",), pairs=(("red", "cube"),))
        handle = assign_codename([], rng=random.Random(1), wordlist=wl)
        assert handle == "red-cube"
