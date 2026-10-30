"""Finite native evidence export; no normalization, cache, source or review effects."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone

from .azure_devops import AzureDevOpsProvider
from .azure_observation_reads import AdoReader, MAX_RECORDS
from .base import ProviderError
from .observation_credentials import ObservationCredentialError, ObservationCredentials

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def _id(value: object) -> str:
    if (
        not isinstance(value, str) or not value.strip() or value != value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise ProviderError("ADO review evidence lacks canonical identity")
    return value


def _pr_scope(value: dict, number: int) -> tuple[str, str]:
    if type(value.get("pullRequestId")) is not int or value["pullRequestId"] != number:
        raise ProviderError("ADO review evidence PR identity mismatch")
    repository = value.get("repository")
    project = repository.get("project") if isinstance(repository, dict) else None
    if not isinstance(project, dict):
        raise ProviderError("ADO review evidence lacks project/repository identity")
    return _id(project.get("id")), _id(repository.get("id"))


def acquire_evidence(
    repo: str, number: int, endpoint: str, *, credential_source=None,
    credential_source_factory=None, budget: float,
    expected_principal: str | None = None, reader_factory=AdoReader,
    clock=time.monotonic, instant=lambda: datetime.now(timezone.utc).isoformat(),
) -> dict:
    """Renew observe credentials per request and bind each new token to one principal."""
    if (
        type(number) is not int or number <= 0
        or (credential_source is None) == (credential_source_factory is None)
        or credential_source is not None and not callable(credential_source)
        or credential_source_factory is not None and not callable(credential_source_factory)
        or not isinstance(repo, str) or not repo or not isinstance(endpoint, str) or not endpoint
        or type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0
    ):
        raise ProviderError("ADO review acquisition requires explicit scope, credentials and budget")
    if expected_principal is not None:
        _id(expected_principal)
    provider = AzureDevOpsProvider()
    endpoint = endpoint.rstrip("/")
    started = clock()
    deadline = started + budget
    observed_from = instant()
    principal = None
    principal_changed = False
    verified = {}
    identity_checks = 0

    def remaining():
        value = deadline - clock()
        if value <= 0:
            raise ProviderError("ADO review acquisition budget exhausted", transient=True)
        return value

    if credential_source_factory is not None:
        credential_source = credential_source_factory(remaining)
    if not callable(credential_source):
        raise ProviderError("ADO review credential factory did not return a callable owner")
    credentials = ObservationCredentials(
        credential_source, provider="azure-devops", api_base=endpoint, repository=repo,
    )

    def bound_source():
        nonlocal principal, identity_checks, principal_changed
        remaining()
        material = credentials.acquire()
        key = (material.forge.authentication, hashlib.sha256(material.token.encode()).digest())
        if key not in verified:
            probe = reader_factory(
                provider, repo, number, endpoint, None,
                credential_source=lambda: material, budget=remaining(), clock=clock,
            )
            try:
                body = probe.identity()
                actor = body.get("authorizedUser")
                current = _id(actor.get("id") if isinstance(actor, dict) else None)
            except ObservationCredentialError:
                raise
            except (ProviderError, ValueError, TypeError):
                raise ObservationCredentialError("ADO review principal is unavailable") from None
            if (
                expected_principal is not None and current != expected_principal
                or principal is not None and current != principal
            ):
                principal_changed = True
                raise ObservationCredentialError("ADO review credential principal changed")
            principal = current
            identity_checks += 1
            if len(verified) >= 128:
                verified.pop(next(iter(verified)))
            verified[key] = current
        return material

    reader = reader_factory(
        provider, repo, number, endpoint, None,
        credential_source=bound_source, budget=remaining(), clock=clock,
    )

    def perform(call):
        try:
            return call()
        except ObservationCredentialError:
            # The credential owner intentionally redacts arbitrary callback
            # failures. Recover only this operation's own safe refusal reason.
            if principal_changed:
                raise ObservationCredentialError("ADO review credential principal changed") from None
            remaining()
            raise

    def metadata():
        remaining()
        start = instant()
        value = perform(reader.pr)
        return {
            "data": value, "complete": True, "errors": [],
            "observed_from": start, "observed_to": instant(),
        }

    def collection(name, call):
        remaining()
        start = instant()
        value = perform(call)
        return {
            "data": list(value.records[:MAX_RECORDS]), "complete": value.complete,
            "errors": [] if value.complete else [name + "_incomplete"],
            "observed_from": start, "observed_to": instant(),
        }

    first = metadata()
    project_id, repository_id = _pr_scope(first["data"], number)
    if principal is None:
        raise ObservationCredentialError("ADO review credential principal was not observed")
    sections = {"pull_request": first}
    initial_iterations = collection("iterations", reader.iterations)
    rows = initial_iterations["data"]
    if any(type(row.get("id")) is not int or row["id"] <= 0 for row in rows):
        raise ProviderError("ADO review iterations have invalid identity")
    latest = max(rows, key=lambda row: row["id"]) if rows and initial_iterations["complete"] else {}
    head = (latest.get("sourceRefCommit") or {}).get("commitId")
    common = (latest.get("commonRefCommit") or {}).get("commitId")
    omissions = {}
    if latest and isinstance(head, str) and head and isinstance(common, str) and common:
        changes = collection("changes", lambda: reader.iteration_changes(latest["id"]))
        changes["revision"] = {"head_sha": head, "iteration": latest["id"], "common_sha": common}
        sections["changes"] = changes
    else:
        omissions["changes"] = "current_range_unavailable"
    sections["discussions"] = collection("discussions", reader.threads)
    sections["policies"] = collection("policies", lambda: reader.evaluations(project_id, number))
    try:
        final = metadata()
    except ObservationCredentialError:
        raise
    except ProviderError:
        final = {**first, "complete": False, "errors": ["final_metadata_unavailable"]}
    if _pr_scope(final["data"], number) != (project_id, repository_id):
        raise ProviderError("ADO review repository identity changed during acquisition")
    sections["pull_request"] = final
    final_iterations = collection("iterations", reader.iterations)
    if any(type(row.get("id")) is not int or row["id"] <= 0 for row in final_iterations["data"]):
        raise ProviderError("ADO review iterations have invalid identity")
    if not final_iterations["complete"]:
        known = {row["id"]: row for row in initial_iterations["data"]}
        for row in final_iterations["data"]:
            if type(row.get("id")) is not int or row["id"] <= 0:
                raise ProviderError("ADO review iterations have invalid identity")
            known[row["id"]] = row
        final_iterations["data"] = list(known.values())
        final_iterations["observed_from"] = initial_iterations["observed_from"]
    sections["iterations"] = final_iterations
    reviewers = final["data"].get("reviewers")
    supplied = isinstance(reviewers, list) and all(isinstance(row, dict) for row in reviewers)
    sections["reviewers"] = {
        "data": reviewers if supplied else [],
        "complete": supplied and final["complete"],
        "errors": [] if supplied and final["complete"] else ["reviewers_unavailable"],
        "observed_from": final["observed_from"], "observed_to": final["observed_to"],
    }
    remaining()
    result = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": {
            "provider": "azure-devops", "endpoint": endpoint, "project_id": project_id,
            "repository_id": repository_id, "principal_id": principal, "number": number,
        },
        "sections": sections,
        "acquisition": {
            "source": "configured_native_provider", "effects": "none",
            "observed_from": observed_from, "observed_to": instant(),
            "duration_ms": round((clock() - started) * 1000, 3),
            "principal_source": "provider_connection_data",
            "credential_identity_checks": identity_checks, "omissions": omissions,
            "snapshot_atomic": False,
        },
    }
    if len(json.dumps(result, allow_nan=False).encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ProviderError("ADO review evidence exceeds its byte budget")
    return result
