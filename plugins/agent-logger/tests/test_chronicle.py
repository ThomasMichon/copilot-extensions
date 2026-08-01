"""Tests for the background-chronicling orchestrator daemon and its two seams.

Covers the work-locking invariants the chronicler compatibility contract
requires: I2 continuation-segment reservation (input fencing), I4 settle gate +
already-journaled skip, and the seam contracts (router origin keying, per-sink
landing policy) plus the objective narration-style resolution.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from agent_logger.chronicle.digest import (
    DAILY_DIGEST_TEMPLATE,
    build_digest_manifest,
    group_by_day,
)
from agent_logger.chronicle.orchestrator import (
    Chronicler,
    ManifestWriter,
    WriteResult,
)
from agent_logger.chronicle.sink import (
    DirectCommitLanding,
    LogSink,
    OriginRepoRouter,
    RouteRule,
)
from agent_logger.chronicle.source import (
    DEFAULT_SETTLE_SECONDS,
    DiscoveredSession,
    ReservationState,
    ReservationStore,
    SegmentRef,
    SyncedSessionSource,
    chronicle_dedup_key,
)
from agent_logger.config import (
    OBJECTIVE_NARRATION_INSTRUCTION,
    resolve_narration_style,
)

# --------------------------------------------------------------------------
# SegmentRef identity + dedup_key alignment (I2 requirement)
# --------------------------------------------------------------------------


def test_segment_ref_key_and_parse_roundtrip() -> None:
    ref = SegmentRef("sess-abc", 3)
    assert ref.key == "sess-abc:3"
    assert SegmentRef.parse(ref.key) == ref


def test_dedup_key_derives_from_reservation_identity() -> None:
    """The mesh task dedup_key MUST key on the same identity as the reservation."""
    ref = SegmentRef("parent-1", 2)
    # dedup_key embeds exactly (parent_session_id, segment_index).
    assert chronicle_dedup_key(ref) == "chronicle:parent-1:2"
    assert chronicle_dedup_key(ref).endswith(ref.key)


# --------------------------------------------------------------------------
# I2: ReservationStore CAS semantics
# --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> ReservationStore:
    return ReservationStore(tmp_path / "chronicle.db")


def test_reserve_first_holder_wins(store: ReservationStore) -> None:
    ref = SegmentRef("s1", 0)
    assert store.reserve(ref, "worker-a") is True
    # Same holder re-reserving is idempotent.
    assert store.reserve(ref, "worker-a") is True
    # A different holder reserves nothing while it is held.
    assert store.reserve(ref, "worker-b") is False
    assert store.state_of(ref) is ReservationState.RESERVED


def test_mark_journaled_is_terminal_and_skips(store: ReservationStore) -> None:
    ref = SegmentRef("s1", 0)
    store.reserve(ref, "worker-a")
    store.mark_journaled(ref, log_path="logs/x.md")
    assert store.is_journaled(ref) is True
    # A journaled segment can never be re-reserved -> no double-log.
    assert store.reserve(ref, "worker-a") is False
    assert store.reserve(ref, "worker-b") is False


def test_release_downgrade_guard(store: ReservationStore) -> None:
    ref = SegmentRef("s1", 0)
    store.reserve(ref, "worker-a")
    # Wrong holder cannot release.
    assert store.release(ref, "worker-b") is False
    # Correct holder releases -> available again (stale-reclaim).
    assert store.release(ref, "worker-a") is True
    assert store.state_of(ref) is ReservationState.AVAILABLE
    # After journaling, release is refused (never resurrect a logged unit).
    store.reserve(ref, "worker-a")
    store.mark_journaled(ref)
    assert store.release(ref, "worker-a") is False
    assert store.is_journaled(ref) is True


def test_two_racing_workers_only_one_reserves(store: ReservationStore) -> None:
    ref = SegmentRef("s1", 0)
    reserved = [store.reserve(ref, h) for h in ("a", "b", "c")]
    assert reserved.count(True) == 1


# --------------------------------------------------------------------------
# Session-source: synced corpus scan + I4 settle gate + journaled skip
# --------------------------------------------------------------------------


def _write_session(
    corpus_root: Path,
    machine: str,
    session_id: str,
    *,
    repository: str = "owner/repo",
    created_at: str = "2026-07-28T10:00:00Z",
    age_seconds: float = DEFAULT_SETTLE_SECONDS + 60,
    source_repo: str | None = "__unset__",
) -> Path:
    sess = corpus_root / machine / "session-state" / session_id
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"ts": 1}\n', encoding="utf-8")
    (sess / "workspace.yaml").write_text(
        f"id: {session_id}\nrepository: {repository}\n"
        f"branch: main\ncreated_at: {created_at}\n",
        encoding="utf-8",
    )
    # source_repo="__unset__" -> no origin sidecar (pre-backfill session);
    # source_repo=None -> a recorded machine-only origin; a string -> that repo.
    if source_repo != "__unset__":
        import json

        (sess / "origin.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "machine": machine,
                    "source_repo": source_repo,
                    "basis": "machine-default" if source_repo is None else "cwd",
                }
            ),
            encoding="utf-8",
        )
    old = time.time() - age_seconds
    import os

    os.utime(sess, (old, old))
    return sess


def test_scan_applies_settle_gate(tmp_path: Path) -> None:
    corpus = tmp_path / "sessions"
    _write_session(corpus, "book2", "settled", age_seconds=DEFAULT_SETTLE_SECONDS + 30)
    _write_session(corpus, "book2", "fresh", age_seconds=5)
    src = SyncedSessionSource(corpus, ReservationStore(tmp_path / "c.db"))
    found = {s.session_id for s in src.scan()}
    assert found == {"settled"}


def test_scan_skips_journaled(tmp_path: Path) -> None:
    corpus = tmp_path / "sessions"
    _write_session(corpus, "book2", "one")
    reservations = ReservationStore(tmp_path / "c.db")
    src = SyncedSessionSource(corpus, reservations)
    assert {s.session_id for s in src.scan()} == {"one"}
    # Journal it -> next scan skips it (idempotent under catch-up replays).
    reservations.mark_journaled(SegmentRef("one", 0))
    assert src.scan() == []


def test_scan_reads_origin_repository(tmp_path: Path) -> None:
    corpus = tmp_path / "sessions"
    _write_session(corpus, "book2", "one", repository="org/aperture-labs")
    src = SyncedSessionSource(corpus, ReservationStore(tmp_path / "c.db"))
    sessions = src.scan()
    assert sessions[0].repository == "org/aperture-labs"
    assert sessions[0].day == "2026-07-28"
    assert sessions[0].ref == SegmentRef("one", 0)
    # No origin sidecar written -> no recorded origin.
    assert sessions[0].origin_recorded is False
    assert sessions[0].source_repo is None


def test_scan_reads_recorded_origin_sidecar(tmp_path: Path) -> None:
    """The source seam surfaces the recorded origin.json source_repo so the
    router can key off it (derive-the-origin-never-guess)."""
    corpus = tmp_path / "sessions"
    _write_session(
        corpus, "book2", "harnessed", repository="owner/fork",
        source_repo="aperture-labs",
    )
    _write_session(
        corpus, "book2", "machine-only", repository="owner/random",
        source_repo=None,
    )
    src = SyncedSessionSource(corpus, ReservationStore(tmp_path / "c.db"))
    by_id = {s.session_id: s for s in src.scan()}
    assert by_id["harnessed"].origin_recorded is True
    assert by_id["harnessed"].source_repo == "aperture-labs"
    # A recorded machine-only origin: sidecar present, source_repo null.
    assert by_id["machine-only"].origin_recorded is True
    assert by_id["machine-only"].source_repo is None


# --------------------------------------------------------------------------
# Log-sink: router origin keying + machine-default fallback
# --------------------------------------------------------------------------


def _session(
    session_id: str,
    repository: str | None,
    *,
    source_repo: str | None = None,
    origin_recorded: bool = False,
) -> DiscoveredSession:
    return DiscoveredSession(
        session_id=session_id,
        machine="book2",
        session_path=Path("/x") / session_id,
        repository=repository,
        source_repo=source_repo,
        origin_recorded=origin_recorded,
        created_at="2026-07-28T10:00:00Z",
    )


def test_router_routes_by_recorded_origin() -> None:
    """A recorded origin (origin.json source_repo) is authoritative, not the
    raw workspace repository -- derive-the-origin-never-guess."""
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", None)], default_sink="dotfiles"
    )
    # Recorded aperture-labs origin -> skipped, regardless of raw repository.
    assert (
        router.route(
            _session(
                "a", "owner/some-fork", source_repo="aperture-labs",
                origin_recorded=True,
            )
        )
        is None
    )
    # Recorded work origin -> machine default (dotfiles), even if the raw
    # repository happens to contain "aperture-labs" noise.
    assert (
        router.route(
            _session(
                "b", "aperture-labs-mirror", source_repo="acme-webapp",
                origin_recorded=True,
            )
        )
        == "dotfiles"
    )


def test_router_recorded_machine_only_takes_default() -> None:
    """A recorded machine-only origin (sidecar present, source_repo null) has no
    repo to match and authoritatively takes the machine default -- it does NOT
    fall back to the raw repository string."""
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", None)], default_sink="dotfiles"
    )
    # Raw repository names aperture-labs, but the RECORDED origin is machine-only
    # -> machine default, never the aperture-labs skip rule.
    assert (
        router.route(
            _session(
                "a", "org/aperture-labs", source_repo=None, origin_recorded=True,
            )
        )
        == "dotfiles"
    )


def test_router_no_sidecar_falls_back_to_raw_repository() -> None:
    """Pre-backfill transition: with NO recorded origin, the router falls back
    to matching the raw workspace repository (legacy behavior)."""
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", None)], default_sink="dotfiles"
    )
    assert router.route(_session("a", "org/aperture-labs")) is None
    assert router.route(_session("b", "owner/acme-webapp")) == "dotfiles"


def test_router_routes_by_origin_with_default_fallback() -> None:
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", "aperture-labs")], default_sink="dotfiles"
    )
    # aperture-labs origin -> its sink (never misfiled into dotfiles).
    assert router.route(_session("a", "git@host:org/aperture-labs.git")) == "aperture-labs"
    # any other origin -> machine-default sink.
    assert router.route(_session("b", "owner/webapp")) == "dotfiles"
    # no recorded repository -> machine-default sink.
    assert router.route(_session("c", None)) == "dotfiles"


def test_router_none_default_drops_unmatched() -> None:
    router = OriginRepoRouter([RouteRule("aperture-labs", "ap")], default_sink=None)
    assert router.route(_session("b", "owner/webapp")) is None


def test_router_skip_sentinel_beats_default() -> None:
    """A null-sink rule skips its origin without falling through to default."""
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", None)], default_sink="dotfiles"
    )
    # aperture-labs-origin is dropped (skipped), NOT misfiled into dotfiles.
    assert router.route(_session("a", "git@host:org/aperture-labs.git")) is None
    # every other dotfiles-machine origin still defaults to dotfiles.
    assert router.route(_session("b", "owner/webapp")) == "dotfiles"
    assert router.route(_session("c", "owner/copilot-extensions")) == "dotfiles"
    assert router.route(_session("d", None)) == "dotfiles"


def test_group_by_day_drops_skipped_origin() -> None:
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", None)], default_sink="dotfiles"
    )
    digests = group_by_day(
        [
            _session("a", "org/aperture-labs"),
            _session("b", "owner/dotfiles"),
        ],
        router,
    )
    # only the dotfiles-origin session survives to a digest.
    assert [(d.sink_id, len(d.sessions)) for d in digests] == [("dotfiles", 1)]


def test_group_by_day_buckets_by_sink_and_day() -> None:
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", "ap")], default_sink="dotfiles"
    )
    sessions = [
        _session("a", "org/aperture-labs"),
        _session("b", "owner/webapp"),
        _session("c", "owner/dotfiles"),
    ]
    digests = group_by_day(sessions, router)
    by_sink = {d.sink_id: len(d.sessions) for d in digests}
    assert by_sink == {"ap": 1, "dotfiles": 2}


# --------------------------------------------------------------------------
# narration_style: objective is first-class
# --------------------------------------------------------------------------


def test_resolve_objective_narration_style() -> None:
    assert resolve_narration_style("objective") == OBJECTIVE_NARRATION_INSTRUCTION
    assert resolve_narration_style("OBJECTIVE") == OBJECTIVE_NARRATION_INSTRUCTION
    # Free-form voice instructions pass through untouched.
    assert resolve_narration_style("Consult my-voice skill") == "Consult my-voice skill"
    assert resolve_narration_style(None) is None


def test_digest_manifest_expands_objective_and_uses_compact_template() -> None:
    sink = LogSink(sink_id="dotfiles", repo_path=Path("/repo"))
    digests = group_by_day(
        [_session("a", "owner/dotfiles")],
        OriginRepoRouter([], default_sink="dotfiles"),
    )
    manifest = build_digest_manifest(digests[0], sink)
    assert manifest["mode"] == "digest"
    assert manifest["digest_date"] == "2026-07-28"
    assert manifest["narration_style"] == OBJECTIVE_NARRATION_INSTRUCTION
    assert manifest["digest_template"] == DAILY_DIGEST_TEMPLATE
    assert manifest["sessions"][0]["segment_ref"] == "a:0"


# --------------------------------------------------------------------------
# Landing policy: direct-commit into a real git repo
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sink-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def test_direct_commit_landing(git_repo: Path) -> None:
    (git_repo / "logs").mkdir()
    (git_repo / "logs" / "day.md").write_text("# chronicle\n", encoding="utf-8")
    result = DirectCommitLanding().land(
        git_repo, ["logs/day.md"], message="chronicle: 2026-07-28"
    )
    assert result.ok and result.committed
    log = subprocess.run(
        ["git", "-C", str(git_repo), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "chronicle: 2026-07-28" in log


def test_direct_commit_landing_nothing_to_commit(git_repo: Path) -> None:
    result = DirectCommitLanding().land(git_repo, [], message="empty")
    assert result.ok and not result.committed


# --------------------------------------------------------------------------
# Chronicler orchestration: idempotency + no double-log
# --------------------------------------------------------------------------


class _FakeWriter:
    """A writer that records manifests and reports the produced log path."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def write(self, manifest: dict, sink: LogSink) -> WriteResult:
        self.calls.append(manifest)
        return WriteResult(ok=True, log_paths=[f"{sink.output_root}/{manifest['digest_date']}.md"])


class _NullLanding(DirectCommitLanding):
    def land(self, repo_path, log_paths, *, message):  # type: ignore[override]
        from agent_logger.chronicle.sink import LandingResult

        return LandingResult(ok=True, detail="noop", committed=True)


def _chronicler(tmp_path: Path, writer, *, holder="cloud1") -> Chronicler:
    corpus = tmp_path / "sessions"
    _write_session(corpus, "book2", "s1", repository="owner/dotfiles")
    _write_session(corpus, "book2", "s2", repository="org/aperture-labs")
    reservations = ReservationStore(tmp_path / "c.db")
    source = SyncedSessionSource(corpus, reservations)
    router = OriginRepoRouter(
        [RouteRule("aperture-labs", "ap")], default_sink="dotfiles"
    )
    sinks = {
        "dotfiles": LogSink("dotfiles", tmp_path, landing_policy=_NullLanding()),
        "ap": LogSink("ap", tmp_path, landing_policy=_NullLanding()),
    }
    return Chronicler(source, router, sinks, writer=writer, holder=holder)


def test_run_once_produces_per_sink_daily_digests(tmp_path: Path) -> None:
    writer = _FakeWriter()
    chronicler = _chronicler(tmp_path, writer)
    result = chronicler.run_once()
    assert result.scanned == 2
    assert result.digests == 2
    assert result.landed == 2
    sinks = {c["sink"] for c in writer.calls}
    assert sinks == {"dotfiles", "ap"}


def test_run_once_is_idempotent(tmp_path: Path) -> None:
    writer = _FakeWriter()
    chronicler = _chronicler(tmp_path, writer)
    first = chronicler.run_once()
    assert first.landed == 2
    # Second pass: everything already journaled -> nothing rescanned/landed.
    second = chronicler.run_once()
    assert second.scanned == 0
    assert second.landed == 0
    # Writer was not called again.
    assert len(writer.calls) == 2


def test_writer_failure_releases_reservation(tmp_path: Path) -> None:
    class _FailWriter:
        def write(self, manifest, sink):
            return WriteResult(ok=False, detail="boom")

    corpus = tmp_path / "sessions"
    _write_session(corpus, "book2", "s1", repository="owner/dotfiles")
    reservations = ReservationStore(tmp_path / "c.db")
    source = SyncedSessionSource(corpus, reservations)
    router = OriginRepoRouter([], default_sink="dotfiles")
    sinks = {"dotfiles": LogSink("dotfiles", tmp_path, landing_policy=_NullLanding())}
    chronicler = Chronicler(source, router, sinks, writer=_FailWriter(), holder="cloud1")

    result = chronicler.run_once()
    assert result.outcomes[0].status == "failed"
    # The segment was released (not journaled) so a retry can re-claim it.
    assert reservations.state_of(SegmentRef("s1", 0)) is ReservationState.AVAILABLE


def test_manifest_writer_persists_manifest(tmp_path: Path) -> None:
    writer = ManifestWriter(tmp_path / "manifests")
    sink = LogSink("dotfiles", tmp_path)
    digests = group_by_day(
        [_session("a", "owner/dotfiles")],
        OriginRepoRouter([], default_sink="dotfiles"),
    )
    manifest = build_digest_manifest(digests[0], sink)
    result = writer.write(manifest, sink)
    assert result.ok
    written = list((tmp_path / "manifests").glob("*.json"))
    assert len(written) == 1
    assert "dotfiles-2026-07-28.json" == written[0].name


def test_factory_skip_repositories_builds_skip_rule(tmp_path: Path) -> None:
    """The dotfiles v1 config: skip aperture-labs-origin, default -> dotfiles."""
    import copy

    from agent_logger.chronicle.factory import build_chronicler
    from agent_logger.config import DEFAULTS, Config

    data = copy.deepcopy(DEFAULTS)
    data["chronicle"].update(
        {
            "enabled": True,
            "corpus_root": str(tmp_path / "sessions"),
            "db_path": str(tmp_path / "c.db"),
            "manifests_dir": str(tmp_path / "m"),
            "default_sink": "dotfiles",
            "skip_repositories": ["aperture-labs"],
            "sinks": {"dotfiles": {"repo_path": str(tmp_path / "repo")}},
        }
    )
    cfg = Config(data, home=tmp_path)
    chronicler = build_chronicler(cfg)
    router = chronicler.router
    # aperture-labs-origin skipped; everything else -> dotfiles.
    assert router.route(_session("a", "org/aperture-labs")) is None
    assert router.route(_session("b", "owner/webapp")) == "dotfiles"
    assert "dotfiles" in chronicler.sinks
