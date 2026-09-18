"""Public-safe codename generation for worktrees (effort:
``pr-attribution-codenames``, issue #2838).

A codename is a short, branch/filename-safe handle assigned to a worktree so
a public PR marker (or, historically, a leaked branch name) can carry a
lookup key without decoding any machine/worktree/session/timestamp
information. Two sources of a handle:

- **Built-in generator** -- a small, organization-neutral word list shipped
  with this plugin. Deliberately whimsical but *not* themed after any
  product, franchise, or brand: this is a general-purpose public plugin
  (see CONTRIBUTING.md's contribution boundary), so the *default* vocabulary
  stays neutral. A control repo wanting its own flavor uses the hook below.
- **External generator hook** -- an adopter-configured shell command that
  prints one handle to stdout. This is how a private control repo plugs in
  its own themed vocabulary without that vocabulary ever living in this
  public plugin.

Format validation on hook output is a **syntax** check only -- it proves the
output is branch-safe, not that it is semantically non-identifying (a hook
could print a syntactically valid but identifying value, e.g.
``machine-20260917``). The informationless/public-safe guarantee this
module offers therefore holds unconditionally only for the built-in
generator; a configured hook is an explicit trust decision made by whoever
owns that hook's vocabulary.
"""

from __future__ import annotations

import math
import os
import random
import re
import signal
import subprocess
import threading
import time
from collections.abc import Iterable

from agent_procutil import no_window_flags

# Deliberately generic and whimsical -- small mechanical/workshop objects and
# their moods, not tied to any product, franchise, or brand. Kept short so
# generated handles stay compact; extend either list to grow variety.
CODENAME_NOUNS: tuple[str, ...] = (
    "gizmo", "widget", "sprocket", "gadget", "contraption", "doohickey",
    "cog", "pulley", "lever", "hinge", "bolt", "rivet", "spanner", "wrench",
    "bracket", "bearing", "flywheel", "valve", "gauge", "dial", "switch",
    "circuit", "capacitor", "resistor", "beacon", "compass", "lantern",
    "satchel", "toolbox", "ledger", "blueprint", "workbench", "crate",
)
CODENAME_ADJECTIVES: tuple[str, ...] = (
    "rusty", "humming", "lopsided", "tinkling", "restless", "squeaky",
    "wobbly", "gleaming", "tarnished", "nimble", "stubborn", "tidy",
    "patient", "curious", "drowsy", "brisk", "quiet", "sturdy", "faded",
    "polished", "crooked", "steady", "eager", "placid",
)

#: Branch/filename-safe handle: one or more lowercase-alnum segments joined
#: by single hyphens, no leading/trailing hyphen, no double hyphen. ``\Z``
#: (not ``$``) so a trailing newline can never sneak a match past the end of
#: the intended value -- ``$`` matches immediately before a final ``\n`` too.
HANDLE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")

#: A hard ceiling on any handle (built-in or hook-sourced) -- long enough for
#: a 3-word handle plus a suffix, short enough to never strain a branch name.
MAX_HANDLE_LENGTH = 64

#: Read at most this many bytes of a hook's stdout before giving up on it --
#: a broken/adversarial hook must never let this process buffer unbounded
#: output while validating a handle that can only ever be
#: :data:`MAX_HANDLE_LENGTH` bytes long anyway.
_MAX_HOOK_STDOUT_BYTES = MAX_HANDLE_LENGTH + 16

DEFAULT_HOOK_TIMEOUT_SECONDS = 5.0


def is_valid_handle(value: str) -> bool:
    """Whether ``value`` is a well-formed handle: lowercase, hyphen-joined,
    alnum segments only, within :data:`MAX_HANDLE_LENGTH`.

    This is a **syntax** check. It rejects malformed output (wrong case,
    spaces, path separators, shell metacharacters, a trailing newline,
    empty/oversized strings) but cannot prove the value is semantically
    non-identifying -- see the module docstring.
    """
    if not value or len(value) > MAX_HANDLE_LENGTH:
        return False
    return bool(HANDLE_RE.match(value))


def generate_handle(*, words: int = 2, rng: random.Random | None = None) -> str:
    """Generate one built-in handle from the neutral word list.

    ``words`` is 1 (a lone noun) or 2 (adjective-noun); any other value is
    treated as 2. Uses ``rng`` if given (for deterministic tests), otherwise
    the module-level random source.
    """
    r = rng or random
    noun = r.choice(CODENAME_NOUNS)
    if words == 1:
        return noun
    adjective = r.choice(CODENAME_ADJECTIVES)
    return f"{adjective}-{noun}"


def is_valid_hook_timeout(timeout: object) -> bool:
    """Whether ``timeout`` is a finite, positive, non-boolean number.

    Rejects ``NaN``/``inf``/``-inf`` (which raise inside
    :meth:`subprocess.Popen.wait`/``communicate`` instead of failing
    closed), non-positive values, ``bool`` (a ``bool`` is an ``int``
    subclass in Python, so ``isinstance(True, (int, float))`` is true --
    checked explicitly to reject it before it silently becomes ``1``), and
    an integer too large to convert to ``float`` (``math.isfinite`` raises
    ``OverflowError`` for one instead of returning ``False``).
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return False
    try:
        return math.isfinite(timeout) and timeout > 0
    except OverflowError:
        return False


def _process_group_kwargs() -> dict:
    """Popen kwargs that put the hook in its own process group/session, so a
    timeout can kill the whole subtree (a pipeline's later stages, a
    background child) -- not just the immediate shell. Mirrors the pattern
    already used for this plugin's other bounded hook subprocesses (see
    ``_start_project_session_hook``/``_finish_project_session_hook`` in
    ``__main__.py``)."""
    if os.name == "posix":
        return {"start_new_session": True}
    return {
        "creationflags": (
            no_window_flags()
            | getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",  # headless-guard: allow bounded hook child in its own process group while stdout/stderr stay piped (same rationale as _start_project_session_hook in __main__.py)
                0,
            )
        ),
    }


def _kill_process_group(process: subprocess.Popen) -> None:
    """Best-effort kill of ``process`` and its whole process group/session,
    then **reap** it (``wait()``) so a repeatedly-timing-out hook can't
    accumulate zombie/leaked child processes in this long-lived agent
    process.

    On POSIX, ``killpg`` reaches the whole session started via
    ``start_new_session=True``. On Windows, ``CTRL_BREAK_EVENT`` only
    reaches processes that installed a console control handler --
    typically just the immediate shell under ``shell=True``, not a
    pipeline's later stages or a background child -- so it is not a
    reliable tree-termination mechanism; ``taskkill /T /F`` (this repo's
    existing Windows tree-kill tool, e.g. in ``install.ps1``) recurses the
    whole descendant tree instead.
    """
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _read_bounded_stdout(
    process: subprocess.Popen, *, deadline: float
) -> tuple[bytes, bool]:
    """Read at most :data:`_MAX_HOOK_STDOUT_BYTES` from ``process.stdout`` in
    a daemon thread, joined against ``deadline`` (a :func:`time.monotonic`
    timestamp) so a hook that never writes and never exits cannot block this
    process past its timeout.

    Returns ``(data, timed_out)``. ``data`` is empty when ``timed_out`` is
    true or the read otherwise failed.
    """
    box: dict[str, bytes] = {}

    def _reader() -> None:
        try:
            assert process.stdout is not None
            box["data"] = process.stdout.read(_MAX_HOOK_STDOUT_BYTES)
        except OSError:
            box["data"] = b""

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    remaining = max(0.0, deadline - time.monotonic())
    reader.join(remaining)
    if reader.is_alive():
        return b"", True
    return box.get("data", b""), False


def generate_via_hook(
    command: str,
    *,
    timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
) -> str | None:
    """Run an adopter-configured external generator hook and return its
    handle, or ``None`` on any failure (invalid timeout, spawn failure,
    timeout, non-zero exit, malformed/non-UTF-8/oversized output) --
    **fail-closed**: the caller must fall back to :func:`generate_handle`,
    never propagate an invalid value.

    ``command`` is executed via the shell (so an adopter can compose a
    pipeline), in its own process group/session so a timeout can kill the
    whole subtree, not just the immediate shell. At most
    :data:`_MAX_HOOK_STDOUT_BYTES` of stdout is read regardless of how much
    the hook tries to write (bounded memory); stderr is discarded entirely.
    The result is stripped and validated with :func:`is_valid_handle` before
    being accepted. This proves the output is branch-safe; it does NOT
    prove the output is non-identifying -- see the module docstring's
    trust-scope note.
    """
    if not command or not is_valid_hook_timeout(timeout):
        return None
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_process_group_kwargs(),
        )
    except OSError:
        return None
    try:
        raw, timed_out = _read_bounded_stdout(process, deadline=deadline)
        if timed_out:
            _kill_process_group(process)
            return None
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            return None
    finally:
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
    if process.returncode != 0:
        return None
    try:
        candidate = raw.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return None
    if not is_valid_handle(candidate):
        return None
    return candidate


def assign_codename(
    existing: Iterable[str],
    *,
    hook_command: str = "",
    hook_timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
    words: int = 2,
    max_attempts: int = 50,
    rng: random.Random | None = None,
) -> str:
    """Assign a codename not already present in ``existing``.

    Invokes the external hook (if ``hook_command`` is set) **at most once**:
    a hook is an external command, not assumed cheap or safe to spam on
    every collision retry. If it fails validation (fail-closed, per
    :func:`generate_via_hook`) or its output collides with ``existing``,
    assignment falls back to the built-in generator's own retry loop
    (``max_attempts`` tries) rather than re-invoking the hook. Local
    collision avoidance only -- cross-machine uniqueness is a separate,
    shared-registry concern, not this function's job.
    """
    taken = set(existing)
    if hook_command:
        candidate = generate_via_hook(hook_command, timeout=hook_timeout)
        if candidate is not None and candidate not in taken:
            return candidate
    for _ in range(max_attempts):
        candidate = generate_handle(words=words, rng=rng)
        if candidate not in taken:
            return candidate
    raise ValueError(
        f"could not assign a unique codename after {max_attempts} attempts"
    )
