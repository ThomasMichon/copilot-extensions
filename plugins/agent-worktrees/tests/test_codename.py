"""Tests for agent_worktrees.codename: built-in generator, external hook
(bounded, validated, fail-closed), and collision-avoiding assignment."""

from __future__ import annotations

import base64
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agent_worktrees.codename import (
    CODENAME_ADJECTIVES,
    CODENAME_NOUNS,
    MAX_HANDLE_LENGTH,
    MAX_HOOK_TIMEOUT_SECONDS,
    assign_codename,
    generate_handle,
    generate_via_hook,
    is_valid_handle,
    is_valid_hook_timeout,
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


def _py(code: str) -> str:
    """Build a cross-platform shell command that runs ``code`` via the
    current Python interpreter -- avoids depending on POSIX-only shell
    builtins (`printf`/`false`/`sleep`), which aren't available under
    Windows' default `cmd.exe` shell that ``shell=True`` uses there."""
    return f'"{sys.executable}" -c "{code}"'


class TestIsValidHookTimeout:
    def test_accepts_positive_finite_numbers(self) -> None:
        assert is_valid_hook_timeout(5.0)
        assert is_valid_hook_timeout(5)
        assert is_valid_hook_timeout(0.001)

    def test_rejects_non_finite(self) -> None:
        assert not is_valid_hook_timeout(float("nan"))
        assert not is_valid_hook_timeout(float("inf"))
        assert not is_valid_hook_timeout(float("-inf"))

    def test_rejects_non_positive(self) -> None:
        assert not is_valid_hook_timeout(0)
        assert not is_valid_hook_timeout(-1)

    def test_rejects_bool(self) -> None:
        assert not is_valid_hook_timeout(True)
        assert not is_valid_hook_timeout(False)

    def test_rejects_non_numeric(self) -> None:
        assert not is_valid_hook_timeout("5")
        assert not is_valid_hook_timeout(None)

    def test_rejects_oversized_integer_without_raising(self) -> None:
        # math.isfinite() raises OverflowError for an int too large to
        # convert to float -- must return False, not propagate.
        assert not is_valid_hook_timeout(10**400)

    def test_rejects_very_large_finite_float(self) -> None:
        # "Finite" per math.isfinite() but large enough to risk overflowing
        # a platform wait-timeout conversion downstream (Popen.wait/thread
        # join) -- must be bounded, not just finite.
        assert not is_valid_hook_timeout(1e308)

    def test_accepts_up_to_the_max_bound(self) -> None:
        assert is_valid_hook_timeout(MAX_HOOK_TIMEOUT_SECONDS)
        assert not is_valid_hook_timeout(MAX_HOOK_TIMEOUT_SECONDS + 1)


class TestGenerateViaHook:
    def test_empty_command_returns_none(self) -> None:
        assert generate_via_hook("") is None

    def test_valid_output_is_accepted(self) -> None:
        assert generate_via_hook(_py("print('quiet-gizmo')")) == "quiet-gizmo"

    def test_strips_trailing_newline(self) -> None:
        # print() always appends a newline -- the accepted value must have
        # it stripped, not merely tolerate it (is_valid_handle rejects it).
        assert generate_via_hook(_py("print('quiet-gizmo')")) == "quiet-gizmo"

    def test_malformed_output_is_rejected(self) -> None:
        # Uppercase and a space -- syntactically invalid, fails closed.
        assert generate_via_hook(_py("print('Not A Handle')")) is None

    def test_invalid_utf8_output_fails_closed(self) -> None:
        # Bytes that are not valid UTF-8 must hit the explicit
        # UnicodeError catch, not raise out of generate_via_hook.
        invalid_utf8_bytes = "import sys; sys.stdout.buffer.write(bytes([0xff, 0xfe, 0x80]))"
        assert generate_via_hook(_py(invalid_utf8_bytes)) is None

    def test_nonzero_exit_fails_closed(self) -> None:
        assert generate_via_hook(_py("import sys; sys.exit(1)")) is None

    def test_timeout_fails_closed(self) -> None:
        assert (
            generate_via_hook(_py("import time; time.sleep(5)"), timeout=0.2)
            is None
        )

    def test_timeout_kills_the_whole_process_group(self) -> None:
        """A timed-out hook's descendants must not keep running/writing
        after generate_via_hook returns -- regression guard for the
        process-group isolation fix (a bare shell=True timeout only kills
        the immediate shell, not children it spawned)."""
        marker = Path(tempfile.gettempdir()) / f"codename-hook-marker-{os.getpid()}"
        if marker.exists():
            marker.unlink()
        try:
            # ``.as_posix()`` avoids backslash-escaping headaches when this
            # path is embedded, quoted, in the outer shell command below.
            child_code = (
                "import time; time.sleep(5); "
                f"open({marker.as_posix()!r}, 'w').close()"
            )
            result = generate_via_hook(_py(child_code), timeout=0.2)
            assert result is None
            # Give a leaked (not-actually-killed) child time to have written
            # the marker if process-group isolation were broken.
            time.sleep(0.5)
            assert not marker.exists(), (
                "hook's child process kept running past the timeout -- "
                "process-group kill did not reach it"
            )
        finally:
            if marker.exists():
                marker.unlink()

    def test_timeout_reaps_the_process_no_zombie_left_behind(
        self, monkeypatch
    ) -> None:
        """After a timeout, the Popen object itself must already be waited
        on (``poll()`` returns an exit status, not ``None``) -- regression
        guard for accumulating zombie/leaked child processes across many
        hook timeouts in a long-lived agent process."""
        import agent_worktrees.codename as codename_mod

        captured: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def _capture(*args: object, **kwargs: object) -> subprocess.Popen:
            process = real_popen(*args, **kwargs)  # type: ignore[arg-type]
            captured.append(process)
            return process

        monkeypatch.setattr(codename_mod.subprocess, "Popen", _capture)
        result = codename_mod.generate_via_hook(
            _py("import time; time.sleep(5)"), timeout=0.2
        )
        assert result is None
        assert captured, "expected the hook subprocess to have been created"
        assert captured[0].poll() is not None, (
            "hook process was killed but never reaped -- would accumulate "
            "as a zombie across repeated timeouts"
        )

    def test_successful_hook_still_cleans_up_a_backgrounded_descendant(
        self,
    ) -> None:
        """A hook can print a valid handle and exit immediately while
        leaving a *backgrounded* descendant alive in the same process
        group/session (e.g. it spawned a detached child before exiting).
        The shell process itself "succeeding" (no timeout) must not be
        treated as proof nothing is still running -- the whole group must
        be swept regardless of how the immediate process finished."""
        marker = (
            Path(tempfile.gettempdir())
            / f"codename-hook-descendant-marker-{os.getpid()}"
        )
        if marker.exists():
            marker.unlink()
        try:
            grandchild_code = (
                "import time; time.sleep(0.3); "
                f"open({marker.as_posix()!r}, 'w').close()"
            )
            # Base64-encode the grandchild source before embedding it in
            # parent_code: repr() of a string containing single quotes (the
            # marker path's own quoting, 'w') switches to double-quote
            # wrapping, which collides with _py()'s outer double-quoted
            # shell command -- nested-quote breakage, not a real subprocess
            # bug. Base64 sidesteps quoting entirely.
            encoded_grandchild = base64.b64encode(
                grandchild_code.encode()
            ).decode()
            # The grandchild must not inherit the parent's stdout (our read
            # pipe) -- otherwise the pipe never reaches EOF until the
            # grandchild itself exits/closes it, which would make this hit
            # the *timeout* path instead of the intended "shell exits
            # cleanly, descendant lingers" success path. Give it its own
            # devnull stdio, same process group (no new session).
            parent_code = (
                "import base64, subprocess, sys; "
                f"src = base64.b64decode('{encoded_grandchild}').decode(); "
                "subprocess.Popen([sys.executable, '-c', src], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
                "stdin=subprocess.DEVNULL); "
                "print('quiet-gizmo')"
            )
            # The hook process spawns a detached-looking grandchild (which
            # inherits the hook's process group/session since it is not
            # given its own) and then prints a valid handle and exits
            # immediately -- the "success" path, not the timeout path.
            result = generate_via_hook(_py(parent_code), timeout=2.0)
            assert result == "quiet-gizmo"
            # Give a leaked (not-actually-killed) grandchild time to have
            # written the marker if group-sweep-on-success were missing.
            # Comfortably longer than the grandchild's own 0.3s delay --
            # the earlier version of this test slept for less time than
            # the grandchild's delay and could not have caught the leak
            # this test exists to catch; see the effort README's Journal.
            time.sleep(1.0)
            assert not marker.exists(), (
                "a descendant backgrounded by a successful hook kept "
                "running after generate_via_hook returned -- the whole "
                "process group was not swept on the success path"
            )
        finally:
            if marker.exists():
                marker.unlink()

    def test_nonexistent_command_fails_closed(self) -> None:
        assert generate_via_hook("this-command-does-not-exist-anywhere") is None

    def test_invalid_timeout_fails_closed_without_raising(self) -> None:
        for bad_timeout in (float("nan"), float("inf"), -1.0, 0.0, True):
            assert generate_via_hook(_py("print('quiet-gizmo')"), timeout=bad_timeout) is None

    def test_oversized_integer_timeout_fails_closed_without_raising(self) -> None:
        # math.isfinite() raises OverflowError for an int too large to
        # convert to float -- must fail closed, not propagate.
        assert generate_via_hook(_py("print('quiet-gizmo')"), timeout=10**400) is None

    def test_oversized_output_is_rejected_not_buffered_unbounded(self) -> None:
        # A hook that emits far more than a handle could ever need must be
        # rejected (too long once validated), and must not be read in full.
        huge = _py("print('x' * 1_000_000)")
        assert generate_via_hook(huge) is None

    def test_truncated_output_is_rejected_not_validated_as_a_stripped_prefix(
        self,
    ) -> None:
        """A hook printing a valid handle padded with enough trailing
        whitespace to fill (and exceed) the read cap must be rejected
        outright -- not truncated to the cap, `.strip()`-ed down to just
        the valid-looking prefix, and accepted. That would let oversized
        output "rescue" itself by front-loading a valid handle."""
        # 'quiet-gizmo' + far more padding than the cap can hold.
        padded = _py("print('quiet-gizmo' + ' ' * 1000)")
        assert generate_via_hook(padded) is None

    def test_semantically_identifying_but_syntactically_valid_output_still_passes(
        self,
    ) -> None:
        """Format validation is a SYNTAX check only (see the module
        docstring's trust-scope note) -- a hook printing a well-formed but
        identifying handle is accepted here. The public-safety guarantee
        for this path depends on the hook owner, not this function."""
        assert (
            generate_via_hook(_py("print('machine-20260917')"))
            == "machine-20260917"
        )


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
            [],
            hook_command=_py("print('from-the-hook')"),
            rng=random.Random(1),
        )
        assert handle == "from-the-hook"

    def test_hook_failure_falls_back_to_builtin(self) -> None:
        handle = assign_codename(
            [],
            hook_command=_py("import sys; sys.exit(1)"),
            rng=random.Random(1),
        )
        assert is_valid_handle(handle)
        assert handle != ""

    def test_hook_collision_retries_into_builtin(self) -> None:
        # The hook always returns the same (already-taken) handle; assignment
        # must retry with the builtin generator rather than loop forever
        # returning a taken value.
        handle = assign_codename(
            {"from-the-hook"},
            hook_command=_py("print('from-the-hook')"),
            rng=random.Random(3),
        )
        assert handle != "from-the-hook"
        assert is_valid_handle(handle)
