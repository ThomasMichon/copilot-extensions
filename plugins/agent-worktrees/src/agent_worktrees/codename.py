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

import random
import re
import subprocess
from collections.abc import Iterable

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
#: by single hyphens, no leading/trailing hyphen, no double hyphen.
HANDLE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: A hard ceiling on any handle (built-in or hook-sourced) -- long enough for
#: a 3-word handle plus a suffix, short enough to never strain a branch name.
MAX_HANDLE_LENGTH = 64

DEFAULT_HOOK_TIMEOUT_SECONDS = 5.0


def is_valid_handle(value: str) -> bool:
    """Whether ``value`` is a well-formed handle: lowercase, hyphen-joined,
    alnum segments only, within :data:`MAX_HANDLE_LENGTH`.

    This is a **syntax** check. It rejects malformed output (wrong case,
    spaces, path separators, shell metacharacters, empty/oversized strings)
    but cannot prove the value is semantically non-identifying -- see the
    module docstring.
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


def generate_via_hook(
    command: str,
    *,
    timeout: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
) -> str | None:
    """Run an adopter-configured external generator hook and return its
    handle, or ``None`` on any failure (timeout, non-zero exit, malformed
    output) -- **fail-closed**: the caller must fall back to
    :func:`generate_handle`, never propagate an invalid value.

    ``command`` is executed via the shell (so an adopter can compose a
    pipeline); its stdout is stripped and validated with
    :func:`is_valid_handle` before being accepted. This proves the output is
    branch-safe; it does NOT prove the output is non-identifying -- see the
    module docstring's trust-scope note.
    """
    if not command:
        return None
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    candidate = result.stdout.strip()
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
