"""Read-only preview of project registrations conflicting with hosting policy."""

from __future__ import annotations

from pathlib import Path

import yaml
from repository_policy import AgentHostingError, RepositoryPolicyError, load_agent_policy

from . import installer, repos


GUIDANCE = (
    "Preserve existing registrations, sessions, tracking, history and claims. "
    "Use an eligible external harness for source-only work. Review each conflict "
    "or unresolved registration before explicitly authorizing a state-preserving "
    "migration or unregistration; this audit applies no changes."
)


def preview(*, marketplace_id: str) -> tuple[dict[str, object], int]:
    """Read only registered roots; never discover, adopt, repair or launch them."""
    try:
        projects = installer.read_projects_registry(strict=True)["projects"]
        registered = repos.read_registry(strict=True).repos
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RepositoryPolicyError(f"Cannot read registration audit inputs: {exc}") from exc

    rows: list[dict[str, object]] = []
    counts = {"conflicts": 0, "unresolved": 0, "invalid": 0}
    for name, entry in sorted(projects.items(), key=lambda item: str(item[0])):
        row: dict[str, object] = {"project": str(name), "action": "operator-review"}
        try:
            if not isinstance(name, str) or not name or not isinstance(entry, dict):
                raise RepositoryPolicyError("Project registration must have a name and mapping")
            local_exposure = entry.get("expose_agent")
            if local_exposure is not None and type(local_exposure) is not bool:
                raise RepositoryPolicyError("Registered expose_agent must be a Boolean")
            row["registration_expose_agent"] = local_exposure
            repository = registered.get(name)
            path = repository.local_path() if repository is not None else None
            if not path:
                row.update({
                    "state": "unresolved", "code": "repository-agent-exposure-audit-unresolved",
                    "error": "No registered root on this platform; no CWD fallback or discovery attempted",
                })
                counts["unresolved"] += 1
            else:
                row["registered_path"] = path
                if not Path(path).is_absolute():
                    raise RepositoryPolicyError(
                        "Registered root is not absolute; refusing CWD-relative policy resolution"
                    )
                policy = load_agent_policy(path, marketplace_id=marketplace_id)
                row["policy"] = policy.to_dict()
                if policy.expose_agent is False:
                    row.update({
                        "state": "conflict", "code": "repository-agent-exposure-denied",
                        "error": "Existing project registration conflicts with repository hosting denial",
                    })
                    counts["conflicts"] += 1
                else:
                    row.update({"state": "eligible", "action": "none"})
        except AgentHostingError as exc:
            row.update({"state": "invalid", "code": exc.code, "error": str(exc)})
            counts["invalid"] += 1
        rows.append(row)
    result: dict[str, object] = {
        "version": 1, "read_only": True, "automatic_changes": False,
        "marketplace_id": marketplace_id, "projects": rows,
        **counts, "guidance": GUIDANCE,
    }
    if any(counts.values()):
        result.update({
            "code": "repository-agent-exposure-audit-findings",
            "error": (
                f"Registration audit found {counts['conflicts']} conflict(s), "
                f"{counts['unresolved']} unresolved root(s), and {counts['invalid']} invalid entry/entries; "
                "preserve existing state and review explicitly"
            ),
        })
        return result, 3
    result["allowed"] = True
    return result, 0
