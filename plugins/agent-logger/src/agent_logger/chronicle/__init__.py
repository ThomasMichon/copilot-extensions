"""agent-logger background chronicling -- the orchestrator daemon.

The chronicler is a scheduled, fleet-wide, single-elected pass that turns the
**synced** Copilot session corpus into objective, matter-of-fact daily logs
landed in a target harness repo. It is driven by ``agent-dispatch``'s schedule
management + single-producer job-lease (shape 1); this package supplies the two
pluggable seams the daemon runs between:

- :mod:`agent_logger.chronicle.source` -- the **session-source** seam: discover
  settled sessions from the synced corpus, skip already-journaled ones, and
  fence the exact continuation segments a chronicle unit will log (the I2
  reservation) so a racing pass can never double-log.
- :mod:`agent_logger.chronicle.sink` -- the **log-sink** seam
  ``{router, profile, landing-policy}``: route a session to its target repo by
  recorded origin, style the output (objective default; consumers may layer a
  voice), and land it through a per-sink landing policy (direct-commit /
  squash-pr / a consumer-supplied merge-queue).

:mod:`agent_logger.chronicle.orchestrator` wires the two seams into the
``scan -> digest -> reserve -> manifest -> writer -> land`` loop.

The daemon core is deliberately transport-, repo-, and voice-neutral: every
policy that differs between consumers (origin routing rules, output voice,
landing mechanism) is injected through a seam so a consumer (e.g. aperture-labs
permanent-record) can adopt the daemon without re-implementing scan/digest.
"""

from __future__ import annotations

from agent_logger.chronicle.digest import (
    DAILY_DIGEST_TEMPLATE,
    DailyDigest,
    build_digest_manifest,
    group_by_day,
)
from agent_logger.chronicle.orchestrator import (
    Chronicler,
    ChronicleResult,
    DigestOutcome,
    ManifestWriter,
    Writer,
    WriteResult,
)
from agent_logger.chronicle.sink import (
    DirectCommitLanding,
    LandingPolicy,
    LandingResult,
    LogSink,
    OriginRepoRouter,
    Profile,
    Router,
    RouteRule,
    SquashPRLanding,
)
from agent_logger.chronicle.source import (
    DiscoveredSession,
    ReservationState,
    ReservationStore,
    SegmentRef,
    SessionSource,
    SyncedSessionSource,
    chronicle_dedup_key,
)

__all__ = [
    "DAILY_DIGEST_TEMPLATE",
    "ChronicleResult",
    "Chronicler",
    "DailyDigest",
    "DigestOutcome",
    "DirectCommitLanding",
    "DiscoveredSession",
    "LandingPolicy",
    "LandingResult",
    "LogSink",
    "ManifestWriter",
    "OriginRepoRouter",
    "Profile",
    "ReservationState",
    "ReservationStore",
    "RouteRule",
    "Router",
    "SegmentRef",
    "SessionSource",
    "SquashPRLanding",
    "SyncedSessionSource",
    "WriteResult",
    "Writer",
    "build_digest_manifest",
    "chronicle_dedup_key",
    "group_by_day",
]
