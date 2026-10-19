"""Desired-state validation without authorization, I/O or transport fixtures."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from fleet_manager import planning
from fleet_manager.gateway_manifest import (
    GatewayManifest,
    KeySourceRef,
    ManifestError,
    RouteSpec,
    ServiceSlot,
)


@pytest.fixture
def document() -> dict[str, Any]:
    return {
        "schema": "fleet-manager.gateway-manifest",
        "version": 1,
        "fleet": "fleet-a",
        "gateway": "gateway-a",
        "revision": 1,
        "roles": ["service", "assets"],
        "key_sources": [
            {"name": "key-b", "provider": "owner-store", "reference": "keys/service-b@v2"},
            {"name": "key-a", "provider": "owner-store", "reference": "gateway/service-a"},
        ],
        "slots": [
            {"name": "slot-b", "role": "assets", "principal": "service-a",
             "key_source": "key-b", "configuration_ref": "config/assets@v1",
             "routes": [{"prefix": "/assets", "methods": ["GET"],
                         "capabilities": ["http"]}]},
            {"name": "slot-a", "role": "service", "principal": "service-a",
             "key_source": "key-a",
             "routes": [
                 {"prefix": "/ui", "methods": ["POST", "GET"],
                  "capabilities": ["response-stream", "http"]},
                 {"prefix": "/api", "methods": ["GET"], "capabilities": ["http"]},
             ]},
        ],
    }


def route(document: dict[str, Any]) -> dict[str, Any]:
    return document["slots"][0]["routes"][0]


def test_roundtrip_normalization_and_revision_identity(document: dict[str, Any]) -> None:
    original = copy.deepcopy(document)
    manifest = GatewayManifest.load(document)
    assert document == original
    assert GatewayManifest.parse(json.dumps(document).encode()) == manifest
    assert GatewayManifest.load(manifest.to_dict()) == manifest
    assert GatewayManifest.parse(manifest.canonical_bytes()) == manifest
    assert manifest.canonical_bytes() == planning.canonical(manifest.to_dict())
    assert manifest.digest == planning.digest(manifest.to_dict())
    assert len(manifest.digest) == 64
    assert manifest.roles == ("assets", "service")
    assert tuple(source.name for source in manifest.key_sources) == ("key-a", "key-b")
    assert tuple(slot.name for slot in manifest.slots) == ("slot-a", "slot-b")
    assert tuple(item.prefix for item in manifest.slots[0].routes) == ("/api", "/ui")
    assert manifest.slots[0].routes[1].methods == ("GET", "POST")
    assert manifest.slots[0].routes[1].capabilities == ("http", "response-stream")
    assert manifest.slots[0].configuration_ref is None
    assert "configuration_ref" not in manifest.to_dict()["slots"][0]
    assert manifest.slots[1].configuration_ref == "config/assets@v1"
    for field in ("roles", "key_sources", "slots"):
        document[field].reverse()
    for slot in document["slots"]:
        slot["routes"].reverse()
        for item in slot["routes"]:
            item["methods"].reverse()
            item["capabilities"].reverse()
    equivalent = GatewayManifest.load(document)
    assert equivalent == manifest
    assert equivalent.canonical_bytes() == manifest.canonical_bytes()
    assert equivalent.digest == manifest.digest
    document["revision"] = 2
    assert GatewayManifest.load(document).digest != manifest.digest


def test_loaded_state_is_frozen_and_detached(document: dict[str, Any]) -> None:
    manifest = GatewayManifest.load(document)
    before = manifest.canonical_bytes()
    objects = (manifest, manifest.key_sources[0], manifest.slots[0],
               manifest.slots[0].routes[0])
    for item in objects:
        field = next(iter(item.__dataclass_fields__))
        with pytest.raises(FrozenInstanceError):
            setattr(item, field, "changed")
    assert isinstance(manifest.key_sources[0], KeySourceRef)
    assert isinstance(manifest.slots[0], ServiceSlot)
    assert isinstance(manifest.slots[0].routes[0], RouteSpec)
    for collection in (manifest.roles, manifest.key_sources, manifest.slots,
                       manifest.slots[0].routes, manifest.slots[0].routes[0].methods,
                       manifest.slots[0].routes[0].capabilities):
        assert isinstance(collection, tuple)
    document["roles"].append("new-role")
    document["key_sources"][0]["reference"] = "new/reference"
    route(document)["methods"].append("DELETE")
    fresh = manifest.to_dict()
    fresh["roles"].append("other-role")
    fresh["key_sources"][0]["reference"] = "other/reference"
    fresh["slots"][0]["routes"][0]["methods"].append("DELETE")
    fresh["slots"].clear()
    assert manifest.canonical_bytes() == before


@pytest.mark.parametrize("field", [
    "schema", "version", "fleet", "gateway", "revision", "roles", "key_sources", "slots",
])
def test_missing_root_fields(document: dict[str, Any], field: str) -> None:
    del document[field]
    with pytest.raises(ManifestError, match="^invalid-fields$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize(("field", "value"), [
    ("schema", "fleet-manager.gateway"), ("version", True), ("version", 1.0),
    ("version", 2), ("revision", True), ("revision", False), ("revision", 0),
    ("revision", -1), ("revision", 2**53), ("revision", 1.0), ("revision", "1"),
    ("revision", None), ("fleet", ""), ("gateway", "bad/name"),
    ("fleet", "a" * 129), ("gateway", False),
])
def test_invalid_root_values(document: dict[str, Any], field: str, value: object) -> None:
    document[field] = value
    with pytest.raises(ManifestError):
        GatewayManifest.load(document)


def test_identifier_and_revision_boundaries(document: dict[str, Any]) -> None:
    document["revision"] = 2**53 - 1
    document["fleet"] = "A" + "_.-9" * 31 + "xyz"
    document["gateway"] = "0"
    manifest = GatewayManifest.load(document)
    assert len(manifest.fleet) == 128
    assert manifest.revision == 2**53 - 1


@pytest.mark.parametrize("level", ["root", "key-source", "slot", "route"])
@pytest.mark.parametrize("field", ["unknown", "key", "password", "token", "private_key"])
def test_unknown_and_inline_fields(
    document: dict[str, Any], level: str, field: str
) -> None:
    target = {"root": document, "key-source": document["key_sources"][0],
              "slot": document["slots"][0], "route": route(document)}[level]
    target[field] = "synthetic-secret-do-not-echo"
    with pytest.raises(ManifestError, match="^invalid-fields$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize(("level", "field"), [
    ("key-source", "name"), ("key-source", "provider"), ("key-source", "reference"),
    ("slot", "name"), ("slot", "role"), ("slot", "principal"), ("slot", "key_source"),
    ("slot", "routes"), ("route", "prefix"), ("route", "methods"), ("route", "capabilities"),
])
def test_missing_nested_fields(document: dict[str, Any], level: str, field: str) -> None:
    target = {"key-source": document["key_sources"][0],
              "slot": document["slots"][0], "route": route(document)}[level]
    del target[field]
    with pytest.raises(ManifestError, match="^invalid-fields$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize("level", ["key-source", "slot", "route"])
@pytest.mark.parametrize("value", [None, [], "not-an-object", True])
def test_nested_records_require_objects(
    document: dict[str, Any], level: str, value: object
) -> None:
    target = {"key-source": document["key_sources"],
              "slot": document["slots"], "route": document["slots"][0]["routes"]}[level]
    target[0] = value
    with pytest.raises(ManifestError, match="^invalid-fields$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize("field", ["roles", "key_sources", "slots"])
def test_duplicate_names(document: dict[str, Any], field: str) -> None:
    document[field].append(copy.deepcopy(document[field][0]))
    with pytest.raises(ManifestError, match="^duplicate-"):
        GatewayManifest.load(document)


@pytest.mark.parametrize(("field", "value", "code"), [
    ("role", "undeclared", "undeclared-role"),
    ("key_source", "undeclared", "undeclared-key-source"),
    ("name", "bad/name", "invalid-identifier"),
    ("principal", {}, "invalid-identifier"),
])
def test_explicit_slot_assignments(
    document: dict[str, Any], field: str, value: object, code: str
) -> None:
    document["slots"][0][field] = value
    with pytest.raises(ManifestError, match=f"^{code}$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize(("field", "value"), [
    ("name", "bad/name"), ("provider", "bad:provider"), ("provider", 1),
])
def test_key_source_identifiers(document: dict[str, Any], field: str, value: object) -> None:
    document["key_sources"][0][field] = value
    with pytest.raises(ManifestError, match="^invalid-identifier$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize("value", [
    "", "a" * 257, None, {}, "some secret", "key\nname", "key\x00name", "key\x7fname",
    "key\u0085name", "key\u00a0name", "https://owner:password@example/key",
    "https://example/key", "user:password@host", "password=synthetic",
    "token:synthetic", "private_key=synthetic", "key?token=synthetic",
    "key#fragment", "/absolute", "../key", "keys/../key", "keys//key", "keys/",
    "keys\\name", "keys/%41", "$(command)", "keys;command", "keys@v1/extra",
    "keys@", "keys@v1@v2", "clé/name",
])
@pytest.mark.parametrize("field", ["reference", "configuration_ref"])
def test_reference_grammar(document: dict[str, Any], field: str, value: object) -> None:
    target = document["key_sources"][0] if field == "reference" else document["slots"][0]
    target[field] = value
    with pytest.raises(ManifestError, match="^invalid-reference$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize("value", ["gateway/service-a", "keys/service-a@v2", "a" * 256])
def test_reference_boundaries(document: dict[str, Any], value: str) -> None:
    document["key_sources"][0]["reference"] = value
    document["slots"][0]["configuration_ref"] = value
    GatewayManifest.load(document)


@pytest.mark.parametrize("prefix", [
    "", "/", "relative", "/trail/", "/a//b", "//a", "/a%20b", "/a%2Fb",
    "/a\\b", "/a?query", "/a#fragment", "/a b", "/a\tb", "/a\x00b", "/a\x7fb",
    "/a\u00a0b", "/.", "/..", "/a/./b", "/a/../b", "/é", "/a:b", "/a@b",
    "/" + "a" * 256, None, [],
])
def test_unsafe_prefixes(document: dict[str, Any], prefix: object) -> None:
    route(document)["prefix"] = prefix
    with pytest.raises(ManifestError, match="^invalid-prefix$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize("prefix", ["/v1", "/_fleet", "/health", "/ready", "/metrics", "/status"])
@pytest.mark.parametrize("suffix", ["", "/nested"])
def test_reserved_prefixes(document: dict[str, Any], prefix: str, suffix: str) -> None:
    route(document)["prefix"] = prefix + suffix
    with pytest.raises(ManifestError, match="^reserved-prefix$"):
        GatewayManifest.load(document)


@pytest.mark.parametrize("prefix", [
    "/v10", "/_fleet-extra", "/healthcheck", "/readyness", "/metrics-v2", "/status2",
    "/a/health", "/a.../b._~-9", "/" + "a" * 255,
])
def test_segment_aware_boundaries(document: dict[str, Any], prefix: str) -> None:
    route(document)["prefix"] = prefix
    GatewayManifest.load(document)


@pytest.mark.parametrize("prefix", ["/api", "/api/child", "/api/child/deeper"])
@pytest.mark.parametrize("same_slot", [False, True])
def test_global_route_overlaps(
    document: dict[str, Any], prefix: str, same_slot: bool
) -> None:
    target = document["slots"][1]["routes"][0] if same_slot else route(document)
    target["prefix"] = prefix
    with pytest.raises(ManifestError, match="^overlapping-prefix$"):
        GatewayManifest.load(document)


def test_ancestor_after_descendant_and_segment_siblings(document: dict[str, Any]) -> None:
    route(document)["prefix"] = "/api/child"
    with pytest.raises(ManifestError, match="^overlapping-prefix$"):
        GatewayManifest.load(document)
    route(document)["prefix"] = "/api-v2"
    GatewayManifest.load(document)


@pytest.mark.parametrize("methods", [
    [], ["CONNECT"], ["TRACE"], ["get"], ["UNKNOWN"], ["GET", "GET"], [True],
    "GET", None, [["GET"]], ["GET"] * 8,
])
def test_method_denials(document: dict[str, Any], methods: object) -> None:
    route(document)["methods"] = methods
    with pytest.raises(ManifestError):
        GatewayManifest.load(document)


@pytest.mark.parametrize("capabilities", [
    [], ["response-stream"], ["websocket"], ["http", "websocket"],
    ["http", "http"], "http", None, [True], [["http"]],
])
def test_capability_denials(document: dict[str, Any], capabilities: object) -> None:
    route(document)["capabilities"] = capabilities
    with pytest.raises(ManifestError):
        GatewayManifest.load(document)


def test_all_methods_and_capabilities(document: dict[str, Any]) -> None:
    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    route(document)["methods"] = methods
    route(document)["capabilities"] = ["response-stream", "http"]
    manifest = GatewayManifest.load(document)
    assert manifest.slots[1].routes[0].methods == tuple(sorted(methods))


@pytest.mark.parametrize(("field", "value"), [
    ("roles", []), ("roles", ["role"] * 65), ("roles", {}), ("roles", [True]),
    ("key_sources", []), ("key_sources", [{}] * 129), ("key_sources", {}),
    ("slots", [{}] * 65), ("slots", {}), ("slots", None),
])
def test_collection_limits(document: dict[str, Any], field: str, value: object) -> None:
    document[field] = value
    with pytest.raises(ManifestError):
        GatewayManifest.load(document)


@pytest.mark.parametrize("value", [[], [{}] * 17, {}, None])
def test_per_slot_route_limits(document: dict[str, Any], value: object) -> None:
    document["slots"][0]["routes"] = value
    with pytest.raises(ManifestError, match="^collection-limit$"):
        GatewayManifest.load(document)


def test_empty_withdrawal_and_collection_maxima(document: dict[str, Any]) -> None:
    document["roles"] = [f"role-{index}" for index in range(64)]
    document["key_sources"] = [
        {"name": f"key-{index}", "provider": "owner", "reference": "keys/name"}
        for index in range(128)
    ]
    document["slots"] = []
    manifest = GatewayManifest.load(document)
    assert manifest.slots == ()
    assert GatewayManifest.parse(manifest.canonical_bytes()) == manifest
    document["slots"] = [
        {"name": f"slot-{index}", "role": "role-0", "principal": "same-identity",
         "key_source": "key-0", "routes": [
             {"prefix": f"/service-{index}/route-{number}", "methods": ["GET"],
              "capabilities": ["http"]} for number in range(4)]}
        for index in range(64)
    ]
    manifest = GatewayManifest.load(document)
    assert len(manifest.slots) == 64
    assert sum(len(slot.routes) for slot in manifest.slots) == 256
    document["slots"][0]["routes"].append(
        {"prefix": "/extra", "methods": ["GET"], "capabilities": ["http"]}
    )
    with pytest.raises(ManifestError, match="^route-limit$"):
        GatewayManifest.load(document)


def test_sixteen_routes_per_slot(document: dict[str, Any]) -> None:
    document["slots"] = document["slots"][:1]
    document["slots"][0]["routes"] = [
        {"prefix": f"/route-{index}", "methods": ["GET"], "capabilities": ["http"]}
        for index in range(16)
    ]
    assert len(GatewayManifest.load(document).slots[0].routes) == 16


@pytest.mark.parametrize("raw", [
    b"", b"{", b"null", b"[]", b"{}", b'{"revision":1,"revision":2}',
    b'{"nested":{"key":1,"key":2}}', b'{"x":NaN}', b'{"x":Infinity}',
    b'{"x":-Infinity}', b'{"x":1e999}', b'{"x":"\\ud800"}', b"\xff",
    b"[" * 20 + b"0" + b"]" * 20,
    pytest.param(b" " * (planning.MAX_BYTES + 1), id="oversized-json"),
])
def test_strict_json_failures(raw: bytes) -> None:
    with pytest.raises(ManifestError):
        GatewayManifest.parse(raw)


@pytest.mark.parametrize("raw", ["{}", bytearray(b"{}"), None, 1])
def test_parse_requires_bytes(raw: Any) -> None:
    with pytest.raises(ManifestError, match="^invalid-json$"):
        GatewayManifest.parse(raw)


@pytest.mark.parametrize("value", [
    None, [], True, {"x": object()}, {"x": float("nan")}, {"x": "\ud800"},
    {"x": ["x"] * (planning.MAX_ITEMS + 1)}, {"x" * 257: 0},
    {str(index): 0 for index in range(planning.MAX_ITEMS + 1)},
    {"x": "x" * 65537}, {"x": ["x" * 65536] * 17},
])
def test_root_and_tree_limits(value: object) -> None:
    with pytest.raises(ManifestError):
        GatewayManifest.load(value)


def test_cycles_and_nesting_reuse_planning_bounds(document: dict[str, Any]) -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(ManifestError, match="^invalid-json$"):
        GatewayManifest.load(cycle)
    nested: object = 0
    for _ in range(14):
        nested = [nested]
    document["extra"] = nested
    with pytest.raises(ManifestError, match="^invalid-json$"):
        GatewayManifest.load(document)


def test_errors_do_not_echo_supplied_values(document: dict[str, Any]) -> None:
    secret = "synthetic-private-token-do-not-echo"
    documents = [
        {**document, "password": secret},
        {**document, "fleet": f"password={secret}"},
        {**document, "revision": secret},
    ]
    document["key_sources"][0]["reference"] = f"https://user:{secret}@host/key"
    documents.append(document)
    for value in documents:
        with pytest.raises(ManifestError) as caught:
            GatewayManifest.load(value)
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)
        assert caught.value.code == str(caught.value)
    with pytest.raises(ManifestError) as caught:
        GatewayManifest.parse(f'{{"key":"{secret}","key":0}}'.encode())
    assert secret not in str(caught.value)
    assert caught.value.__suppress_context__
