"""Static maintenance-role contracts, not live rollout qualification."""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "maintain-agent-infrastructure"


def _read(relative: str) -> str:
    return (SKILL / relative).read_text(encoding="utf-8")


def test_role_is_discoverable_and_worktree_owned() -> None:
    text = _read("SKILL.md")
    frontmatter = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert frontmatter is not None
    assert "name: maintain-agent-infrastructure\n" in frontmatter[1]
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
    ):
        assert term in text


def test_agent_recovery_belongs_to_monitor_not_maintainer() -> None:
    maintainer = " ".join(
        (_read("SKILL.md") + _read("assets/CHARTER.md")
         + _read("references/rollout-and-recovery.md")).split()
    )
    assert "recovers original agent sessions" not in maintainer
    assert "--resume <original-id>" not in maintainer
    assert "never starts, stops, resumes or recovers other agents" in maintainer
    assert "declare host-ready" in maintainer.casefold() or "declares host-ready" in maintainer
    monitor = " ".join(
        (PLUGIN / "skills" / "monitor-agent-fleet" / "references"
         / "repair-escalation.md").read_text(encoding="utf-8").split()
    )
    for term in (
        "## Recovery rules",
        "--resume <original-id>",
        "original user/provider identity",
        "create a duplicate session",
        "A resume receipt is not a running CLI",
        "continuation turn",
        "error or a timeout is not proof",
        "Never create a second live head or a new worktree",
        "recover one agent at a time",
    ):
        assert term in monitor


def test_maintenance_window_protocol_is_shared_with_monitor() -> None:
    maintainer = " ".join(_read("references/rollout-and-recovery.md").split())
    monitor = " ".join(
        (PLUGIN / "skills" / "monitor-agent-fleet" / "SKILL.md")
        .read_text(encoding="utf-8").split()
    )
    for text in (maintainer, monitor):
        for term in ("Quiesce", "Restore", "stopped for maintenance"):
            assert term in text
        for term in ("off the host", "durable window state", "hold the window",
                     "authorized owner", "never host-clear"):
            assert term in text
        folded = text.casefold()
        for term in ("legacy finite-worker recovery", "host-targeted admission",
                     "in-flight launches", "until host-ready and restore",
                     "requeued during the window", "supervisor's new worker is live"):
            assert term in folded
    register = " ".join(
        (PLUGIN / "skills" / "monitor-agent-fleet" / "references" / "fleet-register.md")
        .read_text(encoding="utf-8").split()
    )
    for term in ("## Hold automatic replacement", "agent-dispatch actions hold",
                 "inhibit worker-admission", "Revoking a standing grant does not stop it",
                 "If any path has no hold that its owner can set, hold the window"):
        assert term in register
    for term in ("Request", "Maintain", "host-ready"):
        assert term in maintainer
    window = maintainer.split("## Maintenance window", 1)[1].split("## ", 1)[0]
    assert "approv" not in window.casefold()


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
    assert "maintain-agent-infrastructure" in topology
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
                resolved = (path.parent / target.split("#", 1)[0]).resolve()
                assert resolved.is_relative_to(PLUGIN), target
                assert resolved.is_file(), target
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "skills/maintain-agent-infrastructure/SKILL.md" in readme
    assert "assign an infrastructure maintainer" in readme.casefold()
    assert "fleet-maintenance" in (PLUGIN / "plugin.json").read_text(encoding="utf-8")


def test_infrastructure_and_progress_roles_stay_separate_after_rename() -> None:
    monitor = PLUGIN / "skills" / "monitor-agent-fleet" / "SKILL.md"
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert not (PLUGIN / "skills" / "maintain-agent-fleet" / "SKILL.md").exists()
    for text in (_read("SKILL.md"), monitor.read_text(encoding="utf-8"), readme):
        assert "Do not merge these roles" in text
    assert "not agent-progress\n  monitoring" in _read("SKILL.md")
    assert "host/tooling maintenance" in monitor.read_text(encoding="utf-8")
    assert "no second skill or invocation alias" in readme
    assert "preserve the worktree, original grants, holds and pending effects" in readme
    for path in (monitor, monitor.parent / "references" / "repair-escalation.md"):
        for target in re.findall(
            r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")
        ):
            if "maintain-agent-" in target:
                assert (path.parent / target).resolve().is_file(), target
