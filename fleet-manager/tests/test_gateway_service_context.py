"""Strict profile schema, complete revision ownership and coherent publication."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import threading
from typing import Any

from fleet_manager import planning
from fleet_manager.gateway_service_policy import ServiceAuthority, ServicePolicyError
import pytest

from test_gateway_service_policy import Harness, harness as harness
from test_runtime_identity import NOW


@pytest.mark.parametrize("field", [
    "schema", "version", "manifest", "identity", "bindings", "callers",
])
def test_all_root_fields_required(harness: Harness, field: str) -> None:
    del harness.document[field]
    with pytest.raises(ServicePolicyError, match="^invalid-fields$"):
        harness.owner.reload()


@pytest.mark.parametrize("location", ["root", "identity", "binding", "caller", "key"])
def test_no_unknown_fields_or_private_key_material(harness: Harness, location: str) -> None:
    target = {
        "root": harness.document, "identity": harness.document["identity"],
        "binding": harness.document["bindings"][0], "caller": harness.document["callers"][0],
        "key": harness.document["bindings"][0]["jwk"],
    }[location]
    target["d" if location == "key" else "secret"] = "synthetic-private-never-echo"
    with pytest.raises(ServicePolicyError) as caught:
        harness.owner.reload()
    assert "synthetic-private-never-echo" not in str(caught.value)
    assert "synthetic-private-never-echo" not in repr(harness.owner.status())


@pytest.mark.parametrize("change", [
    "missing", "extra", "duplicate", "unsupported", "reference",
    "private", "off-curve", "noncanonical", "bad-kid", "wrong-curve", "wrong-type",
])
def test_binding_resolution_and_real_public_point_validation(harness: Harness, change: str) -> None:
    bindings = harness.document["bindings"]
    if change == "missing":
        bindings.pop()
    elif change == "extra":
        bindings.append({**bindings[0], "reference": "unused"})
    elif change == "duplicate":
        bindings.append(copy.deepcopy(bindings[0]))
    elif change == "unsupported":
        bindings[0]["provider"] = "unknown"
    elif change == "reference":
        bindings[0]["reference"] = "https://untrusted.example/key"
    elif change == "private":
        bindings[0]["jwk"] = harness.keys[1].as_dict(private=True)
    elif change == "off-curve":
        bindings[0]["jwk"]["x"] = "A" * 43
        bindings[0]["jwk"]["y"] = "A" * 43
    elif change == "noncanonical":
        bindings[0]["jwk"]["x"] = "!" * 43
    elif change == "bad-kid":
        bindings[0]["jwk"]["kid"] = "\n"
    elif change == "wrong-curve":
        bindings[0]["jwk"]["crv"] = "P-384"
    else:
        bindings[0]["jwk"] = []
    with pytest.raises(ServicePolicyError):
        harness.owner.reload()


@pytest.mark.parametrize(("field", "value"), [
    ("principal", "bad/principal"), ("principal", "x" * 129), ("principal", ""),
    ("key_source", "missing"), ("slots", []), ("slots", ["service-a", "service-a"]),
    ("slots", ["missing"]), ("slots", "service-a"),
    ("methods", []), ("methods", ["GET", "GET"]), ("methods", ["TRACE"]),
    ("methods", ["get"]), ("methods", None),
])
def test_strict_caller_scope_schema(harness: Harness, field: str, value: object) -> None:
    harness.document["callers"][0][field] = value
    with pytest.raises(ServicePolicyError):
        harness.owner.reload()


@pytest.mark.parametrize("collection", ["bindings", "callers"])
def test_collection_bounds(harness: Harness, collection: str) -> None:
    harness.document[collection] = [harness.document[collection][0]] * 129
    with pytest.raises(ServicePolicyError, match="^collection-limit$"):
        harness.owner.reload()


def test_duplicate_callers_and_undeclared_unused_source(harness: Harness) -> None:
    harness.document["callers"] *= 2
    with pytest.raises(ServicePolicyError, match="^duplicate-caller$"):
        harness.owner.reload()
    harness.document["callers"].pop()
    harness.document["manifest"]["key_sources"].append(
        {"name": "unused", "provider": "owner-store", "reference": "keys/unused"})
    with pytest.raises(ServicePolicyError, match="^unresolved-key-source$"):
        harness.owner.reload()


@pytest.mark.parametrize(("field", "value"), [
    ("issuer", "http://issuer.example"), ("audience", ""),
    ("trust_valid_until", True), ("trust_valid_until", NOW),
    ("jwks", {"keys": []}), ("jwks", {"keys": [{"kty": "oct", "k": "private"}]}),
])
def test_existing_verifier_trust_constraints_reused(
    harness: Harness, field: str, value: object,
) -> None:
    harness.document["identity"][field] = value
    with pytest.raises(ServicePolicyError):
        harness.owner.reload()


@pytest.mark.parametrize("raw", [
    b'{"schema":1,"schema":2}', b'{"value":NaN}', b"not-json",
    b'{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":{"i":{"j":{"k":{"l":{"m":0}}}}}}}}}}}}}',
    b"x" * (planning.MAX_BYTES + 1),
], ids=["duplicate", "nonfinite", "malformed", "deep", "oversized"])
def test_strict_bounded_json(harness: Harness, raw: bytes) -> None:
    harness.raw = raw
    with pytest.raises(ServicePolicyError, match="^invalid-json$"):
        harness.owner.reload()


def test_normalized_order_noop_complete_digest_and_no_mutable_aliases(harness: Harness) -> None:
    first = harness.owner.reload()
    context = first.snapshot.context
    original = context.digest
    harness.document["manifest"]["roles"].reverse()
    harness.document["manifest"]["slots"].reverse()
    harness.document["manifest"]["key_sources"].reverse()
    harness.document["bindings"].reverse()
    harness.document["callers"][0]["methods"].reverse()
    result = harness.owner.reload()
    assert not result.changed and result.snapshot is first.snapshot
    with pytest.raises(FrozenInstanceError):
        context.digest = "forged"
    harness.document["identity"]["audience"] = "changed"
    assert context.digest == original
    assert context.manifest.slots[0].principal == "service-a"
    with pytest.raises(ServicePolicyError, match="^revision-conflict$"):
        harness.owner.reload()
    assert harness.owner.status().revision == 1


@pytest.mark.parametrize("change", ["identity", "binding", "caller"])
def test_same_revision_covers_entire_profile(harness: Harness, change: str) -> None:
    harness.owner.reload()
    if change == "identity":
        harness.document["identity"]["trust_valid_until"] += 1
    elif change == "binding":
        harness.document["bindings"][0]["jwk"] = harness.keys[2].as_dict(private=False)
    else:
        harness.document["callers"][0]["methods"] = ["GET"]
    with pytest.raises(ServicePolicyError, match="^revision-conflict$"):
        harness.owner.reload()


def test_invalid_source_owner_and_stale_candidate_preserve_known_good(harness: Harness) -> None:
    harness.owner.reload()
    grant = harness.service()
    nonce = harness.owner.challenge()
    original = copy.deepcopy(harness.document)
    for owner in ("fleet", "gateway"):
        harness.document["manifest"][owner] = "wrong"
        with pytest.raises(ServicePolicyError, match="^owner-mismatch$"):
            harness.owner.reload()
        harness.document = copy.deepcopy(original)
    harness.failure = OSError("synthetic-path-private")
    with pytest.raises(ServicePolicyError, match="^source-unavailable$"):
        harness.owner.reload()
    assert harness.owner.status().last_error == "source-unavailable"
    assert "synthetic-path-private" not in repr(harness.owner.status())
    harness.failure = RuntimeError("source-programming-error")
    with pytest.raises(RuntimeError, match="^source-programming-error$"):
        harness.owner.reload()
    harness.failure = None
    harness.raw = b"invalid"
    with pytest.raises(ServicePolicyError, match="^invalid-json$"):
        harness.owner.reload()
    harness.raw = None
    harness.update()
    harness.document["manifest"]["revision"] = 1
    with pytest.raises(ServicePolicyError, match="^stale-revision$"):
        harness.owner.reload()
    assert harness.owner.status().revision == 2
    assert harness.owner.challenge() == nonce
    assert harness.owner.revalidate_service(grant).name == "service-a"


def test_concurrent_publication_rechecks_latest_revision_and_no_source_lock(harness: Harness) -> None:
    harness.owner.reload()
    old = copy.deepcopy(harness.document)
    old["manifest"]["revision"] = 2
    old["manifest"]["slots"][0]["principal"] = "revision-two"
    new = copy.deepcopy(old)
    new["manifest"]["revision"] = 3
    new["manifest"]["slots"][0]["principal"] = "revision-three"
    entered, resume = threading.Event(), threading.Event()

    def source() -> bytes:
        if threading.current_thread().name.startswith("older"):
            entered.set()
            assert resume.wait(5)
            return planning.canonical(old)
        return planning.canonical(new)

    authority = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                                 source=source, clock=harness.clock)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="older") as pool:
        pending = pool.submit(authority.reload)
        assert entered.wait(5)
        try:
            published = authority.reload()
            assert published.revision == 3
            assert authority.status().revision == 3
        finally:
            resume.set()
        with pytest.raises(ServicePolicyError, match="^stale-revision$"):
            pending.result(timeout=5)
    assert authority.status().generation == 1


def test_concurrent_same_revision_conflict_is_not_mixed(harness: Harness) -> None:
    barrier = threading.Barrier(2)
    documents = [copy.deepcopy(harness.document) for _ in range(2)]
    documents[1]["callers"] = []
    local = threading.local()

    def source() -> bytes:
        raw = planning.canonical(documents[local.index])
        barrier.wait(timeout=5)
        return raw

    authority = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                                 source=source, clock=harness.clock)

    def publish(index: int) -> Any:
        local.index = index
        try:
            return authority.reload()
        except ServicePolicyError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, range(2)))
    assert results.count("revision-conflict") == 1
    publication = next(item for item in results if not isinstance(item, str))
    assert publication.changed and publication.generation == 1
    assert len(publication.snapshot.context.callers) in (0, 1)
    assert authority.status().generation == 1


def test_all_unordered_profile_lists_normalize_without_changing_verifier(harness: Harness) -> None:
    harness.document["identity"]["jwks"]["keys"].append(
        {**harness.keys[2].as_dict(private=False), "kid": "issuer-2"})
    harness.document["callers"][0]["slots"].append("service-b")
    harness.document["callers"].append({
        "principal": "caller-b", "key_source": "service-b",
        "slots": ["service-b"], "methods": ["GET"],
    })
    first = harness.owner.reload()
    nonce = harness.owner.challenge()
    harness.document["identity"]["jwks"]["keys"].reverse()
    harness.document["callers"][0]["slots"].reverse()
    harness.document["callers"].reverse()
    for slot in harness.document["manifest"]["slots"]:
        slot["routes"][0]["methods"].reverse()
    result = harness.owner.reload()
    assert not result.changed and result.snapshot is first.snapshot
    assert harness.owner.challenge() == nonce


def test_constructor_does_not_sample_clock_or_source() -> None:
    def unavailable() -> Any:
        raise AssertionError("constructor sampled an explicit owner input")

    authority = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                                 source=unavailable, clock=unavailable)
    assert authority.status().revision is None


def test_exact_declared_profile_capacity_is_accepted(harness: Harness) -> None:
    manifest = harness.document["manifest"]
    manifest["key_sources"] = [
        {"name": f"source-{index}", "provider": "owner-store",
         "reference": f"keys/source-{index}"} for index in range(128)
    ]
    public = harness.keys[1].as_dict(private=False)
    harness.document["bindings"] = [
        {"provider": "owner-store", "reference": item["reference"], "jwk": public}
        for item in manifest["key_sources"]
    ]
    manifest["slots"] = [
        {"name": f"slot-{index}", "role": "service", "principal": "service-a",
         "key_source": "source-0",
         "routes": [{"prefix": f"/service-{index}", "methods": ["GET"],
                     "capabilities": ["http"]}]} for index in range(64)
    ]
    harness.document["callers"] = [
        {"principal": f"caller-{index}", "key_source": "source-0",
         "slots": [slot["name"] for slot in manifest["slots"]], "methods": ["GET"]}
        for index in range(128)
    ]
    context = harness.owner.reload().snapshot.context
    assert len(context.keys) == len(context.callers) == 128
    assert len(context.manifest.slots) == len(context.callers[0].slots) == 64


def test_unrelated_caller_and_slot_policy_preserves_current_caller_handle(harness: Harness) -> None:
    harness.owner.reload()
    grant = harness.caller()
    harness.document["callers"].append({
        "principal": "caller-b", "key_source": "service-b",
        "slots": ["service-b"], "methods": ["GET"],
    })
    harness.document["manifest"]["slots"][1]["configuration_ref"] = "config/changed"
    harness.update()
    assert harness.owner.revalidate_caller(grant).slot.name == "service-a"
    harness.document["callers"][1]["methods"] = ["POST"]
    harness.update()
    assert harness.owner.revalidate_caller(grant).method == "GET"
