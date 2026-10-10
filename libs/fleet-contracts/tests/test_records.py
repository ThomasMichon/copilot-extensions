from dataclasses import FrozenInstanceError, replace

import pytest

from fleet_contracts import (
    ConnectorRegistration,
    ContractError,
    DriverSnapshot,
    DriverTarget,
    RouteRequest,
    SearchParameters,
    ServiceOffer,
    TargetRef,
    decode_json,
    encode_json,
)
from fleet_contracts.records import MAX_MESSAGE_BYTES, MAX_QUERY_BYTES, MAX_SERVICES, MAX_TARGETS


def registration():
    return ConnectorRegistration(
        "lab", TargetRef("static-ssh", "ssh-a", "worker-a"), "connector-a", 4, 1500,
        (ServiceOffer("index", "index-install-a", "index-v1", ("health", "index.search")),),
    )


def request():
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
def test_wire_roundtrip(record, reader):
    encoded = encode_json(record.to_dict())
    assert reader.from_dict(decode_json(encoded)) == record
    assert len(encoded) < MAX_MESSAGE_BYTES


@pytest.mark.parametrize("record,reader", [
    (registration(), ConnectorRegistration), (request(), RouteRequest),
    (DriverSnapshot("static-ssh", "ssh-a", "sha256:" + "0" * 64,
                    (DriverTarget("worker-a", "configured", ("ssh",)),)), DriverSnapshot),
])
@pytest.mark.parametrize("mutation", ["unknown", "missing", "version", "bool-version", "schema"])
def test_wire_rejects_unknown_or_incompatible_record(record, reader, mutation):
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
def test_untrusted_json_rejects_ambiguity_and_bounds(raw):
    with pytest.raises(ContractError):
        decode_json(raw)


@pytest.mark.parametrize("value", [
    {"x": float("inf")}, {"x": float("nan")}, {"x": "\ud800"},
    {"x": "a" * MAX_MESSAGE_BYTES}, {0: "non-string-key"}, [],
], ids=["infinity", "nan", "surrogate", "oversized", "non-string-key", "array"])
def test_encoding_cannot_emit_unbounded_or_invalid_json(value):
    with pytest.raises(ContractError):
        encode_json(value)


def test_json_byte_boundary_is_exact():
    raw = b'{"x":"' + b"a" * (MAX_MESSAGE_BYTES - len(b'{"x":""}')) + b'"}'
    assert len(raw) == MAX_MESSAGE_BYTES
    assert encode_json(decode_json(raw)) == raw
    with pytest.raises(ContractError):
        decode_json(raw + b" ")


@pytest.mark.parametrize("bad", ["", " ", "-option", "*", "a/b", "a\nb", "a" * 129, 1, None])
def test_qualified_identity_is_bounded(bad):
    with pytest.raises(ContractError):
        TargetRef("static-ssh", "ssh-a", bad)


@pytest.mark.parametrize("field", ["generation", "expires_at"])
@pytest.mark.parametrize("bad", [True, False, 0, -1, "4", 1.5, 2**63])
def test_registration_rejects_non_integer_and_out_of_range_fences(field, bad):
    with pytest.raises(ContractError):
        replace(registration(), **{field: bad})


def test_expiry_and_lifetime_boundaries():
    reg = registration()
    reg.check_live(1499)
    reg.check_live(900)
    for now in (899, 1500, 1501):
        with pytest.raises(ContractError):
            reg.check_live(now)
    for ttl in (0, True, 3601):
        with pytest.raises(ContractError):
            reg.check_live(1499, max_ttl=ttl)


def test_offers_and_targets_have_unique_immutable_identity():
    reg = registration()
    with pytest.raises(ContractError):
        replace(reg, services=(reg.services[0], reg.services[0]))
    with pytest.raises(ContractError):
        replace(reg, services=list(reg.services))
    with pytest.raises(FrozenInstanceError):
        reg.generation = 9
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
def test_adapters_cannot_offer_generic_execution_or_unknown_operations(offer):
    with pytest.raises(ContractError):
        ServiceOffer("svc", "install", *offer)


@pytest.mark.parametrize("change", [
    {"q": ""}, {"q": " "}, {"q": "a\0b"}, {"q": "a" * (MAX_QUERY_BYTES + 1)},
    {"limit": True}, {"limit": 0}, {"limit": 101}, {"limit": 1.5},
    {"source": "two\nvalues"}, {"source": ""}, {"repo": "x" * 257},
])
def test_search_parameters_are_typed_and_bounded(change):
    data = {"q": "query", **change}
    with pytest.raises(ContractError):
        SearchParameters.from_dict(data)


def test_multibyte_query_bound_and_existing_index_filters():
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
def test_route_is_bound_to_current_offer_not_just_target_name(change):
    with pytest.raises(ContractError):
        replace(request(), **change).check_offer(registration(), 1499)


def test_operation_binding_and_health_parameters():
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


def test_native_installation_identity_is_opaque_not_a_logical_name_or_path():
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


def test_driver_states_do_not_imply_live_health_or_managed_capabilities():
    target = DriverTarget("worker-a", "configured", ("ssh",))
    assert target.reason is None
    DriverTarget("worker-a", "unavailable", (), "provider-unavailable")
    for change in (
        {"state": "ready"}, {"capabilities": ("unknown",)},
        {"capabilities": ("ssh", "ssh")}, {"reason": "provider-unavailable"},
        {"state": "unavailable"}, {"capabilities": ["ssh"]},
    ):
        with pytest.raises(ContractError):
            replace(target, **change)
