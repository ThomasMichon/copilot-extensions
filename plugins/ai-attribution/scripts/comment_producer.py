"""Optional in-process producer resolution; no host enrollment or forge effects."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from attribution_profile import ProfileError, load_profile
from comment_preparation import Prepared, prepare

if TYPE_CHECKING:
    from agent_worktrees.config import Config


@dataclass(frozen=True)
class CommentProducer:
    """Trusted owner inputs, never a remote request or a serialized callback.

    Invoke on the synchronous owner's worker before its replay transaction.
    The returned composer captures data only; the current owner still controls
    policy withdrawal, selection/admission and all native effects.
    """

    source_root: Path
    source_config: Config
    scope: Mapping[str, object]
    context: Mapping[str, object]
    allow_anonymous: bool = False

    def __call__(
        self, generation: dict[str, object], *, checkpoint: Callable[[], None],
    ) -> Callable[..., Prepared]:
        """Resolve actual source/profile once, returning bounded pure composition."""
        try:
            from agent_worktrees.attribution_prepare_cli import resolve_comment_origin
        except ImportError:
            raise ProfileError("producer source owner unavailable") from None

        checkpoint()
        scope, context = copy.deepcopy(dict(self.scope)), copy.deepcopy(dict(self.context))
        if (
            generation.get("scope") != scope or context.get("domain") != "forge"
            or context.get("visibility") not in ("public", "internal")
            or context.get("activity") != "comment" or type(self.allow_anonymous) is not bool
            or not self.source_root.is_absolute()
        ):
            raise ProfileError("producer destination binding differs")
        # Snapshot mutable trusted inputs before resolving either owner.
        config = copy.deepcopy(self.source_config)
        root = self.source_root.resolve(strict=True)
        source = resolve_comment_origin(
            root, config, visibility=str(context["visibility"]),
        )
        checkpoint()
        profile, errors = load_profile(Path.home(), root)
        if errors:
            raise ProfileError("producer signature profile unavailable")
        checkpoint()
        allow_anonymous = self.allow_anonymous

        selected_scope = scope

        def compose(
            kind: str, body: str, *, scope: dict[str, object], comment_id: object,
            write_id: object, previous: object = None,
        ) -> Prepared:
            if scope != selected_scope or kind not in ("comment", "reply", "edit_owned_comment"):
                raise ProfileError("producer action binding differs")
            selected_context = {**context, "activity": "reply" if kind == "reply" else "comment"}
            return prepare(
                body, profile, selected_context, source, comment_id=comment_id,
                write_id=write_id, previous=previous, allow_anonymous=allow_anonymous,
            )

        return compose
