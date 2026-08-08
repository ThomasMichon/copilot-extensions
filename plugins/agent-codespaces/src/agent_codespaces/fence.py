"""Cross-harness in-CodeSpace lockfile fence (git-ref-resource-leases Phase 4).

The repo-ref L2 store (``coordination.py`` / ``agent-worktrees lease``) is
**same-harness-scoped by construction**: a *different* harness writes leases to a
*different* store repo and cannot collide there. The one seam it cannot cover is
two **different** harnesses contending for one shared CodeSpace. This module
fences that **on the resource itself** -- a marker dropped *inside* the CodeSpace
(``~/.agent-lease``) that names the writing harness (identity = the lease store
origin / control-plane repo), the holder ClaimRef, and a timestamp + TTL.

On connect: read the marker; if one from a **foreign** harness is present and
still **fresh**, the connect is refused (a genuine cross-harness collision the
L2 store cannot see); a **stale** marker, a **same-harness** marker, or an
absent/unreadable one is fine to overwrite with our own.

**Degrade-safe by construction.** This module is pure decision logic + shell
command builders; the caller runs the read/write over SSH and treats any
failure (no harness identity, unreadable marker, exec error) as *proceed* --
the fence only *adds* a cross-harness signal, it never becomes a new hard
dependency. The only blocking outcome is a **successfully read, fresh, foreign**
marker.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass

#: Marker path inside the CodeSpace (the home-dir lockfile).
FENCE_PATH = "~/.agent-lease"

#: Marker schema version -- an unrecognized version parses as *unreadable*
#: (proceed), never mis-honored.
FENCE_VERSION = 1

#: Default marker TTL (seconds). A crashed/departed harness's marker goes stale
#: on this timer so the CodeSpace is reclaimable without manual cleanup. Matches
#: the L2 default so the two fences expire on a comparable horizon.
DEFAULT_FENCE_TTL = 3600

#: Clock-skew allowance (seconds) when judging a foreign marker fresh, so a
#: small cross-machine clock difference never spuriously *keeps* a stale marker
#: fresh nor drops a still-valid one.
FENCE_SKEW = 30


@dataclass(frozen=True)
class FenceMarker:
    """The ``~/.agent-lease`` payload: who holds this CodeSpace, per its harness.

    ``harness`` is the writing harness's identity (its lease store origin URL);
    ``holder`` is the qualified ClaimRef (``machine/project/worktree_id
    [#session]``); ``written_at`` + ``ttl`` bound its freshness.
    """

    harness: str
    holder: str
    written_at: float
    ttl: int = DEFAULT_FENCE_TTL
    version: int = FENCE_VERSION

    @classmethod
    def parse(cls, text: str | None) -> FenceMarker | None:
        """Parse a marker JSON blob; return None when absent/garbage/unknown.

        A None return is the degrade-safe "no honorable marker" signal -- the
        caller then simply proceeds (and overwrites). Never raises.
        """
        if not text or not text.strip():
            return None
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("version") != FENCE_VERSION:
            return None
        harness = str(data.get("harness", "")).strip()
        holder = str(data.get("holder", "")).strip()
        if not harness:
            return None
        try:
            written_at = float(data.get("written_at", 0.0))
            ttl = int(data.get("ttl", DEFAULT_FENCE_TTL))
        except (ValueError, TypeError):
            return None
        return cls(
            harness=harness,
            holder=holder,
            written_at=written_at,
            ttl=ttl,
            version=FENCE_VERSION,
        )

    def is_fresh(self, now: float | None = None, *, skew: int = FENCE_SKEW) -> bool:
        """Whether the marker is still within ``written_at + ttl`` (+ skew)."""
        if self.ttl <= 0:
            return False
        clock = time.time() if now is None else now
        return clock <= self.written_at + self.ttl + skew

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "harness": self.harness,
            "holder": self.holder,
            "written_at": self.written_at,
            "ttl": self.ttl,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass(frozen=True)
class FenceDecision:
    """The pure verdict of :func:`evaluate` over a read marker.

    ``action`` is ``"proceed"`` (overwrite ours -- absent / stale / same-harness
    / unreadable) or ``"refuse"`` (a fresh foreign marker -- a real cross-harness
    collision). ``foreign_harness`` / ``foreign_holder`` describe the blocker on
    a refuse; ``reason`` is a short human tag either way.
    """

    action: str
    reason: str
    foreign_harness: str = ""
    foreign_holder: str = ""

    @property
    def refuse(self) -> bool:
        return self.action == "refuse"

    @property
    def proceed(self) -> bool:
        return self.action == "proceed"


def evaluate(
    local_harness: str,
    marker: FenceMarker | None,
    *,
    now: float | None = None,
    skew: int = FENCE_SKEW,
) -> FenceDecision:
    """Decide whether to proceed (and overwrite) or refuse, given a read marker.

    Pure and total. Refuses **only** a fresh marker from a *different* harness;
    everything else -- no marker, an expired marker, or our own harness's marker
    -- proceeds. An empty ``local_harness`` (identity unresolved) always
    proceeds: with no identity we cannot tell foreign from own, so the fence
    degrades off rather than blocking blindly.
    """
    if not marker:
        return FenceDecision("proceed", "no-marker")
    if not local_harness.strip():
        return FenceDecision("proceed", "no-identity")
    if marker.harness == local_harness.strip():
        return FenceDecision("proceed", "same-harness")
    if not marker.is_fresh(now, skew=skew):
        return FenceDecision(
            "proceed", "stale-foreign",
            foreign_harness=marker.harness, foreign_holder=marker.holder,
        )
    return FenceDecision(
        "refuse", "fresh-foreign",
        foreign_harness=marker.harness, foreign_holder=marker.holder,
    )


def _remote_path_expr(path: str) -> str:
    """Return a double-quoted shell expression for ``path`` with ~ -> $HOME.

    A leading ``~/`` is rewritten to ``$HOME/`` and the whole thing wrapped in
    **double** quotes so the variable expands (single-quoting would make the
    tilde/``$HOME`` literal). The fence path is an internal constant, so this
    stays deliberately narrow rather than a general shell-quoter.
    """
    expanded = path
    if path == "~":
        expanded = "$HOME"
    elif path.startswith("~/"):
        expanded = "$HOME/" + path[2:]
    return '"' + expanded + '"'


def read_marker_command(path: str = FENCE_PATH) -> str:
    """Shell command that prints the marker's contents (empty when absent).

    ``cat`` of a missing file is swallowed so the read never fails the exec on an
    absent marker (the common case).
    """
    return f"cat {_remote_path_expr(path)} 2>/dev/null || true"


def write_marker_command(marker: FenceMarker, path: str = FENCE_PATH) -> str:
    """Shell command that atomically writes ``marker`` to ``path``.

    Writes to a temp sibling then ``mv`` into place so a concurrent reader never
    sees a half-written marker. Best-effort at the caller: a nonzero exit is
    swallowed there.
    """
    payload = shlex.quote(marker.to_json())
    target = _remote_path_expr(path)
    tmp = _remote_path_expr(path + ".tmp.$$")
    return f"printf '%s' {payload} > {tmp} && mv {tmp} {target}"
