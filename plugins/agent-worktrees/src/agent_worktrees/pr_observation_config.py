"""Repository-owned observation policy; configuration never waives native gates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyRule:
    name: str = ""
    policy_id: str = ""
    context_name: str = ""
    context_genre: str = ""
    kind: str = "agent"

    def matches(self, policy: dict) -> bool:
        selectors = (
            (self.name, policy.get("name")),
            (self.policy_id, str(policy.get("id", ""))),
            (self.context_name, policy.get("context_name")),
            (self.context_genre, policy.get("context_genre")),
        )
        return all(not expected or expected.casefold() == str(actual or "").casefold()
                   for expected, actual in selectors)


@dataclass(frozen=True)
class ObservationConfig:
    enabled: bool = True
    reviews: bool = True
    comments: bool = True
    policies: bool = True
    builds: bool = True
    minimum_approvals: int = 1
    non_author: bool = True
    approval_after_push: bool = True
    required_reviewers: bool = True
    resolved_comments: bool = True
    no_blocking_votes: bool = True
    policy_rules: tuple[PolicyRule, ...] = ()
    guidance: str = ""


def _mapping(value: object, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{label} has unknown keys: {', '.join(sorted(map(str, unknown)))}")
    return value


def _boolean(data: dict, key: str, default: bool, label: str) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{label}.{key} must be a boolean")
    return value


def parse_observation(raw: object) -> ObservationConfig | None:
    if raw is None:
        return None
    data = _mapping(raw, {"enabled", "features", "requirements", "policy_rules", "guidance"}, "pr.observation")
    features = _mapping(data.get("features", {}), {"reviews", "comments", "policies", "builds"},
                        "pr.observation.features")
    requirements = _mapping(
        data.get("requirements", {}),
        {"minimum_approvals", "non_author", "approval_after_push", "required_reviewers",
         "resolved_comments", "no_blocking_votes"}, "pr.observation.requirements",
    )
    count = requirements.get("minimum_approvals", 1)
    if type(count) is not int or not 0 <= count <= 100:
        raise ValueError("pr.observation.requirements.minimum_approvals must be an integer from 0 to 100")
    rules = data.get("policy_rules", [])
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("pr.observation.policy_rules must be a list of at most 100 rules")
    parsed = []
    for raw_rule in rules:
        rule = _mapping(raw_rule, {"name", "policy_id", "context_name", "context_genre", "kind"},
                        "pr.observation.policy_rules[]")
        values = {}
        for key in ("name", "policy_id", "context_name", "context_genre"):
            value = rule.get(key, "")
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError(f"pr.observation.policy_rules[].{key} must be a bounded string")
            values[key] = value.strip()
        if not any(values.values()):
            raise ValueError("pr.observation.policy_rules[] needs a policy/context selector")
        kind = rule.get("kind", "agent")
        if kind not in ("agent", "human", "composite"):
            raise ValueError("pr.observation.policy_rules[].kind must be agent, human or composite")
        parsed.append(PolicyRule(**values, kind=kind))
    guidance = data.get("guidance", "")
    if not isinstance(guidance, str) or len(guidance) > 16_384:
        raise ValueError("pr.observation.guidance must be a string of at most 16384 characters")
    return ObservationConfig(
        enabled=_boolean(data, "enabled", True, "pr.observation"),
        **{key: _boolean(features, key, True, "pr.observation.features")
           for key in ("reviews", "comments", "policies", "builds")},
        minimum_approvals=count,
        **{key: _boolean(requirements, key, True, "pr.observation.requirements")
           for key in ("non_author", "approval_after_push", "required_reviewers",
                       "resolved_comments", "no_blocking_votes")},
        policy_rules=tuple(parsed), guidance=guidance.strip(),
    )
