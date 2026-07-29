"""Daily digest grouping and the compact daily-digest manifest.

The background chronicle is a *daily* artifact, not a per-session one: a day's
sessions for a given sink collapse into **one** compact log so later agents get
topic/issue "hits" without wading through per-session detail. This module groups
settled sessions by ``(sink, day)`` and builds the manifest the writer agent
runs against.

The daily-digest manifest is deliberately a **distinct shape** from the
per-session ``Summary / Key-Changes / Commits / Open-Items`` log
(``mode: "single" | "batch"``): it uses ``mode: "digest"`` and a compact
day-oriented template so the two output kinds never get conflated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_logger.chronicle.sink import LogSink
from agent_logger.chronicle.source import DiscoveredSession
from agent_logger.config import resolve_narration_style

#: Built-in compact daily-digest body template. One entry per session, factual
#: and terse -- the objective baseline. Tokens: {date} {machine} {sink}
#: {session_count} {sessions}. Distinct from the per-session log template.
DAILY_DIGEST_TEMPLATE = """# Chronicle -- {date}

**Sink:** {sink}
**Sessions:** {session_count}

## Sessions

{sessions}
"""


@dataclass
class DailyDigest:
    """All sessions for one ``(sink, day)`` -- the unit the writer chronicles."""

    sink_id: str
    day: str
    sessions: list[DiscoveredSession] = field(default_factory=list)

    @property
    def refs(self):
        return [s.ref for s in self.sessions]


def group_by_day(
    sessions: list[DiscoveredSession], router
) -> list[DailyDigest]:
    """Route each session to a sink and bucket by ``(sink_id, day)``.

    Sessions the router declines (``route`` returns None) are dropped. Result is
    sorted by ``(sink_id, day)`` for deterministic ordering.
    """
    buckets: dict[tuple[str, str], DailyDigest] = {}
    for session in sessions:
        sink_id = router.route(session)
        if sink_id is None:
            continue
        key = (sink_id, session.day)
        digest = buckets.get(key)
        if digest is None:
            digest = DailyDigest(sink_id=sink_id, day=session.day)
            buckets[key] = digest
        digest.sessions.append(session)
    return [buckets[k] for k in sorted(buckets)]


def build_digest_manifest(
    digest: DailyDigest,
    sink: LogSink,
    *,
    timezone: str | None = None,
) -> dict:
    """Build the ``mode: "digest"`` manifest for one daily digest.

    The manifest carries the same voice seam fields as the per-session contract
    (``narration_style`` / ``exemplars`` / ``closing_remark``) sourced from the
    sink's :class:`~agent_logger.chronicle.sink.Profile`, plus the compact
    ``digest_template`` and the ``digest_date`` the writer renders one daily log
    from.
    """
    return {
        "mode": "digest",
        "return": "json",
        "digest_date": digest.day,
        "sink": sink.sink_id,
        "sessions": [
            {
                "session_id": s.session_id,
                "machine": s.machine,
                "session_path": str(s.session_path),
                "repository": s.repository,
                "branch": s.branch,
                "summary": s.summary,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "segment_ref": s.ref.key,
            }
            for s in digest.sessions
        ],
        "output_root": sink.output_root,
        "log_path_template": sink.log_path_template,
        "timezone": timezone,
        "narration_style": resolve_narration_style(sink.profile.narration_style),
        "exemplars": sink.profile.exemplars,
        "closing_remark": sink.profile.closing_remark,
        "digest_template": sink.profile.digest_template or DAILY_DIGEST_TEMPLATE,
    }
