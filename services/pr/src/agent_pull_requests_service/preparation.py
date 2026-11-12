"""Explicit native origin resolution before pure retained comment preparation."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_pull_requests.native_read import NativeReadHandler, command_prefix
from agent_pull_requests.owner_read import OwnerReadUnavailable
from agent_pull_requests.watch_replay_store import json_object

if TYPE_CHECKING:
    from attribution_profile import ProfileError, load_profile
    from comment_preparation import _source, prepare
else:
    from ._attribution.attribution_profile import ProfileError, load_profile
    from ._attribution.comment_preparation import _source, prepare

_command_prefix: Callable[..., tuple[str, ...]] = command_prefix
_run_native: Callable[[list[str], Callable[[], float], int], Any] = NativeReadHandler._run


@dataclass(frozen=True)
class PreparationConfig:
    command: tuple[str, ...]
    source_root: Path
    source_config: Path
    profile_home: Path
    visibility: str
    allow_anonymous: bool

    def __post_init__(self) -> None:
        _command_prefix(self.command, require_available=False)
        if self.visibility not in ("public", "internal") or type(self.allow_anonymous) is not bool:
            raise ValueError("explicit preparation privacy required")

    def select(
        self, scope: Mapping[str, object], checkpoint: Callable[[], None], seconds: int,
    ) -> NativePreparation | None:
        """Discover only this invocation; rollback never substitutes another origin."""
        selected = NativePreparation(self, dict(scope), seconds)
        remaining = selected.remaining(checkpoint)
        try:
            result = _run_native([
                *self.command, "attribution-prepare", "--comment-origin", "--capabilities",
            ], remaining, 65536)
            remaining()
            value = json_object(result.stdout)
            if (
                result.returncode != 0
                or value.get("schema") != "agent-worktrees.comment-origin-capabilities"
                or type(value.get("version")) is not int or value["version"] != 1
                or value.get("origin_protocol") != "agent-worktrees.comment-origin/v1"
                or not isinstance(value.get("capabilities"), list)
                or "explicit_source_root" not in value["capabilities"]
                or value.get("effects") != "none"
            ):
                raise ValueError("unsupported executing origin contract")
        except (OwnerReadUnavailable, ValueError, UnicodeError, RecursionError):
            logging.getLogger(__name__).warning(
                "selected native preparation unavailable; new allocation disabled",
            )
            return None
        return selected


@dataclass(frozen=True)
class NativePreparation:
    config: PreparationConfig
    scope: Mapping[str, object]
    seconds: int

    def remaining(
        self, checkpoint: Callable[[], None], deadline: float | None = None,
    ) -> Callable[[], float]:
        deadline = min(time.monotonic() + self.seconds, deadline) if deadline is not None else (
            time.monotonic() + self.seconds
        )

        def remaining() -> float:
            checkpoint()
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise OwnerReadUnavailable("native preparation budget exhausted")
            return seconds

        return remaining

    def __call__(
        self, generation: dict[str, Any], *, checkpoint: Callable[[], None],
        deadline: float | None = None,
    ) -> Callable[..., Any]:
        remaining = self.remaining(checkpoint, deadline)
        remaining()
        selected_scope = copy.deepcopy(generation.get("scope"))
        if not isinstance(selected_scope, dict) or {
            key: value for key, value in selected_scope.items() if key != "number"
        } != dict(self.scope):
            raise ProfileError("producer destination binding differs")
        config = self.config
        root = config.source_root.resolve(strict=True)
        source_config = config.source_config.resolve(strict=True)
        home = config.profile_home.resolve(strict=True)
        if not root.is_dir() or not home.is_dir() or not source_config.is_file():
            raise ProfileError("producer references unavailable")
        result = _run_native([
            *config.command, "attribution-prepare", "--comment-origin",
            "--source-root", str(root), "--config", str(source_config),
            "--visibility", config.visibility,
        ], remaining, 65536)
        remaining()
        if result.returncode != 0:
            raise ProfileError("producer origin unavailable")
        source = _source(json_object(result.stdout), config.visibility, config.allow_anonymous)
        profile, errors = load_profile(home, root, env={})
        if errors:
            raise ProfileError("producer signature profile unavailable")
        remaining()

        def compose(
            kind: str, body: str, *, scope: dict[str, object], comment_id: object,
            write_id: object, previous: object = None,
        ) -> Any:
            if scope != selected_scope or kind not in ("comment", "reply"):
                raise ProfileError("producer action binding differs")
            return prepare(
                body, profile, {
                    "domain": "forge", "visibility": config.visibility,
                    "activity": "reply" if kind == "reply" else "comment",
                }, source, comment_id=comment_id, write_id=write_id, previous=previous,
                allow_anonymous=config.allow_anonymous,
            )

        return compose
