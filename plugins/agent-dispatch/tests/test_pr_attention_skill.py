"""Structural guidance regressions, not runtime authorization qualification."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "setting-up-pr-attention-flow"
ROOT = PLUGIN.parents[1]
REFUSALS = (
    "Original grant",
    "Task binding required by selected tool",
    "Author consent required by actual merge policy",
    "Current mutation fence",
    "Old owner's effect/custody",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _missing_refusals(text: str) -> set[str]:
    rows = {
        line.split("|")[1].strip()
        for line in text.splitlines()
        if line.startswith("|") and line.count("|") == 3
    }
    return set(REFUSALS) - rows


def _validate_unobserved_example(example: dict) -> None:
    """Check only this synthetic unobserved fixture, not operational state."""
    assert example["example_only"] is True
    assert example["example_format_version"] == 1
    assert "Synthetic, non-authoritative" in example["description"]
    assert example["original_grant_reference"] is None
    assert example["charter_reference"] is None
    limit = example["lane_choice"]["active_limit"]
    assert type(limit) is int and limit > 0
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
            "original_prepare_receipt",
            "original_post_receipt",
            "original_readback_receipt",
            "native_review_id",
        ):
            assert pending[field] is None
        assert pending["effect_state"] == "unobserved"
        assert pending["unknown_evidence"]
        assert candidate["watch"]["returned_cursor"] is None
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


def test_all_links_resolve_and_every_companion_is_reachable() -> None:
    linked: set[Path] = set()
    documents = [SKILL / "SKILL.md", *SKILL.rglob("*.md")]
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
        for p in (SKILL / folder).iterdir()
    }
    assert companions <= linked
    assert all("../SKILL.md" in _read(p) for p in SKILL.rglob("*.md")
               if p.name != "SKILL.md")


def test_ordered_setup_and_actual_owner_limitations() -> None:
    text = _read(SKILL / "SKILL.md")
    assert len(re.findall(r"^## [1-6]\. ", text, re.M)) == 6
    for requirement in (
        "original grant",
        "positive integer lane limit",
        "before** prescribing",
        "outside the candidate/source repository",
        "session SQLite",
        "complete, paginated live forge census",
        "fresh context for an unrelated PR",
        "never execute",
        "server write fences or atomic provider preconditions",
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
    assert "naked write" in text
    assert "full exact original receipts" in text
    assert "Null IDs remain null" in text
    assert "**same-owner**" in text and "**new-owner**" in text
    assert "Static documentation tests do not qualify runtime" in _read(
        SKILL / "assets" / "CHARTER.md"
    )


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
        "after publication",
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
    ):
        assert requirement in coordination, requirement
    assert "without mandatory per-round coordinator approval/release" in charter
    assert "not verdict relay" in charter


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
def test_example_rejects_invalid_lane_choice(limit: object) -> None:
    example = json.loads(_read(SKILL / "assets" / "ledger.example.json"))
    example["lane_choice"]["active_limit"] = limit
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
