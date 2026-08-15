"""The **log-sink** seam of the background chronicler: ``{router, profile,
landing-policy}``.

A sink is *where* and *how* a day's chronicle lands. The daemon core holds no
opinion about any of the three axes -- each is injected so a consumer can target
a different repo, layer a voice, or land through a different mechanism without
touching scan/digest:

* **Router** -- maps a discovered session to a sink id by its **recorded origin
  repo** (``workspace.yaml`` ``repository``), with a machine-default fallback.
  This is the generalization of permanent-record's origin resolver: an
  test-chamber-origin session routes to the test-chamber sink; everything else
  on a dotfiles machine routes to the dotfiles sink.
* **Profile** -- the output voice/shape. ``narration_style`` defaults to
  ``"objective"`` (matter-of-fact chronicle); a consumer may layer a character
  voice on *its* sink via the same manifest voice seam. A sink also carries the
  compact daily-digest template.
* **LandingPolicy** (I3) -- how a produced log is committed. The daemon must
  **not** hardcode landing; each sink supplies a strategy. Reference strategies:
  :class:`DirectCommitLanding` (dotfiles: one scoped daily commit) and
  :class:`SquashPRLanding` (one daily squash PR, auto-merged). A consumer that
  needs a governed single-flight merge-queue (test-chamber permanent-record)
  supplies its own :class:`LandingPolicy` -- the seam is the extension point, so
  the merge-queue never has to live in the daemon core.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from agent_logger.chronicle.source import DiscoveredSession

#: Default matter-of-fact narration style for the background chronicle.
OBJECTIVE = "objective"


@dataclass
class Profile:
    """The output voice/shape for a sink.

    ``narration_style`` is the primary voice seam (see the manifest contract).
    The background chronicle default is ``"objective"`` -- neutral, factual, no
    persona. A consumer sink may set it to voice-skill instructions to layer a
    character voice on *its* target; the daemon copies these fields into the
    manifest unchanged.
    """

    narration_style: str | None = OBJECTIVE
    exemplars: str | list[str] | None = None
    closing_remark: str | None = None
    #: The compact daily-digest body template (distinct from the per-session
    #: Summary/Key-Changes/Commits/Open-Items shape). None uses the built-in
    #: :data:`agent_logger.chronicle.digest.DAILY_DIGEST_TEMPLATE`.
    digest_template: str | None = None


@dataclass
class RouteRule:
    """One origin-repo -> sink mapping.

    *repository* is matched case-insensitively as a substring of the session's
    recorded ``workspace.yaml`` ``repository`` (so ``test-chamber`` matches
    ``git@host:org/test-chamber.git``). The first matching rule wins.

    ``sink_id=None`` is the **skip sentinel**: a session matching such a rule is
    dropped (routed nowhere), *without* falling through to the router's
    ``default_sink``. This expresses "some other harness owns this origin's
    chronicle" -- e.g. test-chamber-origin sessions that are already chronicled
    multi-machine system-side by permanent-record, so cloud1 must neither file them into
    dotfiles nor double-file them.
    """

    repository: str
    sink_id: str | None


class Router(ABC):
    """Maps a discovered session to a sink id (or None to skip)."""

    @abstractmethod
    def route(self, session: DiscoveredSession) -> str | None: ...


class OriginRepoRouter(Router):
    """Route by **recorded origin repo**, with a machine-default fallback.

    Keys off the session's recorded origin (``origin.json`` ``source_repo``,
    surfaced as :attr:`DiscoveredSession.source_repo`) -- the durable,
    worktree-safe repo derived at sync time -- realizing the vision behavior
    ``derive-the-origin-never-guess``. Rules are tried in order; the first
    substring match decides the outcome -- a sink id, or **skip** (drop) when
    the matched rule's ``sink_id`` is None.

    Fallbacks, in order:

    * A session with a **recorded machine-only** origin (sidecar present,
      ``source_repo`` null -- derived, no harness matched) has no repo to match,
      so it authoritatively takes *default_sink* (the machine default).
    * A session with **no recorded origin** at all (no sidecar -- e.g. a
      pre-backfill session synced before origin marking) falls back to matching
      the raw ``workspace.yaml`` ``repository``, then *default_sink*. This keeps
      the router functioning during the transition; once Phase-4 backfill has
      stamped every session, the recorded origin is always authoritative.

    The one hard job on the test-chamber side: an test-chamber-origin session
    must match a rule (a **skip** sentinel in v1) and **never** fall through to
    the dotfiles default. Because a matched skip returns None *before* the
    fallback, ``default_sink="dotfiles"`` can safely catch every other
    dotfiles-machine origin while test-chamber-origin is explicitly skipped.
    """

    def __init__(self, rules: list[RouteRule], default_sink: str | None) -> None:
        self.rules = rules
        self.default_sink = default_sink

    def route(self, session: DiscoveredSession) -> str | None:
        # The routing key is the recorded origin (derive-the-origin-never-guess).
        # A recorded machine-only origin (source_repo is None) yields no key and
        # authoritatively takes the machine default. Only a session with NO
        # recorded origin at all falls back to the raw repository string.
        if session.origin_recorded:
            key = (session.source_repo or "").lower()
        else:
            key = (session.repository or "").lower()
        if key:
            for rule in self.rules:
                if rule.repository.lower() in key:
                    # A matched skip sentinel (sink_id is None) drops the
                    # session here -- it does NOT fall through to default_sink.
                    return rule.sink_id
        return self.default_sink


@dataclass
class LandingResult:
    ok: bool
    detail: str = ""
    committed: bool = False


class LandingPolicy(ABC):
    """How a produced daily log lands in a sink's repo (I3, per-sink).

    Implementations receive the sink repo path and the paths of the log files
    produced for one chronicle unit (relative to the repo). Landing is
    single-flight per sink by the daemon's own sequencing; a consumer needing a
    governed single-flight merge-queue supplies its own policy here.
    """

    name: str = "abstract"

    @abstractmethod
    def land(
        self, repo_path: Path, log_paths: list[str], *, message: str
    ) -> LandingResult: ...


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class DirectCommitLanding(LandingPolicy):
    """Scoped daily direct-commit to the chronicle subtree (dotfiles sink).

    Stages only the produced log paths and commits them directly to the current
    branch -- never a per-session PR. This is dotfiles' agreed landing model:
    one scoped daily commit to the ``logs/`` chronicle subtree. ``push`` is
    off by default so a caller can batch/push separately.
    """

    name = "direct-commit"

    def __init__(self, *, push: bool = False, remote: str = "origin") -> None:
        self.push = push
        self.remote = remote

    def land(
        self, repo_path: Path, log_paths: list[str], *, message: str
    ) -> LandingResult:
        if not log_paths:
            return LandingResult(ok=True, detail="nothing to land", committed=False)
        add = _git(repo_path, "add", "--", *log_paths)
        if add.returncode != 0:
            return LandingResult(ok=False, detail=f"git add failed: {add.stderr.strip()}")
        status = _git(repo_path, "status", "--porcelain", "--", *log_paths)
        if not status.stdout.strip():
            return LandingResult(ok=True, detail="no changes to commit", committed=False)
        commit = _git(repo_path, "commit", "-m", message, "--", *log_paths)
        if commit.returncode != 0:
            return LandingResult(
                ok=False, detail=f"git commit failed: {commit.stderr.strip()}"
            )
        if self.push:
            push = _git(repo_path, "push", self.remote, "HEAD")
            if push.returncode != 0:
                return LandingResult(
                    ok=False,
                    detail=f"git push failed: {push.stderr.strip()}",
                    committed=True,
                )
        return LandingResult(ok=True, detail="committed", committed=True)


class SquashPRLanding(LandingPolicy):
    """One daily squash PR of the chronicle subtree (auto-merged by the caller).

    Commits the produced logs on a per-day branch and pushes it; opening/merging
    the PR is delegated to *open_pr* (an injected callable) so this policy stays
    free of any specific PR host. When *open_pr* is None it stops after pushing
    the branch and reports the branch name in the detail.
    """

    name = "squash-pr"

    def __init__(
        self,
        *,
        branch_prefix: str = "chronicle/",
        remote: str = "origin",
        open_pr=None,
    ) -> None:
        self.branch_prefix = branch_prefix
        self.remote = remote
        self.open_pr = open_pr

    def land(
        self, repo_path: Path, log_paths: list[str], *, message: str
    ) -> LandingResult:
        if not log_paths:
            return LandingResult(ok=True, detail="nothing to land", committed=False)
        slug = message.lower().replace(" ", "-")[:48].strip("-") or "chronicle"
        branch = f"{self.branch_prefix}{slug}"
        for args in (
            ("checkout", "-B", branch),
            ("add", "--", *log_paths),
            ("commit", "-m", message, "--", *log_paths),
            ("push", "--force-with-lease", self.remote, f"HEAD:{branch}"),
        ):
            res = _git(repo_path, *args)
            if res.returncode != 0:
                return LandingResult(
                    ok=False, detail=f"git {args[0]} failed: {res.stderr.strip()}"
                )
        if self.open_pr is not None:
            detail = self.open_pr(repo_path, branch, message)
            return LandingResult(ok=True, detail=str(detail), committed=True)
        return LandingResult(ok=True, detail=f"pushed {branch}", committed=True)


@dataclass
class LogSink:
    """A resolved chronicle target: a repo, its output tree, voice, and landing.

    ``sink_id`` is the key a :class:`Router` returns. ``repo_path`` is the sink
    repo root; ``output_root`` is the logs subtree (relative to ``repo_path``);
    ``profile`` styles the output; ``landing_policy`` commits it.
    """

    sink_id: str
    repo_path: Path
    output_root: str = "logs"
    profile: Profile = field(default_factory=Profile)
    landing_policy: LandingPolicy = field(default_factory=DirectCommitLanding)
    log_path_template: str = "{year}/{month}/{day} chronicle.md"
