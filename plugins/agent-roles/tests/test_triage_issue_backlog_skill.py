"""Static role-contract checks, not provider or model behavior qualification."""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "triage-issue-backlog"


def _read(relative: str) -> str:
    return (SKILL / relative).read_text(encoding="utf-8")


def test_discoverable_role_and_charter() -> None:
    text = _read("SKILL.md")
    frontmatter = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert frontmatter is not None
    assert "name: triage-issue-backlog\n" in frontmatter[1]
    description = frontmatter[1].split("description: >-", 1)[1]
    assert len(description) <= 1024
    assert "run the persistent issue triager role" in description
    assert "run the persistent issue-triager role" in description
    assert len(text[frontmatter.end():].splitlines()) < 500
    assert len(re.findall(r"^## [1-6]\. ", text, re.M)) == 6
    charter = _read("assets/CHARTER.md")
    for term in ("Original operator", "Exact query/filter", "Allowed effects",
                 "One canonical charter", "Current source-health",
                 "Pending worker/watch/schedule", "finite terminal census"):
        assert term in charter


def test_guidance_sources_do_not_create_authority_or_universal_precedence() -> None:
    text = _read("references/guidance-and-adapters.md")
    for term in ("Executing harness", "Bound knowledge workspace",
                 "Target backlog/repository", "Provider/domain owner",
                 ".agent-roles/triage-issue-backlog.md",
                 "not a permission token", "one universal precedence chain",
                 "No addendum widens the original grant",
                 "configured-but-unresolved required binding",
                 "not permission to broaden", "not activating"):
        assert term in text + _read("SKILL.md")


def test_filtered_provider_and_native_fallback_contracts() -> None:
    text = _read("references/guidance-and-adapters.md")
    for term in ("GitHub", "Azure DevOps", "query plus work-item hydration",
                 "revision/readback", "read-only binding",
                 "Do not guess an organization", "block that intake",
                 "not a complete work-item snapshot",
                 "does not stop", "local SQLite"):
        assert term.casefold() in (text + _read("references/evidence-and-continuity.md")).casefold()
    skill = _read("SKILL.md")
    assert "No SDK driver, new queue, installed watcher, daemon" in skill
    assert "default to **one** research worker" in skill
    assert "Use host model\npreferences" in skill


def test_evidence_and_publication_preserve_unknowns() -> None:
    text = _read("references/evidence-and-continuity.md")
    for term in ("A submitted result is neither accepted nor published",
                 "partial", "must not be posted again", "Unknown IDs",
                 "Successful identity verification is not complete source freshness",
                 "A missing ledger", "longer **diagnostic-only**"):
        assert term.casefold() in (text + _read("SKILL.md")).casefold()
    skill = _read("SKILL.md")
    assert "main agent is the sole publisher" in skill
    assert "Keep required evidence gaps **partial**" in skill
    assert "Triage is not a fix, closure" in skill


def test_no_unchanged_research_or_automatic_transfer() -> None:
    text = _read("references/evidence-and-continuity.md")
    for term in ("no worker\nor public edit", "do not\nincrease production deadlines",
                 "missing data/history", "native completion events",
                 "An old PID's absence never clears",
                 "stop owned native\nschedules", "Do not erase local history"):
        assert term.casefold() in (text + _read("SKILL.md")).casefold()
    assert "An empty pass is not completion" in _read("SKILL.md")
    assert "separate accepted handover" in text


def test_scenarios_remain_synthetic_and_cover_failure_boundaries() -> None:
    scenarios = json.loads(_read("assets/scenarios.json"))
    assert scenarios["example_only"] is True
    assert "not executable decisions or live qualification" in scenarios["description"]
    cases = {case["id"]: case for case in scenarios["cases"]}
    assert set(cases) == {
        "filtered-ado-reader", "two-addenda", "held-prefix", "worker-turn-ended",
        "post-before-local-failure", "healthy-identity-stale-source",
        "repeated-status", "registrar-restored",
    }
    assert all(case["given"] and case["expected"] for case in cases.values())
    assert "do not post again" in cases["post-before-local-failure"]["expected"]
    assert "identity success alone is not clearance" in cases["healthy-identity-stale-source"]["expected"]
    assert "No automatic takeover" in cases["registrar-restored"]["expected"]
