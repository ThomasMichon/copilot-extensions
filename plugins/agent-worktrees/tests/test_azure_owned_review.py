"""Protected response evidence remains additive, ephemeral and confirmation-bound."""

import copy

import pytest

from agent_worktrees.providers.azure_owned_review import (
    AdoReviewContext, NativeActionOutcome, NativeReviewResponse,
)


@pytest.mark.parametrize("failure", [None, "late", "content", "identity", "partial"])
def test_original_protected_response_is_exposed_only_on_confirmation(failure):
    scope = {
        "endpoint": "https://dev.azure.com/example", "project_id": "project",
        "repository_id": "repository", "principal_id": "principal", "number": 42,
    }
    response = {
        "id": 1, "comments": [{"id": 1, "content": "Selected text", "author": {"id": "principal"}}],
    }
    if failure == "content":
        response["comments"][0]["content"] = "Different text"
    elif failure == "identity":
        response["comments"][0]["author"]["id"] = "other"
    elif failure == "partial":
        response.pop("comments")
    binding = object()
    context = AdoReviewContext(
        scope, request=lambda *args, **kwargs: NativeReviewResponse(201, response, late=failure == "late"),
        credential_binding=lambda: binding, checkpoint=lambda: None, review_check=lambda *args: None,
    )
    context.remaining = lambda: 10
    operation = {"intent": {"scope": scope, "operation_id": "operation"}, "intent_id": "intent"}
    result = context.perform(operation, {"id": "comment", "kind": "comment", "body": "Selected text"})
    assert result.confirmed is (failure is None)
    assert result.response == (response if failure is None else None)
    if failure is None:
        original = copy.deepcopy(result.response)
        response["comments"][0]["content"] = "Later mutation"
        assert result.response == original
        assert "Selected text" not in repr(result)
    assert set(result.receipt) == {"origin", "platform_ids", "proof"}
    assert NativeActionOutcome(result.receipt, result.confirmed).response is None
