"""The chronicler daemon: ``scan -> digest -> reserve -> manifest -> writer ->
land``.

:class:`Chronicler` wires the two seams together into one idempotent pass. It is
the body of the recurring, cloud1-pinned job that ``agent-dispatch``'s schedule
management + single-producer job-lease (shape 1) drives; this class holds no
scheduling or leasing logic of its own -- the mesh owns *when* and *where-once*,
the chronicler owns *what* and *how-once-per-segment*.

One :meth:`run_once` pass:

1. **scan** the source (settle gate + already-journaled skip already applied by
   the source) -> settled, unlogged sessions;
2. **route + group** them by ``(sink, day)`` via the log-sink router;
3. for each daily digest, **reserve** every segment it will log (I2). A digest
   with no freshly-reserved segments is skipped -- a racing pass already owns
   them, so this pass logs nothing for it (no double-log);
4. build the compact **digest manifest** and hand it to the **writer** seam
   (which produces the day's log file(s));
5. **land** the produced logs via the sink's landing policy (I3);
6. **mark journaled** each reserved segment on success, or **release** it on
   failure so a later pass can retry (stale-reclaim).

The writer is an injected seam so the daemon is testable without spawning a
Copilot agent; the default :class:`ManifestWriter` writes the manifest to disk
for a spawn harness (the ``orchestrator runner`` caller of the manifest
contract) to run the ``session-log-writer`` agent against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent_logger.chronicle.digest import DailyDigest, build_digest_manifest, group_by_day
from agent_logger.chronicle.sink import LogSink, Router
from agent_logger.chronicle.source import SessionSource


@dataclass
class WriteResult:
    ok: bool
    #: Log file paths produced (relative to the sink's repo), for the landing
    #: policy to stage.
    log_paths: list[str] = field(default_factory=list)
    detail: str = ""


class Writer(Protocol):
    """Turns a digest manifest into one or more log files under the sink repo."""

    def write(self, manifest: dict, sink: LogSink) -> WriteResult: ...


class ManifestWriter:
    """Default writer: persist the manifest for a spawn harness to run.

    The chronicler daemon *produces* the manifest; a spawn harness (the
    ``orchestrator runner`` of the manifest contract) runs the
    ``session-log-writer`` agent against it to render the log. This default
    writes the manifest JSON under ``<manifests_dir>/<sink>-<day>.json`` and
    reports the intended log path so the pass stays observable end-to-end even
    before a spawn harness is wired.
    """

    def __init__(self, manifests_dir: Path) -> None:
        self.manifests_dir = manifests_dir
        self.manifests_dir.mkdir(parents=True, exist_ok=True)

    def write(self, manifest: dict, sink: LogSink) -> WriteResult:
        day = manifest["digest_date"]
        path = self.manifests_dir / f"{sink.sink_id}-{day}.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return WriteResult(ok=True, log_paths=[], detail=f"manifest -> {path}")


@dataclass
class DigestOutcome:
    sink_id: str
    day: str
    reserved: int
    status: str  # "landed" | "written" | "skipped" | "failed"
    detail: str = ""


@dataclass
class ChronicleResult:
    scanned: int = 0
    digests: int = 0
    outcomes: list[DigestOutcome] = field(default_factory=list)

    @property
    def landed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "landed")

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "digests": self.digests,
            "landed": self.landed,
            "outcomes": [
                {
                    "sink": o.sink_id,
                    "day": o.day,
                    "reserved": o.reserved,
                    "status": o.status,
                    "detail": o.detail,
                }
                for o in self.outcomes
            ],
        }


class Chronicler:
    """Runs one idempotent chronicle pass across all configured sinks."""

    def __init__(
        self,
        source: SessionSource,
        router: Router,
        sinks: dict[str, LogSink],
        *,
        writer: Writer,
        holder: str,
        timezone: str | None = None,
    ) -> None:
        self.source = source
        self.router = router
        self.sinks = sinks
        self.writer = writer
        self.holder = holder
        self.timezone = timezone

    def run_once(self, *, now: datetime | None = None) -> ChronicleResult:
        sessions = self.source.scan(now=now)
        digests = group_by_day(sessions, self.router)
        result = ChronicleResult(scanned=len(sessions), digests=len(digests))
        for digest in digests:
            result.outcomes.append(self._process(digest))
        return result

    def _process(self, digest: DailyDigest) -> DigestOutcome:
        sink = self.sinks.get(digest.sink_id)
        if sink is None:
            return DigestOutcome(
                digest.sink_id, digest.day, 0, "skipped", "no sink configured"
            )

        # I2: reserve exactly the segments this unit will log. A segment already
        # reserved by a racing pass (or journaled) reserves nothing here.
        reserved = [s for s in digest.sessions if self.source.reserve(s.ref, self.holder)]
        if not reserved:
            return DigestOutcome(
                digest.sink_id, digest.day, 0, "skipped", "no segments reserved"
            )
        reserved_digest = DailyDigest(digest.sink_id, digest.day, reserved)

        try:
            manifest = build_digest_manifest(
                reserved_digest, sink, timezone=self.timezone
            )
            written = self.writer.write(manifest, sink)
            if not written.ok:
                self._release(reserved_digest)
                return DigestOutcome(
                    digest.sink_id, digest.day, len(reserved), "failed", written.detail
                )

            if written.log_paths:
                landing = sink.landing_policy.land(
                    sink.repo_path,
                    written.log_paths,
                    message=f"chronicle: {digest.day}",
                )
                if not landing.ok:
                    self._release(reserved_digest)
                    return DigestOutcome(
                        digest.sink_id, digest.day, len(reserved), "failed", landing.detail
                    )
                status, detail = "landed", landing.detail
            else:
                # Writer deferred rendering to a spawn harness; segments stay
                # reserved (not yet journaled) until the log is actually written.
                return DigestOutcome(
                    digest.sink_id, digest.day, len(reserved), "written", written.detail
                )
        except Exception as exc:
            self._release(reserved_digest)
            return DigestOutcome(
                digest.sink_id, digest.day, len(reserved), "failed", repr(exc)
            )

        for session in reserved:
            self.source.mark_journaled(session.ref, log_path=None)
        return DigestOutcome(digest.sink_id, digest.day, len(reserved), status, detail)

    def _release(self, digest: DailyDigest) -> None:
        for session in digest.sessions:
            self.source.release(session.ref, self.holder)
