"""Opt-in, process-local read-only policy for production Picker review."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import threading

MESSAGE = "Read-only candidate: live actions, launches and maintenance are disabled."


class ReadOnlyError(RuntimeError):
    pass


class _Reads:
    def __init__(self):
        self.cancelled = threading.Event()
        self._condition = threading.Condition()
        self._active = 0

    @contextmanager
    def operation(self):
        with self._condition:
            if self.cancelled.is_set():
                raise ReadOnlyError("Read-only candidate: review session is closed.")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self.cancelled.set()
            if not self._condition.wait_for(lambda: self._active == 0, timeout=10):
                raise ReadOnlyError("Read-only candidate: admitted reads did not drain.")


@dataclass(frozen=True)
class Review:
    state_dir: Path
    owner_root: Path
    candidate: str
    engine: tuple[str, ...]
    reads: _Reads = field(default_factory=_Reads)


_review: Review | None = None


def active() -> bool:
    return _review is not None


def current() -> Review:
    if _review is None:
        raise RuntimeError("no Picker review is active")
    return _review


def require_writable() -> None:
    if active():
        raise ReadOnlyError(MESSAGE)


def engine_command(args: list[str], ordinary):
    if not active():
        return ordinary()
    from .engine_client import EngineError

    if args not in (
        ["picker-bootstrap", "--json", "--project-config"],
        ["picker-bootstrap", "--json"],
        ["picker-paths", "--json"],
    ):
        raise EngineError(MESSAGE)
    return list(current().engine)


def run_read(argv, **kwargs):
    """Keep admitted read children and their descendants caller-owned."""
    from . import engine_process

    if kwargs.pop("capture_output", False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reads = current().reads
    with reads.operation():
        return engine_process.run(argv, cancelled=reads.cancelled, **kwargs)


def refuse(screen) -> bool:
    if not active():
        return False
    screen.debug = MESSAGE
    screen.refresh()
    return True


@contextmanager
def session(candidate: str):
    """A review process is single-use; late callbacks retain the refusal fence."""
    global _review
    if active():
        raise RuntimeError("Picker review requires a fresh process")
    from . import agent_plugin_runtime

    invocation = agent_plugin_runtime.resolve_installed_plugin_invocation("agent-worktrees")
    if invocation is None:
        raise ReadOnlyError("Read-only candidate: installed Worktrees owner is unavailable; no provisioning attempted.")
    command, owner_root = invocation
    prefix = "worktree-manager-review-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        _review = Review(Path(directory), owner_root, candidate, tuple(command))
        try:
            yield _review
        finally:
            _review.reads.close()
    # Do not clear _review: surviving daemon threads must never regain writes.


def bootstrap(project: str) -> dict:
    from .production_picker import engine_group_b

    payload = engine_group_b.picker_bootstrap(project)
    if (
        not isinstance(payload, dict) or payload.get("project") != project
    ):
        raise ReadOnlyError("Read-only candidate: invalid owner bootstrap; no recovery attempted.")
    return {
        **payload, "default_live": False,
        "project_config": {
            **(payload.get("project_config") or {}),
            "install_dir": str(current().owner_root),
        },
    }


def worktree_rows(project: str, *, worktree_id: str | None = None) -> list[dict]:
    """Read the existing owner's feed; never run list/reconcile/recovery CLIs."""
    from .resident_transport import frames
    from .worktree_feed_contract import CurrentFeed, FeedUnavailable

    feed = CurrentFeed()
    state = current()
    with state.reads.operation():
        stream = frames(
            state.owner_root, project,
            alive=lambda: not state.reads.cancelled.is_set(), startup_timeout=2.0,
        )
        try:
            for frame in stream:
                rows = feed.receive(frame)
                if rows is not None:
                    if worktree_id is None:
                        return rows
                    matches = [row for row in rows if row["id"].endswith(worktree_id)]
                    return matches if len(matches) == 1 else []
        finally:
            stream.close()
    raise FeedUnavailable("Read-only candidate: owner feed closed before publication")


def list_command(pivot, argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """Only qualified, non-provisioning contributed reads are admitted."""
    from . import agent_plugin_runtime

    owner = getattr(pivot, "source_owner", None)
    args = argv[1:]
    if (
        not isinstance(owner, str) or owner.split("@", 1)[0] != "agent-dispatch"
        or Path(argv[0]).stem.casefold() != "agent-dispatch"
        or args[:2] != ["workers", "catalog"]
        or getattr(pivot, "stream", False)
    ):
        raise ReadOnlyError("Read-only candidate: this contributed read has not been qualified.")
    remaining = args[2:]
    seen = set()
    while remaining:
        if (
            len(remaining) < 2 or remaining[0] not in {"--repo", "--workspace"}
            or remaining[0] in seen or not remaining[1] or remaining[1].startswith("-")
        ):
            raise ReadOnlyError("Read-only candidate: unsupported catalog read arguments.")
        seen.add(remaining[0])
        remaining = remaining[2:]
    invocation = agent_plugin_runtime.resolve_installed_plugin_invocation("agent-dispatch")
    if invocation is None:
        raise ReadOnlyError("Read-only candidate: installed Dispatch owner is unavailable.")
    command, owner_root = invocation
    return [*command, *args], {"AGENT_DISPATCH_INSTALL_DIR": str(owner_root)}
