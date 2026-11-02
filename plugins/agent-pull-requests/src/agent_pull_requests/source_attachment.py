"""PR-domain attachment over the existing selected local source owner."""

from __future__ import annotations

import copy

from .review_workspace import _generation, _revision, _scope


def _selection(generation):
    generation = copy.deepcopy(generation)
    scope = _scope(generation.get("scope"))
    _generation(generation, scope)
    result = {
        "schema": "agent-pull-requests.source-attachment", "version": 1,
        "generation_id": generation["id"], "scope": copy.deepcopy(scope),
        "revision": copy.deepcopy(generation["revision"]),
        "state": "unavailable", "source": None, "errors": [],
        "review_authority": False,
    }
    sections, revision = generation["sections"], generation["revision"]
    if (
        not all(sections[name]["complete"] for name in ("pull_request", "iterations", "changes"))
        or not all(revision.get(name) for name in ("head_sha", "common_sha", "iteration"))
        or revision != _revision(sections)
        or any(sections["changes"].get("revision", {}).get(key) != revision[key]
               for key in ("head_sha", "common_sha", "iteration"))
    ):
        result["errors"] = ["source_evidence_incomplete"]
        return result, None
    return result, {
        "scope": copy.deepcopy(scope), "revision": copy.deepcopy(revision),
        "changes": copy.deepcopy(sections["changes"]["data"]),
    }


ACQUISITION_CONTRACT = {
    "schema": "agent-pull-requests.source-acquisition", "version": 1,
    "range": "exact_head_common", "depth": 1, "storage": "selected_dedicated_shallow",
    "resource_owner": "existing_workspace_owner",
}
EXECUTION_CONTRACT = {
    "schema": "agent-pull-requests.source-execution", "version": 1,
    "execution": "owned_async", "release": "terminal_drain",
}


def _supported(source_owner, prepare):
    contract = getattr(source_owner, "contract", None)
    capability = contract() if callable(contract) else None
    if not isinstance(capability, dict) or type(capability.get("version")) is not int or capability != {
        "schema": "agent-pull-requests.local-source-contract", "version": 1,
        "backend_access": False, "fetch": False, "checkout": False,
        "retention": "immutable_git_refs", "resource_owner": "existing_workspace_owner",
    }:
        return False
    acquisition = getattr(source_owner, "acquisition_contract", None)
    declared = acquisition() if callable(acquisition) else None
    return not prepare or (
        isinstance(declared, dict) and type(declared.get("version")) is int
        and declared == ACQUISITION_CONTRACT
    )


def attach_source(generation: dict, source_owner, *, prepare=False, **limits) -> dict:
    """Return a separate envelope; acquisition requires its separately bound owner."""
    result, request = _selection(generation)
    if request is None:
        return result
    operation = getattr(source_owner, "prepare_and_retain" if prepare else "inspect_and_retain", None)
    if not callable(operation) or not _supported(source_owner, prepare):
        result["errors"] = ["source_owner_unsupported"]
        return result
    return _finish(result, operation(request, **limits))


async def attach_source_async(generation, source_owner, *, prepare=False, **limits):
    result, request = _selection(generation)
    if request is None:
        return result
    execution = getattr(source_owner, "execution_contract", None)
    declared = execution() if callable(execution) else None
    if (
        not _supported(source_owner, prepare) or declared != EXECUTION_CONTRACT
        or not isinstance(declared, dict) or type(declared.get("version")) is not int
        or not callable(getattr(source_owner, "execute", None))
    ):
        result["errors"] = ["source_owner_unsupported"]
        return result
    source = await source_owner.execute(
        "prepare_and_retain" if prepare else "inspect_and_retain", request, **limits,
    )
    return _finish(result, source)


def _finish(result, source):
    scope, revision = result["scope"], result["revision"]
    if (
        not isinstance(source, dict) or source.get("schema") != "agent-pull-requests.local-source"
        or type(source.get("version")) is not int or source["version"] != 1
        or source.get("scope") != scope or source.get("revision") != revision
        or type(source.get("complete")) is not bool
        or not isinstance(source.get("errors"), list)
        or any(not isinstance(error, str) or not error for error in source["errors"])
        or not isinstance(source.get("files"), list)
        or not isinstance(source.get("retained_refs"), dict)
        or source["complete"] and (source["errors"] or set(source["retained_refs"]) != {"head", "common"})
    ):
        raise ValueError("selected source owner returned an incompatible attachment")
    if source["complete"]:
        for role, key in (("head", "head_sha"), ("common", "common_sha")):
            retained = source["retained_refs"][role]
            if (
                not isinstance(retained, dict) or retained.get("object_id") != revision[key]
                or not isinstance(retained.get("ref"), str)
                or not retained["ref"].startswith("refs/agent-pull-requests/source/")
            ):
                raise ValueError("selected source owner returned an incompatible retained range")
    result["source"] = copy.deepcopy(source)
    result["state"] = "ready" if source["complete"] else "incomplete"
    result["errors"] = list(source["errors"])
    return result
