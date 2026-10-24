"""Repository-owned negative eligibility; never an adoption or launch grant."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

POLICY_VERSION = 1
_MARKETPLACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_CONFIG = Path(".copilot-extensions") / "agent-worktrees"
_BASES = (
    _CONFIG / "config.yaml",
    Path(".agent-worktrees") / "config.yaml",
    Path(".agent-worktrees.yaml"),
)


class _PolicyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        keys = [key.value for key, _ in node.value if key.tag == "tag:yaml.org,2002:str"]
        if keys.count("expose_agent") > 1:
            raise yaml.constructor.ConstructorError(
                None, None, "duplicate expose_agent policy", node.start_mark,
            )
        return super().construct_mapping(node, deep=deep)


class AgentHostingError(ValueError):
    """A hosting request cannot establish usable repository policy."""

    code = "repository-agent-exposure-invalid"


class RepositoryPolicyError(AgentHostingError):
    """A required source cannot be read or has malformed policy."""


class AgentHostingDenied(AgentHostingError):
    """The execution repository explicitly refuses managed authoring agents."""

    code = "repository-agent-exposure-denied"


def validate_hosting_result(payload: Any) -> None:
    """Require the complete versioned check answer, not merely a policy object."""
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload["version"] != POLICY_VERSION
        or payload.get("allowed") is not True
        or "error" in payload or "code" in payload
        or not isinstance(payload.get("repository"), str)
        or not payload["repository"].strip()
        or "expose_agent" not in payload
        or (payload["expose_agent"] is not None and payload["expose_agent"] is not True)
        or not isinstance(payload.get("sources"), list)
        or any(not isinstance(path, str) or not path.strip() for path in payload["sources"])
        or not isinstance(payload.get("denied_by"), list)
        or payload["denied_by"]
    ):
        raise AgentHostingError(
            "Repository hosting receiver did not return a usable version-1 admission; "
            "upgrade the receiver or use an eligible external harness for source-only work"
        )


@dataclass(frozen=True)
class RepositoryAgentPolicy:
    """Fresh effective negative policy and its read-only provenance."""

    repository: Path
    expose_agent: bool | None
    sources: tuple[Path, ...] = ()
    denied_by: tuple[Path, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the versioned, process-boundary representation."""
        return {
            "version": POLICY_VERSION,
            "repository": str(self.repository),
            "expose_agent": self.expose_agent,
            "sources": [str(path) for path in self.sources],
            "denied_by": [str(path) for path in self.denied_by],
        }


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RepositoryPolicyError(f"Cannot inspect repository policy {path}: {exc}") from exc
    return True


def repository_root(path: str | os.PathLike[str]) -> Path:
    """Resolve the nearest checkout, retaining non-Git standalone directories."""
    try:
        start = Path(path).resolve(strict=True)
        if not start.is_dir():
            raise RepositoryPolicyError(f"Execution workspace is not a directory: {start}")
        for candidate in (start, *start.parents):
            if _present(candidate / ".git"):
                return candidate
        return start
    except (OSError, RuntimeError) as exc:
        raise RepositoryPolicyError(f"Cannot resolve execution workspace {path}: {exc}") from exc


def _read(path: Path) -> bool | None:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_PolicyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RepositoryPolicyError(f"Cannot read repository policy {path}: {exc}") from exc
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RepositoryPolicyError(f"Repository policy {path} must contain a YAML mapping")
    if "expose_agent" not in raw:
        return None
    value = raw["expose_agent"]
    if type(value) is not bool:
        raise RepositoryPolicyError(
            f"Repository policy {path}: expose_agent must be a boolean, not {type(value).__name__}"
        )
    return value


def load_agent_policy(
    path: str | os.PathLike[str], *, marketplace_id: str = "",
) -> RepositoryAgentPolicy:
    """Read current checkout policy without machine overrides, cache or effects.

    Canonical configuration wins over legacy aliases. The selected marketplace
    may add a denial, but cannot erase the repository's distribution-neutral
    denial. An unrelated marketplace is never consulted.
    """
    if not isinstance(marketplace_id, str) or (
        marketplace_id and not _MARKETPLACE.fullmatch(marketplace_id)
    ):
        raise RepositoryPolicyError("Repository policy requires a valid marketplace identity")
    root = repository_root(path)
    candidates = [root / relative for relative in _BASES]
    base = next((candidate for candidate in candidates if _present(candidate)), None)
    selected = [base] if base is not None else []
    if marketplace_id:
        overlay = root / _CONFIG / "marketplaces" / marketplace_id / "config.yaml"
        if _present(overlay):
            selected.append(overlay)
    values = [(source, _read(source)) for source in selected]
    denied = tuple(source for source, value in values if value is False)
    effective = False if denied else next(
        (value for _, value in reversed(values) if value is not None), None,
    )
    return RepositoryAgentPolicy(root, effective, tuple(selected), denied)


def require_agent_hosting(
    path: str | os.PathLike[str], *, marketplace_id: str = "", operation: str = "launch",
) -> RepositoryAgentPolicy:
    """Refuse denied or indeterminate hosting; a return value grants nothing."""
    try:
        policy = load_agent_policy(path, marketplace_id=marketplace_id)
    except RepositoryPolicyError as exc:
        raise RepositoryPolicyError(
            f"Cannot admit managed agent {operation}: {exc}. Repair the repository policy "
            "or use an eligible external harness for source-only work."
        ) from exc
    if policy.expose_agent is False:
        sources = ", ".join(str(source) for source in policy.denied_by)
        raise AgentHostingDenied(
            f"Managed agent {operation} refused: expose_agent: false in {sources}. "
            "Use an eligible external harness for source-only work; allocation, inspection "
            "and contribution operations remain available."
        )
    return policy
