"""Provider request bodies and complete no-bypass policy observations."""

import copy
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import github_knowledge_repository as provider
from agent_worktrees.github_credentials import CredentialObservationError


def metadata(**changes):
    return {
        "id": 120, "full_name": "selected/knowledge", "private": True,
        "owner": {"type": "User", "login": "selected"}, "permissions": {"admin": True},
        "archived": False, "disabled": False, "default_branch": "provider-default", **changes,
    }


@pytest.fixture
def account():
    state = {
        "repository": metadata(), "classic": None, "rules": [],
        "details": {}, "calls": [], "post": metadata(), "pages": {},
    }

    def read(stage, path):
        state["calls"].append(("read", stage, path))
        if stage == "repository":
            return copy.deepcopy(state["repository"])
        if stage == "classic-policy":
            if state["classic"] is None:
                raise CredentialObservationError(stage, "access-unconfirmed", http_status=404)
            return copy.deepcopy(state["classic"])
        return copy.deepcopy(state["details"][int(path.rsplit("/", 1)[1])])

    def capture(stage, arguments, **kwargs):
        state["calls"].append(("capture", stage, list(arguments), kwargs))
        if stage in {"creation", "protection"}:
            return "HTTP/2 201 Created\r\n\r\n" + json.dumps(state["post"])
        page = int(arguments[-1].rsplit("=", 1)[1])
        body, header = state["pages"].get(page, (state["rules"], ""))
        return "HTTP/2 200 OK\r\n" + header + "\r\n\r\n" + json.dumps(body)

    return SimpleNamespace(
        repository="selected/knowledge", login="selected", json=read, capture=capture,
    ), state


def ruleset(state, *, bypass=None, enforcement="active", kinds=("deletion", "non_fast_forward")):
    state["rules"] = [
        {"type": kind, "ruleset_id": 7, "ruleset_source_type": "Repository"} for kind in kinds
    ]
    state["details"][7] = {
        "id": 7, "target": "branch", "source_type": "Repository", "source": "selected/knowledge",
        "enforcement": enforcement, "bypass_actors": [] if bypass is None else bypass,
        "rules": [{"type": kind} for kind in kinds],
    }


def test_fixed_create_and_protect_bodies_do_not_restrict_direct_or_feature_writes(account):
    selected, state = account
    observed = provider.create(selected)
    assert observed["id"] == 120 and observed["default_branch"] == "provider-default"
    create = state["calls"][-1]
    assert create[2][-3:] == ["https://api.github.com/user/repos", "--input", "-"]
    assert json.loads(create[3]["input_text"]) == {"name": "knowledge", "private": True, "auto_init": True}
    provider.protect(selected, observed)
    protect = state["calls"][-1]
    body = json.loads(protect[3]["input_text"])
    assert body == {
        "name": "Knowledge default-branch safeguards", "target": "branch", "enforcement": "active",
        "bypass_actors": [], "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    assert all(call[0] != "capture" or "--method" not in call[2] or "POST" in call[2] for call in state["calls"])


@pytest.mark.parametrize("changes", [
    {"id": True}, {"full_name": "selected/other"}, {"private": False},
    {"owner": {"type": "Organization", "login": "selected"}},
    {"owner": {"type": "User", "login": "elsewhere"}},
    {"permissions": {"admin": False}}, {"archived": True}, {"disabled": True},
    {"default_branch": ""}, {"default_branch": "bad\nbranch"},
])
def test_creation_response_never_confirms_wrong_private_personal_identity(account, changes):
    selected, state = account
    state["post"] = metadata(**changes)
    with pytest.raises(CredentialObservationError, match="identity-unconfirmed"):
        provider.create(selected)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("kind", ["classic", "ruleset", "mixed", "bypass", "missing", "partial"])
def test_equivalent_policy_requires_active_nonbypassable_both_safeguards(account, kind):
    selected, state = account
    if kind in {"classic", "mixed"}:
        state["classic"] = {
            "enforce_admins": {"enabled": True}, "allow_deletions": {"enabled": False},
            "allow_force_pushes": {"enabled": kind == "mixed"},
        }
    if kind in {"ruleset", "mixed", "bypass", "partial"}:
        ruleset(
            state, bypass=[{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}]
            if kind == "bypass" else None,
            kinds=("non_fast_forward",) if kind in {"mixed", "partial"} else ("deletion", "non_fast_forward"),
        )
    result = provider.safeguards(selected, provider.repository(selected))
    assert result["status"] == ("verified" if kind in {"classic", "ruleset", "mixed"} else "missing")
    assert all("POST" not in call[2] for call in state["calls"] if call[0] == "capture")


@pytest.mark.parametrize("change", ["disabled", "evaluate", "hidden-bypass", "source", "boolean-id", "malformed-classic"])
def test_incomplete_or_unsupported_policy_is_not_safeguard_proof(account, change):
    selected, state = account
    ruleset(state)
    if change in {"disabled", "evaluate"}:
        state["details"][7]["enforcement"] = change
    elif change == "hidden-bypass":
        state["details"][7].pop("bypass_actors")
    elif change == "source":
        state["rules"][0]["ruleset_source_type"] = "Organization"
    elif change == "boolean-id":
        state["rules"][0]["ruleset_id"] = True
    else:
        state["classic"] = {"allow_force_pushes": {"enabled": False}}
    with pytest.raises(CredentialObservationError):
        provider.safeguards(selected, provider.repository(selected))


@pytest.mark.parametrize("link", [
    '<https://other.invalid/>; rel="next"',
    '<https://api.github.com/repos/selected/other/rules/branches/provider-default?per_page=100&page=2>; rel="next"',
    '<https://api.github.com/repos/selected/knowledge/rules/branches/provider-default?per_page=100&page=9>; rel="next"',
])
def test_partial_or_redirected_policy_pages_are_refused(account, link):
    selected, state = account
    state["pages"][1] = ([], "Link: " + link)
    with pytest.raises(CredentialObservationError, match="invalid-pagination"):
        provider.safeguards(selected, provider.repository(selected))


def test_policy_drift_and_repository_replacement_are_refused(account):
    selected, state = account
    ruleset(state)
    original = selected.json
    reads = []

    def changing(stage, path):
        value = original(stage, path)
        if stage == "ruleset-policy":
            reads.append(True)
            if len(reads) == 2:
                value["bypass_actors"] = [{"actor_type": "RepositoryRole"}]
        return value

    selected.json = changing
    with pytest.raises(CredentialObservationError, match="changed"):
        provider.safeguards(selected, provider.repository(selected))
    selected.json = original
    expected = provider.repository(selected)
    state["repository"]["id"] = 121
    with pytest.raises(CredentialObservationError, match="repository-changed"):
        provider.safeguards(selected, expected)


def test_complete_policy_pages_are_read_without_following_arbitrary_links(account):
    selected, state = account
    ruleset(state)
    rules = state["rules"]
    state["pages"] = {
        1: ([rules[0]], 'Link: <https://api.github.com/repos/selected/knowledge/rules/branches/'
            'provider-default?per_page=100&page=2>; rel="next"'),
        2: ([rules[1]], ""),
    }
    assert provider.safeguards(selected, provider.repository(selected))["status"] == "verified"
    assert len([call for call in state["calls"] if call[:2] == ("capture", "ruleset-policy")]) == 4
