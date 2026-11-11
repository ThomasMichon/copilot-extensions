"""Static maintenance-role contracts, not live rollout qualification."""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "maintain-agent-fleet"


def _read(relative: str) -> str:
    return (SKILL / relative).read_text(encoding="utf-8")


def test_role_is_discoverable_and_worktree_owned() -> None:
    text = _read("SKILL.md")
    frontmatter = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert frontmatter is not None
    assert "name: maintain-agent-fleet\n" in frontmatter[1]
    description = frontmatter[1].split("description: >-", 1)[1]
    assert len(description) <= 1024
    assert "maintain-and-update-fleet" in description
    assert len(text[frontmatter.end() :].splitlines()) < 500
    for term in (
        "canonical effort",
        "current owner's checkpoint",
        "not an always-on anchor chat",
        "Default to **read-only",
        "Neither role inherits the other's authority",
    ):
        assert term in text
    charter = _read("assets/CHARTER.md")
    for term in (
        "Original operator instruction",
        "Target selectors and limits",
        "Expiry/revocation/hold",
        "Ask-before list",
        "accepted charter revision",
        "Pending effect identities",
        "Stop owned schedules before handoff",
    ):
        assert term in charter


def test_rollout_keeps_authority_and_installation_boundaries() -> None:
    text = _read("SKILL.md") + _read("references/rollout-and-recovery.md")
    for term in (
        "one environment at a time",
        "<repo> update",
        "never hand-edit deployed files",
        "all applicable current selections",
        "outgoing central-service retirement is separate",
        "not a universal prerequisite",
        "Session hosts, keepers, session-owned processes",
        "automatic-only inhibition does not suppress",
        "Reconcile unknown",
        "zero exit",
        "**merged**, **released**, **installed** and **live-verified**",
    ):
        assert term.casefold() in text.casefold()
    for term in (
        "service restarts that terminate all agents",
        "elevation",
        "vault unlocks",
        "credentials/access/spend",
        "reboots",
    ):
        assert term in text


def test_unreachable_and_recovery_checks_preserve_original_owners() -> None:
    text = _read("SKILL.md") + _read("references/rollout-and-recovery.md")
    for term in (
        "agent-machines:performing-machine-maintenance",
        "exact\nmaintenance/machine predicates",
        "declarative owner",
        "not proof a machine is offline",
        "Launcher environment",
        "user-manager",
        "PATH",
        "Credentials and broker",
        "Container connectivity",
        "Remote mounts/name resolution",
        "--resume <original-id>",
        "original user/provider identity",
        "create a duplicate",
        "receipt is not consumption",
        "continuation turn",
    ):
        assert term in text


def test_continuity_and_topology_do_not_grant_activation() -> None:
    text = _read("SKILL.md")
    for term in (
        "No additional topology schema",
        "Materialize the complete",
        "minimum\nqualified coverage",
        "temporary assignment",
        "grants no allocation",
        "standing role stays Active",
        "Stop owned native schedules",
        "accepted custody",
        "exactly one next action",
        "no wake mechanism",
    ):
        assert term in text
    topology = (PLUGIN / "skills/managing-agent-topology/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "maintain-agent-fleet" in topology
    assert "separate maintenance authority" in topology


def test_role_links_resolve_and_public_entrypoints_include_it() -> None:
    for relative in (
        "SKILL.md",
        "assets/CHARTER.md",
        "references/rollout-and-recovery.md",
    ):
        path = SKILL / relative
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", _read(relative)):
            if "://" not in target:
                assert (path.parent / target.split("#", 1)[0]).is_file(), target
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "skills/maintain-agent-fleet/SKILL.md" in readme
    assert "assign a fleet maintainer role" in readme.casefold()
    assert "fleet-maintenance" in (PLUGIN / "plugin.json").read_text(encoding="utf-8")
