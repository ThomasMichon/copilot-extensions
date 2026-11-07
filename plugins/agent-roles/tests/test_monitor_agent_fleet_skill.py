"""Static role contracts; these checks do not qualify live steering."""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "monitor-agent-fleet"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_monitor_is_discoverable_without_activating_supervision() -> None:
    text = _read(SKILL / "SKILL.md")
    frontmatter = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert frontmatter is not None
    assert f"name: {SKILL.name}\n" in frontmatter.group(1)
    description = frontmatter.group(1).split("description: >-", 1)[1]
    assert 0 < len(description) <= 1024
    assert len(text[frontmatter.end():].splitlines()) < 500
    assert "task master" in description
    assert "Default to one **read-only** sweep" in text
    assert "**messages sent: none**" in text
    assert "does not authorize messages now" in text
    assert "not because Dispatch, Bridge or any one transport is" in text
    assert "Writing or enabling this skill is not installing a" in text
    assert "Static documentation tests do not qualify runtime" in _read(
        SKILL / "assets" / "CHARTER.md"
    )


def test_steering_preserves_current_owner_and_unknown_effects() -> None:
    text = " ".join(_read(SKILL / "references" / "steering.md").split())
    for requirement in (
        "explicit steering scope",
        "later hold, narrowing or revocation",
        "current head session",
        "changed head requires reconciliation",
        "one outstanding steer per key",
        "If persistence fails, do not send",
        "never by blind resend",
        "send another only for materially new evidence",
    ):
        assert requirement in text, requirement
    assert text.index("## Before sending") < text.index("## Sending")
    assert text.index("**Persist first.**") < text.index("## Sending")
    assert "Steer a reviewer's verdict" in text


def test_offline_scenarios_have_distinct_evidence_and_explicit_modes() -> None:
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert "never execute" in scenarios["purpose"]
    cases = scenarios["cases"]
    ids = [case["id"] for case in cases]
    assert cases and len(ids) == len(set(ids))
    for case in cases:
        assert case["mode"] in {
            "read-only", "steering-authorized", "dormant-correction",
        }
        assert case["facts"].strip() and case["expected"].strip()
    assert {case["mode"] for case in cases} == {
        "read-only", "steering-authorized", "dormant-correction",
    }


def test_fabric_reader_preserves_cross_project_roster_scope() -> None:
    text = _read(SKILL / "references" / "fabric-readers.md")
    assert "list-sessions --all-projects --worktree <id> --json" in text
    assert "select its owning project with `-p <project>`" in text


def test_fabric_reader_suppresses_worktrees_provisioning() -> None:
    text = " ".join(_read(SKILL / "references" / "fabric-readers.md").split())
    assert "AGENT_WORKTREES_NO_SELFPROVISION=1" in text
    assert "for every Worktrees invocation, including on the remote host" in text
    assert "refusal or missing runtime as unavailable evidence" in text


def test_watchdog_checks_never_block_and_classify_waits() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    checks = " ".join(_read(SKILL / "references" / "watchdog-checks.md").split())
    patterns = " ".join(_read(
        SKILL / "references" / "misdirection-patterns.md"
    ).split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    for requirement in (
        "**The watchdog never asks a blocking question**",
        "**Classify every wait as required, productive or waste**",
        "A merge is not done when the root needs live behavior",
        "or when a delivered steer stalls past its revisit time",
        "defer loop rules to `review-pull-requests`",
    ):
        assert requirement in skill, requirement
    for requirement in (
        "**The watchdog never asks a blocking question.**",
        "never raises another agent's budget",
        "not its live context window",
        "names no failed parent boundary",
        "Unreadable state is `unknown`, never healthy",
        "ask whether the work is excessive before asking anyone for a larger limit",
        "Never steer an agent to deploy without separate deployment authority",
        "Do not count all waiting as waste",
        "Escalate once, with the evidence, a recommended decision and its default",
        "Never repeat the report unchanged",
        "neither counts toward escalation nor justifies a resend",
        "../../review-pull-requests/references/throughput-and-loops.md",
    ):
        assert requirement in checks, requirement
    for pattern in (
        "Unbounded question",
        "Self-added prerequisite",
        "Merged treated as done",
        "Handoff rewrites intent",
        "Disproportionate validation",
        "Superseded agent untold",
        "whether the work itself is excessive",
    ):
        assert pattern in patterns, pattern
    for requirement in (
        "Active and parked cadences",
        "Question revisit time and required default",
        "The monitor asked no blocking question",
    ):
        assert requirement in charter, requirement


def test_dormant_correction_edits_records_only_under_its_own_grant() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    text = " ".join(_read(
        SKILL / "references" / "dormant-correction.md"
    ).split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    steering = " ".join(_read(SKILL / "references" / "steering.md").split())
    patterns = " ".join(_read(
        SKILL / "references" / "misdirection-patterns.md"
    ).split())
    assert "(references/dormant-correction.md)" in skill
    assert "steering never edits a worker's records" in skill
    for requirement in (
        "**records changed: none**",
        "A read-only or steering grant does not authorize record edits; "
        "a correction grant does not authorize messages",
        "they never establish a verdict",
        "Unknown liveness blocks mutation for that target only",
        "A finalized workspace is not a Done objective",
        "it is that owner's subordinate: exclude it",
        "an operator-set marker stays",
        "Report filing and other bookkeeping separately",
        "Do not ask per target",
        "A newly active target is excluded and reported, never interrupted "
        "or messaged",
        "A write without readback is unknown, not done",
        "Never infer a correction from metadata",
        "**recommend retirement**",
        "Only the handoff owner's verified record-only supersede",
        "makes every existing locator resolve to the replacement",
        "An ordinary handoff save is not that operation",
    ):
        assert requirement in text, requirement
    assert text.index("## Clear versus ambiguous") < text.index(
        "## Write safely"
    )
    assert text.index("Recheck liveness through the native owner") < (
        text.index("Write through the owner, then read back")
    )
    for requirement in (
        "| dormant correction",
        "Dormant correction scope (only if explicitly granted; else \"none\")",
        "live-idle, newly active and unknown targets were left unchanged",
    ):
        assert requirement in charter, requirement
    assert "[dormant correction](dormant-correction.md)" in steering
    assert "Over-narrow self-proposed grant" in patterns
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    dormant = {
        case["id"] for case in scenarios["cases"]
        if case["mode"] == "dormant-correction"
    }
    assert {
        "dormant-stale-blocker",
        "dormant-ambiguous-root",
        "dormant-newly-active",
        "dormant-finalized-open-effort",
        "dormant-unavailable-evidence",
        "dormant-stale-baton",
    } <= dormant


def test_recurring_watch_re_resolves_roster_each_cycle() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    readers = " ".join(_read(
        SKILL / "references" / "fabric-readers.md"
    ).split())
    for requirement in (
        "**re-resolves its roster at the start of every cycle**",
        "Retire one removed from the manifest, keeping its history",
        "Keep a declared but unready machine as a coverage gap",
        "Readiness failures never authorize provisioning, connecting or "
        "repairing a venue",
        "Each cycle begins with the roster refresh above.",
    ):
        assert requirement in skill, requirement
    assert skill.index("re-resolves its roster") < skill.index(
        "## 3. Analyze history"
    )
    assert "Roster source: <fixed list | re-resolved each cycle" in charter
    assert "machines added, retired or kept as gaps" in charter
    assert "do not reuse an earlier cycle's readiness" in readers
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert "recurring-roster-refresh" in {
        case["id"] for case in scenarios["cases"]
    }
