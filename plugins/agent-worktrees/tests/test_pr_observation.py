"""Repository policy and honest readiness on the shared observation boundary."""

from dataclasses import replace

import pytest

from agent_worktrees.pr_observation import Gate, Observation, Vote, configured_snapshot
from agent_worktrees.pr_observation_config import ObservationConfig, parse_observation


def observation(*gates, **kwargs):
    return Observation("azure-devops", "Project/repository", 7, "https://dev.azure.com/example",
                       "head", gates=tuple(gates), **kwargs)


def test_observation_config_missing_is_distinct_from_explicit_defaults():
    assert parse_observation(None) is None
    assert parse_observation({}) == ObservationConfig()


def test_related_configuration_has_independent_features_requirements_and_guidance():
    config = parse_observation({
        "features": {"builds": False},
        "requirements": {"minimum_approvals": 2, "approval_after_push": True, "non_author": True},
        "policy_rules": [{"context_name": "presence", "kind": "human"},
                         {"name": "Composite compliance", "kind": "composite"}],
        "guidance": "Keep the source branch; never perform human attestation.",
    })
    assert config.reviews and config.comments and config.policies
    assert not config.builds
    assert config.minimum_approvals == 2 and config.approval_after_push and config.non_author
    assert config.policy_rules[0].matches({"context_name": "Presence"})
    assert not config.policy_rules[0].matches({"context_name": "different"})
    assert config.guidance.startswith("Keep")


@pytest.mark.parametrize("raw", [
    [], {"extra": True}, {"features": {"comments": "false"}},
    {"features": {"typo": True}}, {"requirements": {"minimum_approvals": True}},
    {"requirements": {"minimum_approvals": -1}},
    {"requirements": {"minimum_approvals": 101}},
    {"requirements": {"approval_after_push": 1}},
    {"policy_rules": [{"kind": "human"}]},
    {"policy_rules": [{"name": "A", "kind": "ignore"}]},
    {"policy_rules": [{"name": "A", "unknown": "value"}]},
    {"enabled": "true"}, {"guidance": ["not", "text"]},
])
def test_malformed_policy_never_silently_selects_permissive_defaults(raw):
    with pytest.raises(ValueError):
        parse_observation(raw)


def test_disabled_observation_is_explicit_not_a_false_ready_result():
    assert parse_observation({"enabled": False}).enabled is False
    payload = observation().to_dict()
    assert not payload["ready_for_human"]
    assert not payload["all_required_satisfied"]


def test_human_checkpoint_requires_complete_nonhuman_evidence():
    human = Gate("latest_push_approval", "review", "blocked", "human", "Fresh approval needed")
    build = Gate("build", "build", "satisfied", "external", "Build passed")
    assert observation(human, build, complete=True).to_dict()["ready_for_human"]
    assert not observation(human, build).to_dict()["ready_for_human"]
    for state in ("unknown", "pending", "blocked"):
        assert not observation(human, replace(build, state=state), complete=True).to_dict()["ready_for_human"]


def test_unknown_human_evidence_is_not_a_known_human_checkpoint():
    payload = observation(Gate("review", "review", "unknown", "human", "Cannot read votes"),
                          complete=True).to_dict()
    assert payload["unknown"] == ["review"]
    assert not payload["ready_for_human"]


def test_composite_may_wait_on_known_human_gates_without_waiving_it():
    human = Gate("fresh", "review", "blocked", "human", "Fresh approval needed")
    composite = Gate("policy:1", "composite", "waiting", "external", "Waiting for review",
                     waiting_for=("fresh",))
    payload = observation(human, composite, complete=True).to_dict()
    assert payload["ready_for_human"]
    assert not payload["all_required_satisfied"]
    assert not observation(human, replace(composite, waiting_for=("missing",)),
                           complete=True).to_dict()["ready_for_human"]


def test_gate_satisfaction_and_action_are_not_an_overall_review_verdict():
    gate = Gate("comments", "discussion", "blocked", "agent", "Unresolved thread")
    payload = observation(gate, votes=(Vote("r", "Reviewer", 10),), complete=True).to_dict()
    assert payload["agent_actionable"] == ["comments"]
    assert not payload["all_required_satisfied"]
    assert not payload["ready_for_human"]


def test_fingerprints_include_vote_changes_and_do_not_depend_on_local_clock():
    first = observation(votes=(Vote("reviewer", "Reviewer", 10),), complete=True)
    assert first.to_dict()["fingerprints"] == first.to_dict()["fingerprints"]
    changed = replace(first, votes=(Vote("reviewer", "Reviewer", -5),))
    assert first.to_dict()["fingerprints"]["votes"] != changed.to_dict()["fingerprints"]["votes"]


def test_configured_driver_and_ordinary_provider_use_same_central_dispatch():
    class Config:
        observation = ObservationConfig()

    class Driver:
        def get_configured_snapshot(self, repo, number, **kwargs):
            return repo, number, kwargs["observation"]

    class Legacy:
        def get_snapshot(self, repo, number, **kwargs):
            return repo, number

    assert configured_snapshot(Driver(), "P/r", 7, prcfg=Config()) == ("P/r", 7, Config.observation)
    assert configured_snapshot(Legacy(), "o/r", 8, prcfg=Config()) == ("o/r", 8)


def test_draft_or_hold_cannot_be_mistaken_for_only_human_work():
    from agent_worktrees.pr_snapshot import PRSnapshot
    class Config:
        observation = ObservationConfig()
        hold_labels = ("hold",)
        wip_title_prefixes = ()

    class Driver:
        draft = False
        labels = ()
        def get_configured_snapshot(self, *args, **kwargs):
            evidence = observation(
                Gate("approval", "review", "blocked", "human", "Needs approval"), complete=True,
            )
            return PRSnapshot(draft=self.draft, labels=self.labels, observation=evidence)

    driver = Driver()
    driver.draft = True
    result = configured_snapshot(driver, "P/r", 7, prcfg=Config())
    assert not result.observation.to_dict()["ready_for_human"]
    driver.draft, driver.labels = False, ("HOLD",)
    result = configured_snapshot(driver, "P/r", 7, prcfg=Config())
    assert not result.observation.to_dict()["ready_for_human"]
