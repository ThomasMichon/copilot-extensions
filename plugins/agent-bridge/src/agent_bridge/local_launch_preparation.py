"""Retain a keyed local launch plan across the pre-effect registration boundary."""

from __future__ import annotations

from dataclasses import dataclass

from repository_policy import AgentHostingError

from .transport import SpawnTarget


async def resolve_local_launch(
    target: SpawnTarget, *, session_id: str = "",
) -> tuple[list[str], str | None, dict[str, str]]:
    """Use the transport owner's current resolver across both preparation paths."""
    from .transport import resolve_local_launch as resolve

    return await resolve(target, session_id=session_id)


class LocalLaunchPreparationError(RuntimeError):
    """Local resolution failed before session registration or native launch."""


@dataclass(frozen=True)
class PreparedLocalLaunch:
    session_id: str
    target_json: str
    args: tuple[str, ...]
    cwd: str | None
    environment: dict[str, str]

    def resolve(
        self, target: SpawnTarget, session_id: str,
    ) -> tuple[list[str], str | None, dict[str, str]]:
        if self.session_id != session_id or self.target_json != target.to_json():
            raise ValueError("prepared local launch identity changed")
        from .hosting_policy import require_path_hosting

        require_path_hosting(self.cwd)
        return list(self.args), self.cwd, dict(self.environment)


async def prepare(target: SpawnTarget, session_id: str) -> PreparedLocalLaunch:
    try:
        args, cwd, environment = await resolve_local_launch(target, session_id=session_id)
    except AgentHostingError:
        raise
    except Exception as exc:
        raise LocalLaunchPreparationError(f"Local launch preparation failed: {exc}") from exc
    return PreparedLocalLaunch(
        session_id, target.to_json(), tuple(args), cwd, dict(environment),
    )
