"""Structural guidance regressions, not runtime authorization qualification."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "review-pull-requests"
ROOT = PLUGIN.parents[1]
REFUSALS = (
    "Original grant",
    "Task binding required by selected tool",
    "Author consent required by actual merge policy",
    "Required provider write safeguard",
    "Old owner's effect/custody",
)


@pytest.fixture(autouse=True)
def _isolate_discovery() -> None:
    """Static guidance checks do not use runtime endpoint discovery."""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _missing_refusals(text: str) -> set[str]:
    rows = {
        line.split("|")[1].strip()
        for line in text.splitlines()
        if line.startswith("|") and line.count("|") == 3
    }
    return set(REFUSALS) - rows


def _validate_unobserved_example(example: dict[str, Any]) -> None:
    """Check only this synthetic unobserved fixture, not operational state."""
    assert example["example_only"] is True
    assert example["example_format_version"] == 1
    assert "Synthetic, non-authoritative" in example["description"]
    assert example["original_grant_reference"] is None
    assert example["charter_reference"] is None
    target = example["lane_choice"]["desired_active"]
    assert type(target) is int and target > 0
    limit = example["lane_choice"]["hard_limit"]
    assert limit is None or (type(limit) is int and limit > 0)
    assert "work-conserving" in example["lane_choice"]["expansion"]
    assert example["lane_choice"]["mode"] in {"continuous", "finite-drain"}
    for candidate in example["candidates"]:
        for field in (
            "task_binding",
            "reviewer_handle",
            "consent_evidence",
            "accepted_transfer_reference",
            "provider_terminal_observation",
        ):
            assert candidate[field] is None
        assert candidate["old_owner_effects"] == "unknown"
        assert candidate["current_candidate"]["complete"] is None
        assert all(
            candidate["current_candidate"][field] is None
            for field in ("head", "base", "signature", "observed_at")
        )
        pending = candidate["pending_round"]
        for field in (
            "current_fence_reference",
            "preflight_reference",
            "revision_binding_reference",
            "original_prepare_receipt",
            "original_post_receipt",
            "original_readback_receipt",
            "native_review_id",
        ):
            assert pending[field] is None
        assert pending["effect_state"] == "unobserved"
        assert pending["server_fence_support"] == "unknown"
        assert pending["unknown_evidence"]
        assert candidate["watch"]["mode"] is None
        assert candidate["watch"]["returned_cursor"] is None
        assert candidate["watch"]["last_successful_observation_at"] is None
        assert candidate["communication"]["semantic_acceptance"] is None
        assert candidate["communication"]["cooldown_reservation"] is None
        assert "no write" in candidate["next_action"]


def test_skill_frontmatter_layout_and_discoverability() -> None:
    text = _read(SKILL / "SKILL.md")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match is not None
    frontmatter = match.group(1)
    assert f"name: {SKILL.name}\n" in frontmatter
    description = frontmatter.split("description: >", 1)[1]
    assert 0 < len(description) <= 1024
    assert len(text[match.end():].splitlines()) < 500
    assert "set up sustained PR attention" in description
    assert "single code review" in description
    assert {p.name for p in SKILL.iterdir()} == {
        "SKILL.md", "references", "assets"
    }
    manifest = json.loads(_read(PLUGIN / "plugin.json"))
    assert "skills/" in manifest["skills"]
    assert f"skills/{SKILL.name}/SKILL.md" in _read(PLUGIN / "README.md")


@pytest.mark.parametrize("name", [
    "review-pull-requests", "monitor-agent-fleet", "triage-issue-backlog", "managing-agent-topology",
])
def test_all_links_resolve_and_every_companion_is_reachable(name: str) -> None:
    skill = PLUGIN / "skills" / name
    linked: set[Path] = set()
    documents = list(skill.rglob("*.md"))
    for document in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", _read(document)):
            assert not target.startswith(("http:", "file:", "\\"))
            assert "://" not in target
            path = (document.parent / target.split("#", 1)[0]).resolve()
            assert path.is_relative_to(ROOT.resolve())
            assert path.is_file(), (document.name, target)
            linked.add(path)
    companions = {
        p.resolve() for folder in ("references", "assets")
        for p in (skill / folder).iterdir()
    }
    assert companions <= linked
    assert all("../SKILL.md" in _read(p) for p in documents
               if p.name != "SKILL.md")


def test_migrated_adapter_links_still_name_dispatch_owners() -> None:
    document = SKILL / "references" / "setup-and-adapters.md"
    links = dict(re.findall(r"\[([^\]]+)\]\(([^)]+)\)", _read(document)))
    expected = {
        "Dispatch configuration reference": "docs/configuration.md",
        "Dispatch skill": "skills/agent-dispatch/SKILL.md",
        "registrar recipe reference": "README.md",
        "registrar discovery reference": "docs/spawn-supervisor.md",
    }
    for label, destination in expected.items():
        assert (document.parent / links[label]).resolve() == (
            ROOT / "plugins" / "agent-dispatch" / destination
        ).resolve()


def test_ordered_setup_and_actual_owner_limitations() -> None:
    text = _read(SKILL / "SKILL.md")
    assert len(re.findall(r"^## [1-6]\. ", text, re.M)) == 6
    for requirement in (
        "original grant",
        "positive integer concurrency target",
        "before** prescribing",
        "outside the candidate/source repository",
        "session SQLite",
        "complete, paginated live forge census",
        "fresh context for an unrelated PR",
        "never execute",
        "without making\nserver fencing mandatory",
        "actual returned cursors",
        "Approval is not author consent",
        "No generic administrative bypass",
        "parent completion gate",
    ):
        assert requirement in text, requirement
    setup = _read(SKILL / "references" / "setup-and-adapters.md")
    assert "**unbound**" in setup and "owner-unavailable" in setup
    assert "missing adapter owner/operation" in setup
    assert all(profile in setup for profile in (
        "Provider-native automatic merge", "pr-agent-merge",
        "pr-self-merge", "pr-human-merge", "`direct`",
    ))
    assert "not a made-up gate" in setup


def test_required_refusal_controls_are_present() -> None:
    text = _read(SKILL / "references" / "coordination-and-recovery.md")
    assert not _missing_refusals(text)
    assert "Their absence\n   alone does not block" in text
    assert "full exact original receipts" in text
    assert "Null IDs remain null" in text
    assert "**same-owner**" in text and "**new-owner**" in text
    assert "Static documentation tests do not qualify runtime" in _read(
        SKILL / "assets" / "CHARTER.md"
    )


def test_loop_owner_separates_backlog_capacity_and_returned_results() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    coordination = " ".join(_read(
        SKILL / "references" / "coordination-and-recovery.md"
    ).split())
    assert "This skill owns the multi-reviewer coordination contract" in skill
    assert "charter owns one PR's judgment and same-PR follow-ups" in skill
    for requirement in (
        "eligibility population and priority order separate from concurrency",
        "returned handle is not an active reviewer",
        "Consume returned results and refill eligible vacancies",
        "a returned result alone neither releases a claim",
        "nor permits replay of an uncertain write",
        "blocks independent reviews only where a concrete dependency exists",
    ):
        assert requirement in coordination, requirement


def test_guidance_resolution_separates_contexts_and_uses_existing_owners() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    setup = " ".join(_read(
        SKILL / "references" / "setup-and-adapters.md"
    ).split())
    assert "not because Dispatch is mandatory" in skill
    assert "a role charter need not live in a recipe" in skill
    for requirement in (
        "executing harness",
        "approved-base target guidance",
        "Candidate changes are proposals",
        "state-root --json",
        "state-root --pair --json",
        "registrar doctor --json",
        "registrar discover-repo <resolved-declaration-repository-root>",
        "identities show <selected-name> --repo-root <defining-supplier-root>",
        "source/path/shadows/errors",
        "not a full provenance trace",
        "no Dispatch registrar is required",
        "enabled declarations and safe paths",
        "does not authenticate instruction content",
        "Read the full selected body",
        "not one universal precedence chain",
        "silently swap the acting identity",
        "disable existing required provider safeguards",
        "author/reviewer independence",
        "unresolved/missing/ambiguous/conflicting inputs",
    ):
        assert requirement in setup, requirement
    charter = _read(SKILL / "assets" / "CHARTER.md")
    assert "Executing harness/control owner and reviewed target/base" in charter
    assert "not candidate-supplied authority" in charter


def test_reviewer_posts_directly_without_per_round_coordinator_release() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    coordination = " ".join(_read(
        SKILL / "references" / "coordination-and-recovery.md"
    ).split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    for requirement in (
        "independent reviewer owns each qualifying round end-to-end",
        "direct task/owner-bound verdict publication",
        "author independence and the retained handle once",
        "do not require a coordinator's per-round approval or release handshake",
        "asynchronously returns its actual posted, verified receipt or real blocker",
        "never the normal verdict relay/editor",
        "monitors outcomes, coverage and unresolved obligations",
        "concrete risk or integration need",
        "no author-fix, merge, consent or resource-custody authority",
    ):
        assert requirement in skill, requirement
    for requirement in (
        "supported addressed bot-thread reconciliation",
        "neither relays reviewer JSON nor rewrites/posts the verdict",
        "actual verified posted receipt or real blocker asynchronously",
        "same reviewer for owner-bound reprepare/delta/full-gate",
        "Hold/escalate only actual required-input",
        "adapter exception, not a mandatory per-round middleman",
        "not transfer custody",
        "not a mandatory interceptor or duplicate per-write auditor",
        "reviewer remains responsible for its required publication safeguards",
    ):
        assert requirement in coordination, requirement
    assert "without mandatory per-round coordinator approval/release" in charter
    assert "not verdict relay" in charter
    assert "outcome monitoring" in charter
    assert "not a mandatory duplicate per-write forge audit" in charter
    for text in (skill, coordination, charter):
        assert "independently audits forge authority" not in text
        assert "independently audit current forge authority" not in text
        assert "post-publication audit" not in text


@pytest.mark.parametrize("gate", REFUSALS)
def test_removing_a_refusal_control_is_detected(gate: str) -> None:
    text = _read(SKILL / "references" / "coordination-and-recovery.md")
    mutated = "\n".join(
        line for line in text.splitlines()
        if not line.startswith(f"| {gate} |")
    )
    assert _missing_refusals(mutated) == {gate}


def test_peer_protocol_keeps_original_scope_and_reserves_before_send() -> None:
    text = _read(SKILL / "references" / "communication.md")
    for requirement in (
        "agent-conduct-guidance:using-multi-agent-rules-of-engagement",
        "agent-bridge:agent-bridge",
        "original bounded intake reference",
        "semantic",
        "raw operator transcript",
        "original idempotency key/body",
        "reconcile before a new send",
        "same retained context",
        "persist the cooldown reservation",
        "If persistence fails,\ndo not send",
        "Retain the reservation on unknown admission",
        "Require explicit acceptance",
    ):
        assert requirement in text, requirement


def test_native_host_route_retains_handle_without_bridge_or_polling() -> None:
    text = _read(SKILL / "references" / "communication.md")
    native = text.split("## Native in-session Task route", 1)[1].split(
        "## Bridge retained conversation", 1
    )[0]
    for requirement in (
        "fresh independent reviewer",
        "each unrelated PR",
        "same-PR follow-ups through `write_agent`",
        "continue independent work rather than polling",
        "automatic completion notification",
        "`read_agent` once",
        "known `agent_id`",
        "`since_turn` cursor",
        "omit an unknown",
        "`list_agents` to rediscover",
        "Do not add Bridge idempotency flags",
    ):
        assert requirement in native, requirement
    assert "native Task does not require Bridge" in text
    assert "Bridge installation requirement" in native
    assert "<agent-bridge catalog argv[0]>" not in native


def test_provider_write_safety_is_capability_aware_not_a_universal_fence() -> None:
    coordination = " ".join(_read(
        SKILL / "references" / "coordination-and-recovery.md"
    ).split())
    for requirement in (
        "where supported",
        "preserve any safeguard the selected owner actually requires",
        "Their absence alone does not block otherwise authorized review publication",
        "refresh relevant state immediately before writing",
        "record the residual race",
        "not an atomic current-head transaction",
        "keep any posted effect as historical",
        "never claim stale authority is current or blindly repeat the write",
    ):
        assert requirement in coordination, requirement
    readme = " ".join(_read(PLUGIN / "README.md").split())
    assert "Optional server fencing is not a universal prerequisite" in readme
    assert "official readback without claiming atomicity" in readme


def test_forge_polling_fallback_preserves_clocks_without_polling_native_tasks() -> None:
    coordination = " ".join(_read(
        SKILL / "references" / "coordination-and-recovery.md"
    ).split())
    for requirement in (
        "If webhooks or a subscribable service are unavailable",
        "bounded shared polling is a supported fallback",
        "one observation owner",
        "last successful observation/completeness",
        "partial/failed enumeration",
        "Do not fabricate a cursor",
        "not native Task completion",
        "not a completed review",
    ):
        assert requirement in coordination, requirement


def test_work_conserving_target_and_original_reviewer_survive_capacity_changes() -> None:
    coordination = " ".join(_read(
        SKILL / "references" / "coordination-and-recovery.md"
    ).split())
    for requirement in (
        "the target is not a ceiling",
        "explicit operator/host limits and budgets",
        "Record a work/capacity shortage",
        "A per-worktree target is not a machine-wide capacity grant",
        "a known instability threshold is not a usable capacity allowance",
        "another owner's sessions",
        "Try the retained handle before replacement",
        "If the host is one-shot or confirms context loss",
        "prior findings, author replies, assessed source, receipts and unknown effects",
        "Keep the original handle and replacement reason",
        "A timeout alone is not confirmed context loss",
    ):
        assert requirement in coordination, requirement
    example = json.loads(_read(SKILL / "assets" / "ledger.example.json"))
    example["lane_choice"]["hard_limit"] = 1
    _validate_unobserved_example(example)


def test_loop_discipline_stops_repetition_and_hands_back_explicitly() -> None:
    skill = " ".join(_read(SKILL / "SKILL.md").split())
    loops = " ".join(_read(
        SKILL / "references" / "throughput-and-loops.md"
    ).split())
    charter = " ".join(_read(SKILL / "assets" / "CHARTER.md").split())
    communication = " ".join(_read(
        SKILL / "references" / "communication.md"
    ).split())
    for requirement in (
        "zero eligible PRs awaiting reviewer action",
        "Never republish an identical verdict on an unchanged head without new input",
        "Cap rounds without new author input per PR",
        "A watch timeout is a normal no-change result",
        "explicit hand-back naming the next owner",
        "do not re-review to prompt it",
        "two reviewers never serve the same scope",
    ):
        assert requirement in skill, requirement
    for requirement in (
        "is not new input",
        "Any qualifying trigger prompts reassessment of a standing change request",
        "Review only an overlapping delta",
        "report it once to the scope owner",
        "diagnose the cause once, retry once",
        "normal **no-change** result",
        "back off toward the charter's backstop cadence",
        "the reviewer never authors the fix",
        "re-check live state immediately before any request",
        "ask the scope owner once for a standing, recorded policy",
        "revisit time and the default action at revisit",
        "only with the original scope owner's authority",
        "Two reviewers must not serve the same scope",
        "not the reviewer's own live transcript, is the state",
    ):
        assert requirement in loops, requirement
    for requirement in (
        "never an identical verdict on an unchanged head",
        "Closed hold vocabulary with owner, revisit time and default action",
        "Predecessor told and released after accepted transfer",
    ):
        assert requirement in charter, requirement
    for requirement in (
        "Immediately before each send, re-check live state",
        "consent already present cancels only a consent request",
        "unchanged state within its cooldown",
        "the predecessor stands down there",
        "Never leave two reviewers serving one scope",
    ):
        assert requirement in communication, requirement


def test_effect_ids_and_receipt_timing_are_not_fabricated_prerequisites() -> None:
    text = _read(SKILL / "SKILL.md")
    assert "provider-native review/comment effect IDs as returned" in text
    assert "conversation UUID stays\nnull unless actually observed" in text
    assert "cannot invalidate a legitimate verified forge post" in text
    coordination = _read(SKILL / "references" / "coordination-and-recovery.md")
    before = coordination.split("4. Persist the mutation", 1)[1].split("5.", 1)[0]
    after = coordination.split("5. After the call", 1)[1].split("6.", 1)[0]
    assert "prior original receipts" in before
    assert "Do not precreate" in before
    assert "resulting original response/receipt" in after


def test_synthetic_example_is_unobserved_not_authority() -> None:
    example = json.loads(_read(SKILL / "assets" / "ledger.example.json"))
    _validate_unobserved_example(example)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "2", None])
def test_example_rejects_invalid_concurrency_target(limit: object) -> None:
    example = json.loads(_read(SKILL / "assets" / "ledger.example.json"))
    example["lane_choice"]["desired_active"] = limit
    with pytest.raises(AssertionError):
        _validate_unobserved_example(example)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "2"])
def test_example_rejects_invalid_explicit_hard_limit(limit: object) -> None:
    example = json.loads(_read(SKILL / "assets" / "ledger.example.json"))
    example["lane_choice"]["hard_limit"] = limit
    with pytest.raises(AssertionError):
        _validate_unobserved_example(example)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("root", "original_grant_reference"),
        ("candidate", "task_binding"),
        ("candidate", "consent_evidence"),
        ("round", "current_fence_reference"),
        ("candidate", "accepted_transfer_reference"),
    ],
)
def test_unknown_example_cannot_be_relabelled_as_authorized(
    section: str, field: str
) -> None:
    example = json.loads(_read(SKILL / "assets" / "ledger.example.json"))
    mutated = copy.deepcopy(example)
    target = mutated
    if section == "candidate":
        target = mutated["candidates"][0]
    elif section == "round":
        target = mutated["candidates"][0]["pending_round"]
    target[field] = "synthetic unsupported assertion"
    with pytest.raises(AssertionError):
        _validate_unobserved_example(mutated)
