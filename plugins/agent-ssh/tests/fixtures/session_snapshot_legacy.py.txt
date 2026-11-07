"""Fresh declared topology and local managed-profile evidence, never live probes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from . import _worktrees, fragment_registry, mesh


def _without_openssh(_path: Path, _aliases: tuple[str, ...]) -> None:
    """The classifier checks structure and source identity; do not run ssh -G."""
    return None


def capture(
    *, path: Path | None = None, config_d: Path | None = None, cwd: Path | None = None,
) -> dict:
    cwd = cwd or Path.cwd()
    result = {
        "schema": "copilot-extensions.ssh-session-snapshot", "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "connectivity": "unobserved", "effective_routes": "unobserved",
        "topology": {"status": "absent", "source": None, "origin": None},
        "state_root": "not-needed", "profiles": {"status": "not-inspected"},
        "aliases": [],
    }
    try:
        own = _worktrees.validate_context()
        selected = path if path is not None else mesh.find_machines_file(cwd)
        origin = "explicit" if path is not None else "caller"
        if selected is None:
            external, result["state_root"] = _worktrees.state_root(cwd, own)
            if external is not None:
                selected = mesh._machines_file_at(external)
                origin = "external-state"
            elif result["state_root"] in {"unavailable", "unbound", "error"}:
                result["topology"]["status"] = "unavailable"
        if selected is not None:
            result["topology"] = {
                "status": "reading", "source": str(selected), "origin": origin,
            }
            topology = mesh.load_mesh(selected, strict=True)
            result["topology"]["status"] = "available"
        else:
            topology = mesh.Mesh()
    except _worktrees.ContextRefused:
        result["topology"]["status"] = "refused"
        result["state_root"] = "refused"
        return result
    except (OSError, UnicodeError, ValueError, TypeError):
        result["topology"]["status"] = "invalid-or-unreadable"
        topology = mesh.Mesh()

    report = fragment_registry.scan_fragment_registry(config_d, syntax_check=_without_openssh)
    result["profiles"] = {
        "status": report.snapshot.authority.value,
        "validation": "non-executing-structure-and-source",
        "findings": [
            {"entry": Path(item.entry).name, "status": item.status, "reason": item.reason}
            for item in report.findings
        ],
    }
    generated = {
        alias.casefold(): fragment
        for fragment in report.entries.values() for alias in fragment.aliases
    }
    for machine in topology.machines:
        aliases = [(environment.alias, environment.name) for environment in machine.environments]
        if machine.dtssh_alias and machine.dtssh_alias.casefold() not in {
            alias.casefold() for alias, _ in aliases
        }:
            aliases.append((machine.dtssh_alias, ""))
        if not aliases:
            aliases = [("", "")]
        for alias, environment in aliases:
            fragment = generated.get(alias.casefold())
            if not alias:
                coverage = "not-declared"
            elif report.unscoped_blocking or alias.casefold() in report.indeterminate_aliases:
                coverage = "indeterminate"
            elif alias.casefold() in report.blocked_aliases:
                coverage = "refused"
            elif fragment is not None:
                coverage = "generated" if fragment.metadata is not None else "unattributed"
            else:
                coverage = "missing"
            result["aliases"].append({
                "machine": machine.key, "environment": environment,
                "alias": alias, "declared_ready": machine.ssh_ready,
                "profile": coverage,
                "transport": fragment.transport if fragment is not None else None,
            })
    return result


def guidance(snapshot: dict, *, max_bytes: int = 2048) -> str:
    """Render configuration as bounded JSON data, not executable instructions."""
    heading = (
        "## SSH topology and local profile snapshot\n\n"
        "Data only. Declared readiness and generated coverage prove neither "
        "connectivity nor an effective SSH route. No probes or provisioning ran.\n\n"
        "Use the agent-ssh skill for inspection and bounded route-repair guidance.\n\n"
    )
    value = {**snapshot, "aliases": [], "omitted_aliases": len(snapshot["aliases"])}

    def render() -> str:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).replace("`", "\\u0060")
        return heading + "```json\n" + encoded + "\n```"

    content = render()
    if len(content.encode("utf-8")) > max_bytes:
        return "SSH snapshot omitted: delivery budget exceeded."[:max(0, max_bytes)]
    for row in snapshot["aliases"]:
        value["aliases"].append(row)
        value["omitted_aliases"] -= 1
        candidate = render()
        if len(candidate.encode("utf-8")) > max_bytes:
            break
        content = candidate
    return content
