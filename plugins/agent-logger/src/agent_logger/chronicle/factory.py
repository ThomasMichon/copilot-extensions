"""Build a :class:`~agent_logger.chronicle.orchestrator.Chronicler` from config.

Turns the machine-local ``chronicle`` config block into a wired chronicler: the
synced-corpus source, an origin-repo router, the named sinks with their profiles
and landing policies, and the default manifest writer. Only the elected host
enables ``chronicle`` in its config, so this factory is what that one machine's
scheduled tick calls.
"""

from __future__ import annotations

from pathlib import Path

from agent_logger.chronicle.orchestrator import Chronicler, ManifestWriter
from agent_logger.chronicle.sink import (
    DirectCommitLanding,
    LandingPolicy,
    LogSink,
    OriginRepoRouter,
    Profile,
    RouteRule,
    SquashPRLanding,
)
from agent_logger.chronicle.source import (
    ReservationStore,
    SyncedSessionSource,
)
from agent_logger.config import Config
from agent_logger.segmenter.platform import detect_machine


def _landing_policy(spec: dict) -> LandingPolicy:
    kind = (spec.get("landing") or "direct-commit").strip().lower()
    if kind == "squash-pr":
        return SquashPRLanding(remote=spec.get("remote", "origin"))
    return DirectCommitLanding(
        push=bool(spec.get("push", False)), remote=spec.get("remote", "origin")
    )


def _build_sink(sink_id: str, spec: dict) -> LogSink:
    repo_path = spec.get("repo_path")
    if not repo_path:
        raise ValueError(f"chronicle sink {sink_id!r} is missing repo_path")
    profile = Profile(
        narration_style=spec.get("narration_style", "objective"),
        exemplars=spec.get("exemplars"),
        closing_remark=spec.get("closing_remark"),
        digest_template=spec.get("digest_template"),
    )
    return LogSink(
        sink_id=sink_id,
        repo_path=Path(repo_path).expanduser(),
        output_root=spec.get("output_root", "logs"),
        profile=profile,
        landing_policy=_landing_policy(spec),
        log_path_template=spec.get(
            "log_path_template", "{year}/{month}/{day} chronicle.md"
        ),
    )


def build_chronicler(cfg: Config) -> Chronicler:
    """Wire a chronicler from *cfg*'s ``chronicle`` block.

    Raises ``ValueError`` if a configured sink is malformed. The caller decides
    whether to run based on :attr:`Config.chronicle_enabled`.
    """
    block = cfg.chronicle
    holder = block.get("holder") or cfg.machine_name or detect_machine()

    reservations = ReservationStore(cfg.chronicle_db_path)
    source = SyncedSessionSource(
        cfg.chronicle_corpus_root,
        reservations,
        settle_seconds=cfg.chronicle_settle_seconds,
    )

    rules = [
        RouteRule(repository=r["repository"], sink_id=r["sink"])
        for r in block.get("routes", [])
        if r.get("repository") and r.get("sink")
    ]
    router = OriginRepoRouter(rules, block.get("default_sink"))

    sinks = {
        sink_id: _build_sink(sink_id, spec or {})
        for sink_id, spec in (block.get("sinks", {}) or {}).items()
    }

    writer = ManifestWriter(cfg.chronicle_manifests_dir)
    return Chronicler(
        source,
        router,
        sinks,
        writer=writer,
        holder=holder,
        timezone=cfg.log_timezone,
    )
