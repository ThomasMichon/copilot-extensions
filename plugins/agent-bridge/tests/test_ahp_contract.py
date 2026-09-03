"""Offline guards for the pinned Agent Host Protocol contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "ahp" / "v0.8.0"
UPSTREAM = FIXTURE_ROOT / "upstream"
MANIFEST = FIXTURE_ROOT / "source-manifest.json"
POLICY = FIXTURE_ROOT / "compatibility-policy.json"

MAP_INTERFACES = {
    "clientCommands": "CommandMap",
    "serverCommands": "ServerCommandMap",
    "clientNotifications": "ClientNotificationMap",
    "serverNotifications": "ServerNotificationMap",
}
EXPECTED_UNIONS = {
    "clientCommands": "_ExpectedCommands",
    "serverCommands": "_ExpectedServerCommands",
    "clientNotifications": "_ExpectedClientNotifications",
    "serverNotifications": "_ExpectedServerNotifications",
}
CLASSIFICATIONS = {
    "baseline",
    "named-capability",
    "state-prerequisite",
    "adjudicated-exception",
    "x-extension",
    "version-sensitive",
    "explicitly-out-of-scope",
}
NAMED_CAPABILITIES = {
    "AgentCapabilities.multipleChats",
    "AgentCapabilities.multipleChats.fork",
    "AgentCapabilities.multipleChats.sideChat",
    "AgentCapabilities.multipleWorkingDirectories",
    "AgentCapabilities.multipleWorkingDirectories.immutablePrimary",
    "AgentCapabilities.multipleWorkingDirectories.primaryReplacement",
    "ClientCapabilities.mcpApps",
    "McpServerCustomizationApps.capabilities.serverTools",
    "McpServerCustomizationApps.capabilities.serverTools.listChanged",
    "McpServerCustomizationApps.capabilities.serverResources",
    "McpServerCustomizationApps.capabilities.serverResources.listChanged",
    "McpServerCustomizationApps.capabilities.logging",
    "McpServerCustomizationApps.capabilities.sampling",
    "McpServerCustomizationApps.capabilities.sampling.tools",
    "ChangesetCapabilities.review",
}

pytestmark = pytest.mark.guard


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_unique(values: list[str], label: str) -> None:
    assert len(values) == len(set(values)), f"duplicate {label}"


def _extract_interface_methods(source: str, interface: str) -> set[str]:
    match = re.search(
        rf"export interface {re.escape(interface)} \{{(?P<body>.*?)\n\}}",
        source,
        re.DOTALL,
    )
    assert match, f"missing {interface}"
    return set(re.findall(r"^\s*'([^']+)'\s*:", match.group("body"), re.MULTILINE))


def _extract_expected_methods(source: str, type_name: str) -> set[str]:
    match = re.search(
        rf"type {re.escape(type_name)} =(?P<body>.*?);",
        source,
        re.DOTALL,
    )
    assert match, f"missing {type_name}"
    return set(re.findall(r"'([^']+)'", match.group("body")))


def test_pinned_artifacts_match_the_source_manifest() -> None:
    manifest = _load_json(MANIFEST)
    assert isinstance(manifest, dict)
    assert manifest["source"] == {
        "repository": "https://github.com/microsoft/agent-host-protocol",
        "tag": "v0.8.0",
        "commit": "7153143f1c6993fa886d7d59870811cdad479d83",
        "license": "MIT",
    }

    artifacts = manifest["artifacts"]
    artifact_paths = [entry["path"] for entry in artifacts]
    _assert_unique(artifact_paths, "artifact paths")
    expected = {entry["path"]: entry for entry in artifacts}
    actual = {
        path.relative_to(FIXTURE_ROOT).as_posix(): path
        for path in UPSTREAM.rglob("*")
        if path.is_file()
    }
    assert actual.keys() == expected.keys()

    for relative_path, path in actual.items():
        content = path.read_bytes()
        entry = expected[relative_path]
        assert len(content) == entry["bytes"], relative_path
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], relative_path


def test_schema_and_reference_corpora_are_valid_json() -> None:
    manifest = _load_json(MANIFEST)
    schemas = sorted((UPSTREAM / "schema").glob("*.schema.json"))
    reducers = sorted((UPSTREAM / "types" / "test-cases" / "reducers").glob("*.json"))
    round_trips = sorted(
        (UPSTREAM / "types" / "test-cases" / "round-trips").glob("*.json")
    )

    assert len(schemas) == manifest["corpusCounts"]["schemas"]
    assert len(reducers) == manifest["corpusCounts"]["reducers"]
    assert len(round_trips) == manifest["corpusCounts"]["roundTrips"]

    for path in schemas:
        schema = _load_json(path)
        assert isinstance(schema, dict)
        assert schema["$id"].startswith(
            "https://microsoft.github.io/agent-host-protocol/schema/"
        )

    for path in reducers:
        fixture = _load_json(path)
        assert isinstance(fixture, dict)
        assert {
            "description",
            "reducer",
            "initial",
            "actions",
            "expected",
        } <= fixture.keys(), path.name

    for path in round_trips:
        fixture = _load_json(path)
        assert isinstance(fixture, dict)
        assert {
            "name",
            "group",
            "description",
            "type",
            "input",
            "acceptableOutputs",
        } <= fixture.keys(), path.name


def test_message_maps_and_policy_are_exhaustive() -> None:
    messages = (UPSTREAM / "types" / "common" / "messages.ts").read_text(
        encoding="utf-8"
    )
    checks = (UPSTREAM / "types" / "version" / "message-checks.ts").read_text(
        encoding="utf-8"
    )
    policy = _load_json(POLICY)
    assert isinstance(policy, dict)

    for section, interface in MAP_INTERFACES.items():
        mapped = _extract_interface_methods(messages, interface)
        expected = _extract_expected_methods(checks, EXPECTED_UNIONS[section])
        assert mapped == expected

        classifications = policy["surfaceClassifications"][section]
        assert classifications.keys() == CLASSIFICATIONS
        classified = [
            method
            for methods in classifications.values()
            for method in methods
        ]
        assert len(classified) == len(set(classified)), section
        assert set(classified) == mapped, section


def test_version_and_sdk_metadata_remain_on_the_reviewed_contract() -> None:
    policy = _load_json(POLICY)
    assert isinstance(policy, dict)
    contract = policy["contract"]
    assert contract["selectedVersion"] == "0.8.0"
    assert contract["acceptedProtocolVersions"] == ["0.8.0"]
    assert contract["observedLaterRelease"] == "0.9.0"
    assert contract["decision"] == "retain-0.8.0"

    registry = (UPSTREAM / "types" / "version" / "registry.ts").read_text(
        encoding="utf-8"
    )
    assert "export const PROTOCOL_VERSION = '0.8.0';" in registry
    supported_match = re.search(
        r"SUPPORTED_PROTOCOL_VERSIONS:.*?Object\.freeze\(\[(?P<body>.*?)\]\);",
        registry,
        re.DOTALL,
    )
    assert supported_match
    upstream_supported = re.findall(r"'([^']+)'", supported_match.group("body"))

    clients = []
    for path in sorted((UPSTREAM / "clients").glob("*/release-metadata.json")):
        metadata = _load_json(path)
        assert isinstance(metadata, dict)
        clients.append(metadata["client"])
        assert metadata["packageVersion"] == "0.8.0"
        assert metadata["supportedProtocolVersions"] == upstream_supported

    _assert_unique(clients, "SDK client names")
    assert set(clients) == set(policy["conformance"]["officialClientCandidates"])


def test_dispose_chat_exception_and_capability_gate_stay_explicit() -> None:
    policy = _load_json(POLICY)
    assert isinstance(policy, dict)
    exception_entries = policy["exceptions"]
    exception_ids = [entry["id"] for entry in exception_entries]
    exception_surfaces = [entry["surface"] for entry in exception_entries]
    _assert_unique(exception_ids, "exception IDs")
    _assert_unique(exception_surfaces, "exception surfaces")
    exceptions = {entry["surface"]: entry for entry in exception_entries}
    dispose_chat = exceptions["disposeChat"]
    assert dispose_chat["decision"] == {
        "wireVocabulary": "recognized",
        "runtimeHandler": "not-implemented",
        "response": "JSON-RPC MethodNotFound",
        "capabilityConstraint": "AgentCapabilities.multipleChats remains absent.",
    }

    capability_entries = policy["namedCapabilities"]
    capability_paths = [entry["path"] for entry in capability_entries]
    _assert_unique(capability_paths, "named capability paths")
    capabilities = {entry["path"]: entry for entry in capability_entries}
    assert capabilities.keys() == NAMED_CAPABILITIES
    assert capabilities["AgentCapabilities.multipleChats"]["status"] == "deferred"

    messages = (UPSTREAM / "types" / "common" / "messages.ts").read_text(
        encoding="utf-8"
    )
    specification = (
        UPSTREAM / "docs" / "specification" / "chat-channel.md"
    ).read_text(encoding="utf-8")
    assert "'disposeChat':" in messages
    assert "does not currently expose a `disposeChat` command" in specification


def test_extension_namespace_is_reserved_but_unused() -> None:
    policy = _load_json(POLICY)
    assert isinstance(policy, dict)
    assert policy["extensions"]["reservedPrefix"] == "x-"
    assert policy["extensions"]["status"] == "no-extensions-in-first-slice"

    overview = (UPSTREAM / "docs" / "specification" / "overview.md").read_text(
        encoding="utf-8"
    )
    assert "The `x-` prefix is reserved" in overview

    classified_extensions = [
        method
        for section in policy["surfaceClassifications"].values()
        for method in section["x-extension"]
    ]
    assert classified_extensions == []
