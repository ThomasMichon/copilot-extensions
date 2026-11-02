from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import TYPE_CHECKING, TypeVar, TypedDict

if TYPE_CHECKING:
    from typing_extensions import Unpack

import pytest

from fleet_contracts import (
    ConnectorRegistration,
    ContractError,
    CredentialParameters,
    CredentialResult,
    DriverSnapshot,
    DriverTarget,
    HealthResult,
    RouteRequest,
    RouteResponse,
    SearchHit,
    SearchParameters,
    ServiceOffer,
    TargetRef,
    decode_json,
    encode_json,
)
from fleet_contracts.records import (
    MAX_CONTENT_BYTES,
    MAX_CREDENTIAL_FIELDS,
    MAX_CREDENTIAL_VALUE_BYTES,
    MAX_HITS,
    MAX_MESSAGE_BYTES,
    MAX_QUERY_BYTES,
    MAX_SERVICES,
    MAX_TARGETS,
)

_T = TypeVar("_T")
WireRecord = ConnectorRegistration | RouteRequest | DriverSnapshot | RouteResponse
WireReader = (
    type[ConnectorRegistration] | type[RouteRequest] | type[DriverSnapshot] | type[RouteResponse]
)


class HitArguments(TypedDict, total=False):
    chunk_id: str
    score: float
    file_path: str
    source: str
    chunk_type: str
    language: str
    content: str
    line_start: int | None
    line_end: int | None


class ResponseArguments(TypedDict, total=False):
    request_id: str
    service_id: str
    installation_id: str
    operation: str
    health: HealthResult | None
    hits: tuple[SearchHit, ...] | None
    credential: CredentialResult | None


class RequestArguments(TypedDict, total=False):
    fleet_id: str
    target: TargetRef
    connector_id: str
    generation: int
    service_id: str
    installation_id: str
    request_id: str
    operation: str
    parameters: SearchParameters | CredentialParameters | None


def _invalid_call(function: Callable[..., _T], /, *args: object, **kwargs: object) -> _T:
    """Exercise rejected runtime inputs without weakening positive call signatures."""
    return function(*args, **kwargs)


def registration() -> ConnectorRegistration:
    return ConnectorRegistration(
        "lab", TargetRef("static-ssh", "ssh-a", "worker-a"), "connector-a", 4, 1500,
        (ServiceOffer("index", "index-install-a", "index-v1", ("health", "index.search")),),
    )


def request() -> RouteRequest:
    reg = registration()
    return RouteRequest(
        reg.fleet_id, reg.target, reg.connector_id, reg.generation, "index", "index-install-a",
        "request-a", "index.search", SearchParameters("a query", source="docs", repo="owner/repo"),
    )


@pytest.mark.parametrize("record,reader", [
    (registration(), ConnectorRegistration),
    (request(), RouteRequest),
    (DriverSnapshot("static-ssh", "ssh-a", "sha256:" + "0" * 64,
                    (DriverTarget("worker-a", "configured", ("ssh",)),)), DriverSnapshot),
])
def test_wire_roundtrip(record: WireRecord, reader: WireReader) -> None:
    encoded = encode_json(record.to_dict())
    assert reader.from_dict(decode_json(encoded)) == record
    assert len(encoded) < MAX_MESSAGE_BYTES


@pytest.mark.parametrize("record,reader", [
    (registration(), ConnectorRegistration), (request(), RouteRequest),
    (DriverSnapshot("static-ssh", "ssh-a", "sha256:" + "0" * 64,
                    (DriverTarget("worker-a", "configured", ("ssh",)),)), DriverSnapshot),
])
@pytest.mark.parametrize("mutation", ["unknown", "missing", "version", "bool-version", "schema"])
def test_wire_rejects_unknown_or_incompatible_record(
    record: WireRecord, reader: WireReader, mutation: str,
) -> None:
    data = record.to_dict()
    if mutation == "unknown":
        data["executable"] = "never-logged-secret"
    elif mutation == "missing":
        del data["schema"]
    elif mutation == "version":
        data["version"] = 2
    elif mutation == "bool-version":
        data["version"] = True
    else:
        data["schema"] = "other"
    with pytest.raises(ContractError) as error:
        reader.from_dict(data)
    assert "never-logged-secret" not in str(error.value)


@pytest.mark.parametrize("raw", [
    b'{"version":1,"version":2}', b'{"nested":{"x":0,"x":1}}',
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":1e999}',
    b'[]', b'\xff', b'{"broken":', b'{' + b'"x":' + b'[' * 20 + b'0' + b']' * 20 + b'}',
    b" " * (MAX_MESSAGE_BYTES + 1),
], ids=[
    "duplicate", "nested-duplicate", "nan", "infinity", "exponent-overflow", "array",
    "invalid-utf8", "truncated", "deep", "oversized",
])
def test_untrusted_json_rejects_ambiguity_and_bounds(raw: bytes) -> None:
    with pytest.raises(ContractError):
        decode_json(raw)


@pytest.mark.parametrize("value", [
    {"x": float("inf")}, {"x": float("nan")}, {"x": "\ud800"},
    {"x": "a" * MAX_MESSAGE_BYTES}, {0: "non-string-key"}, [],
], ids=["infinity", "nan", "surrogate", "oversized", "non-string-key", "array"])
def test_encoding_cannot_emit_unbounded_or_invalid_json(value: object) -> None:
    with pytest.raises(ContractError):
        _invalid_call(encode_json, value)


def test_json_byte_boundary_is_exact() -> None:
    raw = b'{"x":"' + b"a" * (MAX_MESSAGE_BYTES - len(b'{"x":""}')) + b'"}'
    assert len(raw) == MAX_MESSAGE_BYTES
    assert encode_json(decode_json(raw)) == raw
    with pytest.raises(ContractError):
        decode_json(raw + b" ")


@pytest.mark.parametrize("bad", ["", " ", "-option", "*", "a/b", "a\nb", "a" * 129, 1, None])
def test_qualified_identity_is_bounded(bad: object) -> None:
    with pytest.raises(ContractError):
        _invalid_call(TargetRef, "static-ssh", "ssh-a", bad)


@pytest.mark.parametrize("field", ["generation", "expires_at"])
@pytest.mark.parametrize("bad", [True, False, 0, -1, "4", 1.5, 2**63])
def test_registration_rejects_non_integer_and_out_of_range_fences(field: str, bad: object) -> None:
    with pytest.raises(ContractError):
        _invalid_call(replace, registration(), **{field: bad})


def test_expiry_and_lifetime_boundaries() -> None:
    reg = registration()
    reg.check_live(1499)
    reg.check_live(900)
    for now in (899, 1500, 1501):
        with pytest.raises(ContractError):
            reg.check_live(now)
    for ttl in (0, True, 3601):
        with pytest.raises(ContractError):
            reg.check_live(1499, max_ttl=ttl)


def test_offers_and_targets_have_unique_immutable_identity() -> None:
    reg = registration()
    with pytest.raises(ContractError):
        replace(reg, services=(reg.services[0], reg.services[0]))
    with pytest.raises(ContractError):
        _invalid_call(replace, reg, services=list(reg.services))
    with pytest.raises(FrozenInstanceError):
        setattr(reg, "generation", 9)
    with pytest.raises(ContractError):
        DriverSnapshot("static-ssh", "ssh-a", "sha256:" + "0" * 64,
                       tuple(DriverTarget(f"target-{i}", "configured", ("ssh",))
                             for i in range(MAX_TARGETS + 1)))
    with pytest.raises(ContractError):
        replace(reg, services=tuple(
            ServiceOffer(f"svc-{i}", "install", "health-v1", ("health",))
            for i in range(MAX_SERVICES + 1)
        ))


@pytest.mark.parametrize("offer", [
    ("health-v1", ("index.search",)), ("index-v1", ("execute",)),
    ("index-v1", ("health", "health")), ("unknown", ("health",)), ("index-v1", ()),
])
def test_adapters_cannot_offer_generic_execution_or_unknown_operations(
    offer: tuple[str, tuple[str, ...]],
) -> None:
    with pytest.raises(ContractError):
        ServiceOffer("svc", "install", *offer)


@pytest.mark.parametrize("change", [
    {"q": ""}, {"q": " "}, {"q": "a\0b"}, {"q": "a" * (MAX_QUERY_BYTES + 1)},
    {"limit": True}, {"limit": 0}, {"limit": 101}, {"limit": 1.5},
    {"source": "two\nvalues"}, {"source": ""}, {"repo": "x" * 257},
])
def test_search_parameters_are_typed_and_bounded(change: dict[str, object]) -> None:
    data = {"q": "query", **change}
    with pytest.raises(ContractError):
        SearchParameters.from_dict(data)


def test_multibyte_query_bound_and_existing_index_filters() -> None:
    parameters = SearchParameters("x" * MAX_QUERY_BYTES, 100, "docs", "python", "owner/repo")
    assert parameters.limit == 100
    assert SearchParameters("query", source="Notes and docs").source == "Notes and docs"
    assert SearchParameters("\u00e9" * (MAX_QUERY_BYTES // 2)).q
    with pytest.raises(ContractError):
        SearchParameters("\u00e9" * (MAX_QUERY_BYTES // 2 + 1))
    for injected in ("url", "headers", "path", "method", "shell", "mode"):
        with pytest.raises(ContractError):
            SearchParameters.from_dict({"q": "query", injected: "never-logged-secret"})


@pytest.mark.parametrize("change", [
    {"fleet_id": "other"}, {"connector_id": "other"}, {"generation": 3},
    {"target": TargetRef("static-ssh", "ssh-b", "worker-a")},
    {"target": TargetRef("static-ssh", "ssh-a", "worker-b")},
    {"service_id": "other"}, {"installation_id": "other"},
])
def test_route_is_bound_to_current_offer_not_just_target_name(change: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        _invalid_call(replace, request(), **change).check_offer(registration(), 1499)


def test_operation_binding_and_health_parameters() -> None:
    reg = registration()
    assert request().check_offer(reg, 1499) is reg.services[0]
    health = replace(request(), operation="health", parameters=None)
    assert health.check_offer(reg, 1499) is reg.services[0]
    with pytest.raises(ContractError):
        replace(health, parameters=SearchParameters("not-health"))
    with pytest.raises(ContractError):
        replace(request(), operation="execute")
    with pytest.raises(ContractError):
        replace(request(), parameters=None)
    restricted = replace(reg, services=(
        ServiceOffer("index", "index-install-a", "health-v1", ("health",)),
    ))
    with pytest.raises(ContractError):
        request().check_offer(restricted, 1499)
    with pytest.raises(ContractError):
        request().check_offer(reg, reg.expires_at)
    longer = replace(reg, expires_at=3000)
    with pytest.raises(ContractError):
        request().check_offer(longer, 1499)
    assert request().check_offer(longer, 1499, max_ttl=3600) is longer.services[0]


def test_native_installation_identity_is_opaque_not_a_logical_name_or_path() -> None:
    native_id = "marketplace-identity/agent-index"
    reg = registration()
    offer = replace(reg.services[0], installation_id=native_id)
    reg = replace(reg, services=(offer,))
    routed = replace(request(), installation_id=native_id)
    assert routed.check_offer(reg, 1499) is offer
    assert RouteRequest.from_dict(decode_json(encode_json(routed.to_dict()))) == routed
    for invalid in ("", " ", "a\nb", "a\0b", "a" * 257):
        with pytest.raises(ContractError):
            replace(offer, installation_id=invalid)
        with pytest.raises(ContractError):
            replace(routed, installation_id=invalid)


def test_driver_states_do_not_imply_live_health_or_managed_capabilities() -> None:
    target = DriverTarget("worker-a", "configured", ("ssh",))
    assert target.reason is None
    DriverTarget("worker-a", "unavailable", (), "provider-unavailable")
    for change in (
        {"state": "ready"}, {"capabilities": ("unknown",)},
        {"capabilities": ("ssh", "ssh")}, {"reason": "provider-unavailable"},
        {"state": "unavailable"}, {"capabilities": ["ssh"]},
    ):
        with pytest.raises(ContractError):
            _invalid_call(replace, target, **change)


def hit(**overrides: Unpack[HitArguments]) -> SearchHit:
    base: HitArguments = {
        "chunk_id": "chunk-1", "score": 0.5, "file_path": "docs/example.md", "source": "git:repo",
        "chunk_type": "markdown", "language": "markdown", "content": "example content",
        "line_start": 10, "line_end": 12,
    }
    base.update(overrides)
    return SearchHit(**base)


def search_response(**overrides: Unpack[ResponseArguments]) -> RouteResponse:
    base: ResponseArguments = {
        "request_id": "request-a", "service_id": "index", "installation_id": "index-install-a",
        "operation": "index.search", "hits": (hit(),),
    }
    base.update(overrides)
    return RouteResponse(**base)


def health_response(**overrides: Unpack[ResponseArguments]) -> RouteResponse:
    base: ResponseArguments = {
        "request_id": "request-a", "service_id": "index", "installation_id": "index-install-a",
        "operation": "health", "health": HealthResult(True, "ok"),
    }
    base.update(overrides)
    return RouteResponse(**base)


@pytest.mark.parametrize("record,reader", [
    (search_response(), RouteResponse), (health_response(), RouteResponse),
    (search_response(hits=()), RouteResponse),
])
def test_response_wire_roundtrip(record: RouteResponse, reader: type[RouteResponse]) -> None:
    encoded = encode_json(record.to_dict())
    assert reader.from_dict(decode_json(encoded)) == record
    assert len(encoded) < MAX_MESSAGE_BYTES


@pytest.mark.parametrize("mutation", ["unknown", "missing", "version", "bool-version", "schema"])
def test_response_wire_rejects_unknown_or_incompatible_record(mutation: str) -> None:
    data = search_response().to_dict()
    if mutation == "unknown":
        data["executable"] = "never-logged-secret"
    elif mutation == "missing":
        del data["schema"]
    elif mutation == "version":
        data["version"] = 2
    elif mutation == "bool-version":
        data["version"] = True
    else:
        data["schema"] = "other"
    with pytest.raises(ContractError) as error:
        RouteResponse.from_dict(data)
    assert "never-logged-secret" not in str(error.value)


def test_route_response_for_request_echoes_originating_identity() -> None:
    req = request()
    response = RouteResponse.for_request(req, hits=(hit(),))
    assert response.request_id == req.request_id
    assert response.service_id == req.service_id
    assert response.installation_id == req.installation_id
    assert response.operation == req.operation
    health_req = replace(req, operation="health", parameters=None)
    health = RouteResponse.for_request(health_req, health=HealthResult(True))
    assert health.operation == "health"


@pytest.mark.parametrize("identity", [
    "example-marketplace/agent-index", "\u7814\u7a76/\u7d22\u5f15", "i" * 256,
], ids=["native-cell", "opaque-utf8", "byte-boundary"])
@pytest.mark.parametrize("operation", ["health", "index.search"])
def test_native_opaque_identity_is_preserved_through_routed_response(
    identity: str, operation: str,
) -> None:
    req = replace(
        request(), installation_id=identity, operation=operation,
        parameters=None if operation == "health" else SearchParameters("query"),
    )
    result = (
        RouteResponse.for_request(req, health=HealthResult(True))
        if operation == "health" else RouteResponse.for_request(req, hits=(hit(),))
    )
    assert result.installation_id == identity
    assert RouteResponse.from_dict(decode_json(encode_json(result.to_dict()))) == result


@pytest.mark.parametrize("identity", ["", "\n", "\x80", "i" * 257, "\u00e9" * 129, None],
                         ids=["empty", "c0", "c1", "long-ascii", "long-utf8", "wrong-type"])
def test_routed_response_rejects_invalid_installation_identity(identity: object) -> None:
    with pytest.raises(ContractError):
        _invalid_call(health_response, installation_id=identity)


@pytest.mark.parametrize("change", [
    {"operation": "execute"},
    {"operation": "health", "hits": (hit(),), "health": None},
    {"operation": "health", "health": None},
    {"operation": "index.search", "hits": None, "health": HealthResult(True)},
    {"operation": "index.search", "health": HealthResult(True)},
    {"operation": "index.search", "hits": [hit()]},
])
def test_route_response_operation_shape_is_exclusive(change: dict[str, object]) -> None:
    base: dict[str, object] = {
        "request_id": "request-a", "service_id": "index", "installation_id": "index-install-a",
        "operation": "index.search", "hits": (hit(),),
    }
    base.update(change)
    with pytest.raises(ContractError):
        _invalid_call(RouteResponse, **base)


def test_route_response_bounds_hit_count() -> None:
    with pytest.raises(ContractError):
        search_response(hits=tuple(hit(chunk_id=f"c-{i}") for i in range(MAX_HITS + 1)))
    search_response(hits=tuple(hit(chunk_id=f"c-{i}") for i in range(MAX_HITS)))


def test_route_response_bounds_aggregate_wire_size_even_when_every_field_is_valid() -> None:
    # Every individual hit, and the hit count, independently satisfy their own
    # bounds (MAX_HITS, MAX_CONTENT_BYTES) -- but MAX_HITS * MAX_CONTENT_BYTES
    # vastly exceeds MAX_MESSAGE_BYTES, so the aggregate wire-size check must
    # catch what the per-field/count checks above cannot.
    oversized = tuple(
        hit(chunk_id=f"c-{i}", content="x" * MAX_CONTENT_BYTES) for i in range(MAX_HITS)
    )
    with pytest.raises(ContractError):
        search_response(hits=oversized)


@pytest.mark.parametrize("change", [
    {"chunk_id": ""}, {"chunk_id": 1},
    {"score": float("nan")}, {"score": float("inf")}, {"score": "0.5"}, {"score": True},
    {"file_path": 1}, {"source": None},
    {"content": "x" * (MAX_CONTENT_BYTES + 1)},
    {"line_start": -1}, {"line_start": 1.5}, {"line_end": True},
])
def test_search_hit_is_typed_and_bounded(change: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        _invalid_call(hit, **change)


def test_search_hit_allows_blank_body_fields_but_not_blank_identity() -> None:
    allowed = hit(file_path="", source="", chunk_type="", language="", content="",
                  line_start=None, line_end=None)
    assert allowed.content == ""
    assert allowed.line_start is None


@pytest.mark.parametrize("change", [
    {"available": "yes"}, {"available": 1}, {"detail": 1},
    {"detail": "x" * 513},
])
def test_health_result_is_typed_and_bounded(change: dict[str, object]) -> None:
    base: dict[str, object] = {"available": True, "detail": "ok"}
    base.update(change)
    with pytest.raises(ContractError):
        _invalid_call(HealthResult, **base)


def test_health_result_allows_blank_or_missing_detail() -> None:
    assert HealthResult(True).detail is None
    assert HealthResult(False, "").detail == ""


# --- vault-v1 (CredentialParameters / CredentialResult / credential.get) ---

def vault_registration(operations: tuple[str, ...] = ("health", "credential.get")) -> ConnectorRegistration:
    return ConnectorRegistration(
        "lab", TargetRef("static-ssh", "ssh-a", "worker-a"), "connector-a", 4, 1500,
        (ServiceOffer("vault", "vault-install-a", "vault-v1", operations),),
    )


def credential_request(**overrides: Unpack[RequestArguments]) -> RouteRequest:
    reg = vault_registration()
    base: RequestArguments = {
        "fleet_id": reg.fleet_id, "target": reg.target, "connector_id": reg.connector_id,
        "generation": reg.generation, "service_id": "vault", "installation_id": "vault-install-a",
        "request_id": "request-a", "operation": "credential.get",
        "parameters": CredentialParameters("get-azure-token", resource="https://management.azure.com/"),
    }
    base.update(overrides)
    return RouteRequest(**base)


def credential_response(**overrides: Unpack[ResponseArguments]) -> RouteResponse:
    base: ResponseArguments = {
        "request_id": "request-a", "service_id": "vault", "installation_id": "vault-install-a",
        "operation": "credential.get",
        "credential": CredentialResult(True, (("token", "secret-token"), ("expires_on", "1700000000"))),
    }
    base.update(overrides)
    return RouteResponse(**base)


@pytest.mark.parametrize("change", [
    {"action": "get"}, {"action": "store"}, {"action": "capabilities"}, {"action": ""},
    {"action": 1}, {"host": "two\nvalues"}, {"host": ""}, {"resource": "x" * 257},
])
def test_credential_parameters_are_typed_and_bounded(change: dict[str, object]) -> None:
    base: dict[str, object] = {
        "action": "get-azure-token", "resource": "https://management.azure.com/",
    }
    base.update(change)
    with pytest.raises(ContractError):
        CredentialParameters.from_dict(base)


def test_credential_parameters_cover_known_dedicated_actions() -> None:
    CredentialParameters("get-azure-token", resource="https://management.azure.com/", tenant="t")
    CredentialParameters("get-github-token", host="github.com")
    CredentialParameters("get-access-token", host="dev.azure.com")
    assert CredentialParameters("get-azure-token").host is None


def test_route_request_accepts_credential_get_bound_to_vault_offer() -> None:
    request = credential_request()
    offer = request.check_offer(vault_registration(), 1499)
    assert offer.adapter == "vault-v1"
    # The vault offer doesn't support index.search -- check_offer (not
    # construction) is what must reject a mismatched operation/offer pairing.
    mismatched = replace(
        request, service_id="index", installation_id="index-install-a",
        operation="index.search", parameters=SearchParameters("q"),
    )
    with pytest.raises(ContractError):
        mismatched.check_offer(vault_registration(), 1499)
    with pytest.raises(ContractError):
        replace(request, parameters=None)


@pytest.mark.parametrize("record,reader", [
    (credential_request(), RouteRequest),
    (credential_response(), RouteResponse),
])
def test_credential_wire_roundtrip(
    record: RouteRequest | RouteResponse, reader: type[RouteRequest] | type[RouteResponse],
) -> None:
    encoded = encode_json(record.to_dict())
    assert reader.from_dict(decode_json(encoded)) == record
    assert len(encoded) < MAX_MESSAGE_BYTES


@pytest.mark.parametrize("change", [
    {"available": True, "fields": ()},  # available credential needs >=1 field
    {"available": False, "fields": (("token", "x"),)},  # unavailable cannot carry fields
    {"available": True, "fields": (("token", "x"), ("token", "y"))},  # duplicate field name
    {"available": True, "fields": (("password", "x"),)},  # unknown field name
    {"available": True, "fields": [("token", "x")]},  # mutable collection
    {"available": True, "fields": (("token", "x" * (MAX_CREDENTIAL_VALUE_BYTES + 1)),)},
    {"available": True, "fields": (("token", "x"),), "reason": "cache_miss"},  # available + reason
    {"available": False, "reason": "x" * 129},
    {"available": "yes", "fields": ()},
])
def test_credential_result_is_typed_and_bounded(change: dict[str, object]) -> None:
    base: dict[str, object] = {"available": True, "fields": (("token", "x"),), "reason": None}
    base.update(change)
    with pytest.raises(ContractError):
        _invalid_call(CredentialResult, **base)


def test_credential_result_bounds_field_count() -> None:
    too_many = tuple((f"field-{i}", "x") for i in range(MAX_CREDENTIAL_FIELDS + 1))
    with pytest.raises(ContractError):
        CredentialResult(True, tuple(("token", "x") for _ in too_many))


def test_credential_result_unavailable_carries_only_a_reason() -> None:
    denied = CredentialResult(False, reason="cache_miss")
    assert denied.available is False
    assert denied.fields == ()
    assert denied.reason == "cache_miss"


@pytest.mark.parametrize("change", [
    {"operation": "credential.get", "credential": None},
    {"operation": "credential.get", "hits": ()},
    {"operation": "credential.get", "health": HealthResult(True)},
    {"operation": "health", "credential": CredentialResult(False, reason="n/a"), "health": HealthResult(True)},
    {"operation": "index.search", "credential": CredentialResult(False, reason="n/a"), "hits": ()},
])
def test_route_response_credential_shape_is_exclusive(change: dict[str, object]) -> None:
    base: dict[str, object] = {
        "request_id": "request-a", "service_id": "vault", "installation_id": "vault-install-a",
        "operation": "credential.get",
        "credential": CredentialResult(True, (("token", "x"),)),
    }
    base.update(change)
    with pytest.raises(ContractError):
        _invalid_call(RouteResponse, **base)
