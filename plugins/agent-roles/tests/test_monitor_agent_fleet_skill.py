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
            "repair-authorized", "goal-establishment",
        }
        assert case["facts"].strip() and case["expected"].strip()
    assert {case["mode"] for case in cases} == {
        "read-only", "steering-authorized", "dormant-correction",
        "repair-authorized", "goal-establishment",
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


def test_fabric_reader_inventories_per_project_without_false_zeroes() -> None:
    text = " ".join(_read(SKILL / "references" / "fabric-readers.md").split())
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    for requirement in (
        "<worktrees-argv> --project <project> fleet --json",
        "covers **only the selected project**",
        "enumerate projects on each target host, never only on the observer",
        "agent-worktrees --project <name> list --json --passive",
        "never starts, recovers, waits for or wakes it, nor renews its scan demand",
        "Do not fall back to a plain or `--cache-only` `list --json`",
        "repos list --strict --json",
        "require `inventory_authority == \"complete\"`",
        "a host-level coverage gap",
        "derived `state == \"active\"`",
        "Do not use lifecycle `status == \"active\"`",
        "`live_rest`, `fact_provenance` and `observed_at`",
        "unobserved, not empty or offline",
        "never as zero active work",
        "Do not sweep with `list-sessions`",
        "Bound the command on the remote side",
    ):
        assert requirement in text, requirement
    assert "list-sessions --all-projects --json" not in text
    assert "execution procedure" not in text
    assert "never a session-history sweep" in skill


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


def test_parked_questions_are_answered_only_under_answer_scope() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    text = " ".join(_read(SKILL / "references" / "pending-input.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    steering = " ".join(_read(SKILL / "references" / "steering.md").split())
    readers = " ".join(_read(
        SKILL / "references" / "fabric-readers.md"
    ).split())
    assert "(references/pending-input.md)" in skill
    assert "only with recorded source-change and publication authority" in skill
    for requirement in (
        "**turn state alone does not rule out pending input**",
        "a read-only or steering grant does not authorize answers",
        "If persistence fails, do not answer",
        "exact request identifier",
        "never by blind re-answer",
        "**Never inject keystrokes, drive a terminal or impersonate the "
        "operator**",
        "Treat as operator-only",
        "relay it at the scope of the decision class",
        "rewrite options the worker narrowed on its own",
        "is a standing grant for that class until the task completes",
        "An explicitly chosen narrower option, target or limit stays",
        "Ask the worker to record the relayed grant in its own charter",
    ):
        assert requirement in text, requirement
    assert text.index("1. **Recheck.**") < text.index("2. **Persist first.**")
    assert "Answer scope (only if explicitly granted; else \"none\")" in charter
    assert "[parked questions](pending-input.md)" in steering
    assert "Answer the target's permission prompts" in steering
    assert "documented no-ensure switch" in readers
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert {"parked-routine-question", "parked-operator-only-question"} <= {
        case["id"] for case in scenarios["cases"]
    }


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
        "Re-read both from their current committed source at the start of "
        "each cycle",
        "The supervisory note holds only tactical, per-target state",
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


def test_enforced_goals_require_evidence_and_scope_g5_to_recurring() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    for requirement in (
        "## Enforced goals",
        "**A sweep report that lacks evidence for an applicable goal is "
        "incomplete**",
        "reported against G1-G4 and G6 only",
        "**G5 applies only to a recorded recurring assignment**",
        "**a fleet that stays on-charter without a monitor**",
        "**never self-declare an open-ended assignment done**",
        "never widen the charter's mode, steering, answer, repair or "
        "source-change authority",
    ):
        assert requirement in skill, requirement
    for goal in ("| G1 |", "| G2 |", "| G3 |", "| G4 |", "| G5 |", "| G6 |"):
        assert goal in skill, goal
    assert skill.index("## Enforced goals") < skill.index(
        "## 1. Establish the charter"
    )
    for requirement in (
        "Confidence cadence:",
        "Healthy sweeps to relax:",
        "Any waste finding returns to the confidence cadence",
        "A coverage gap or unknown target earns no healthy-sweep credit",
        "a missing goal marks the sweep incomplete",
    ):
        assert requirement in charter, requirement
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert {
        "finite-request-goal-evidence", "recurring-confidence-cadence",
    } <= {case["id"] for case in scenarios["cases"]}


def test_self_improvement_loop_routes_learnings_by_owner() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    loop = " ".join(_read(SKILL / "references" / "self-improvement.md").split())
    template = _read(SKILL / "assets" / "WEAK-POINTS.md")
    assert "(references/self-improvement.md)" in skill
    assert "(assets/WEAK-POINTS.md)" in skill
    for requirement in (
        "## Improve the fleet, not just the agent (G3)",
        "## Improve this monitor (G4)",
        "**Capture in the ledger first.**",
        "Never stop a sweep to edit guidance or open a change",
        "**Fold at sweep end.**",
        "**Ride one rolling change.**",
        "as a batched, anonymized change",
        "needs recorded source-change and publication authority",
        "A reader that starts, resumes or repairs a service is never the fix",
    ):
        assert requirement in loop, requirement
    assert "**The consuming repository owns its filled-in catalogue**" in (
        template
    )
    for section in (
        "## Agent guidance weak points",
        "## Monitor blind spots",
        "## Reader discovery log",
    ):
        assert section in template, section


def test_thread_register_organizes_operator_messages() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    register = " ".join(_read(SKILL / "references" / "thread-register.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    assert "(references/thread-register.md)" in skill
    assert "Thread register:" in charter
    for column in (
        "| ID |",
        "| Kind |",
        "| Origin |",
        "| Owner |",
        "| Status |",
        "| Next check |",
        "| Exit criterion |",
    ):
        assert column in register, column
    for requirement in (
        "| One-shot |",
        "| Charter |",
        "| Steer-followup |",
        "| Side-quest |",
        "the monitor does not do the side-quest itself",
        "**Walk due threads first.**",
        "**Name the threads that moved.**",
        "A thread closes only when its recorded exit criterion is observed",
        "**Weak points go to the self-improvement thread.**",
        "It grants no authority",
    ):
        assert requirement in register, requirement


def test_failure_does_not_finish_the_turn_and_probes_name_credentials() -> None:
    patterns = " ".join(
        _read(SKILL / "references" / "misdirection-patterns.md").split()
    )
    checks = " ".join(_read(SKILL / "references" / "watchdog-checks.md").split())
    loop = " ".join(_read(SKILL / "references" / "self-improvement.md").split())
    assert "| Reported failure ends the turn |" in patterns
    assert "a reported failure does not finish the turn" in checks
    assert "| Turn end after failure |" in checks
    assert "names the exact credential reference" in loop


def test_fleet_run_learnings_keep_reads_passive_and_receipts_exact() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    readers = " ".join(_read(SKILL / "references" / "fabric-readers.md").split())
    steering = " ".join(_read(SKILL / "references" / "steering.md").split())
    checks = " ".join(_read(SKILL / "references" / "watchdog-checks.md").split())
    assert "`AGENT_BRIDGE_NO_ENSURE=1`" in readers
    assert "A reader run without the switch is not read-only" in readers
    assert "read the bound topology manifest by its explicit path" in readers
    assert "not stopped or complete" in readers
    assert "never selects an alternate steering recipient" in readers
    for requirement in (
        "the original body file",
        "including any trailing newline",
        "not that delivery failed; correct the binding, never resend",
        "not that the requested action ran",
        "record the stage and observation time, reobserve, never replay",
    ):
        assert requirement in steering, requirement
    for requirement in (
        "Hold work to the original acceptance gate, not a stricter one",
        "does not prove a wedged process",
        "Do not rerun an unavailable question reader",
        "can name a constituent model while the selected model is unchanged",
    ):
        assert requirement in checks, requirement
    assert "Model requirement:" in charter
    assert "Adaptive cadence:" in charter
    assert "replacing the one owned schedule" in skill
    assert "never run two" in skill
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert {
        "receipt-binding-conflict-not-failure",
        "constituent-model-notice",
        "adaptive-cadence-single-schedule",
    } <= {case["id"] for case in scenarios["cases"]}


def test_repair_is_graduated_delegated_and_separately_granted() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    text = " ".join(_read(SKILL / "references" / "repair-escalation.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    steering = " ".join(_read(SKILL / "references" / "steering.md").split())
    assert "(references/repair-escalation.md)" in skill
    assert "only a repair scope authorizes repair" in skill
    for requirement in (
        "**repairs applied: none**",
        "A read-only, steering, answer or dormant-correction grant does not "
        "authorize repair",
        "Climb one rung at a time",
        "Capture evidence before any mutation",
        "Wait at least one reobservation interval between rungs",
        "A command that self-heals, resumes or restarts is not a diagnostic",
        "Resume it; never reseed it",
        "requires the grant to name that service",
        "Never skip to a higher rung",
        "Those records are the evidence",
        "A delegate never messages, steers or answers the target",
        "so that two delegates never repair the same shared service",
        "A delegate inherits no more authority than the current rung",
        "A repair grant authorizes no message",
        "Missing recipient history does not prove non-delivery",
        "the original key and the identical request",
        "follows [pending input](pending-input.md)",
        "Never resend under a new key",
    ):
        assert requirement in text, requirement
    for rung in ("R0 Observe", "R1 Diagnose directly", "R2 Doctor records",
                 "R3 Recover transport or session", "R4 Disruptive"):
        assert rung in text, rung
    assert text.index("## Classify the blocker first") < text.index(
        "## The ladder"
    )
    for requirement in (
        "Repair scope (only if explicitly granted; else \"none\"",
        "Rung ceiling:",
        "Host-shared services:",
        "| repair",
    ):
        assert requirement in charter, requirement
    assert "[graduated repair](repair-escalation.md)" in steering
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert {
        "repair-undelivered-transport",
        "repair-identity-fault-ceiling",
        "repair-without-grant",
    } <= {case["id"] for case in scenarios["cases"]}


def test_agent_charters_drive_resolution_with_light_touch() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    text = " ".join(_read(SKILL / "references" / "agent-charters.md").split())
    template = _read(SKILL / "assets" / "AGENT-CHARTER.md")
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    assert "(references/agent-charters.md)" in skill
    assert "**Handle exploratory agents with a light touch**" in skill
    assert "Once an agent is set to work with a goal, stay on its case" in skill
    for requirement in (
        "**The agent's own charter is authoritative; the monitor's copy is a "
        "summary.**",
        "Never overwrite a live agent's record",
        "**charter backfill**",
        "not the reverse",
        "Waiting on the operator is a required wait, not waste",
        "treat the agent as exploratory",
        "**set to work with a goal**",
        "The monitor never finalizes, abandons or retires a worker itself",
        "Changing the root objective still requires its operator",
        "Never steer it toward completion",
        "Never push a premature finalization",
        "## Report churn",
    ):
        assert requirement in text, requirement
    for kind in ("**Completion**", "**Standing**", "**Exploratory**"):
        assert kind in text, kind
    for field in ("Kind: completion | standing | exploratory",
                  "Finalization criterion:", "Agent's own charter:",
                  "Divergence:"):
        assert field in template, field
    assert "Agent charter maintenance" in charter
    assert "exploratory targets received no completion pressure" in charter
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    assert {
        "exploratory-light-touch",
        "completion-finish-line",
        "standing-charter-no-completion-push",
        "charter-backfill-and-rework",
    } <= {case["id"] for case in scenarios["cases"]}

def test_goal_establishment_is_standing_but_bounded() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    text = " ".join(_read(SKILL / "references" / "agent-charters.md").split())
    steering = " ".join(_read(SKILL / "references" / "steering.md").split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    template = _read(SKILL / "assets" / "AGENT-CHARTER.md")
    patterns = _read(SKILL / "references" / "misdirection-patterns.md")
    assert "**Goal establishment is role-inherent.**" in skill
    assert "**Drive by outcome goals, not activity.**" in skill
    assert "never the current activity" in skill
    for requirement in (
        "## Drive by outcome goals, not activity",
        "## Standing goal-establishment authority",
        "**equivalent supervisory agent**",
        "It needs no separate steering grant for them",
        "wins over every other source",
        "it is never operator authority and never widens what the agent may do",
        "keeps **messages sent: none**",
        "An all-work hold or a recorded exclusion still means no message",
        "never changes an operator-defined root",
        "never edits a live agent's charter",
        "Do not send a second goal steer on the same evidence",
        "still need the charter's steering scope",
    ):
        assert requirement in text, requirement
    assert "standing goal-establishment authority" in steering
    assert "If persistence fails, do not send" in steering
    assert "Operator-goal register:" in charter
    assert "Goal establishment: <standing (role-inherent default)" in charter
    assert "no operator-defined goal was paraphrased" in charter
    for field in ("Root source:", "Alignment:", "Projected progress:"):
        assert field in template, field
    assert "| Activity mistaken for goal |" in patterns
    scenarios = json.loads(_read(SKILL / "assets" / "scenarios.json"))
    cases = {case["id"]: case for case in scenarios["cases"]}
    assert "messages sent: none" in cases["goal-steer-read-only-sweep"]["expected"]
    assert {
        "activity-shaped-root-goal-steer",
        "goal-steer-disputed-draft",
    } <= set(cases)