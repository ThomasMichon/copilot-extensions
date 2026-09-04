"""Regression tests for the agent-bridge contract registry checker."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-agent-bridge-contracts.py"
SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "agent-bridge"
    / "contract"
    / "registry.schema.json"
)
SOURCE = "plugins/agent-bridge/src/agent_bridge/protocol.py"
FIXTURE = "plugins/agent-bridge/contract/fixtures/http/current/health.json"
HOST_SOURCE = "plugins/agent-bridge/src/agent_bridge/session_host/protocol.py"
HOST_FIXTURE = (
    "plugins/agent-bridge/contract/fixtures/session-host/current/messages.json"
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative: str, value: str | dict[str, Any]) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2) + "\n" if isinstance(value, dict) else value
    path.write_text(text, encoding="utf-8")


def _sha256(repo: Path, relative: str) -> str:
    data = (repo / relative).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        canonical = data
    else:
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _registry(repo: Path, commit: str, blob: str) -> dict[str, Any]:
    http_contract = {
        "id": "agent-bridge.http-wire",
        "authority": "agent-bridge/http-wire",
        "owner": "agent-bridge",
        "kind": "wire-protocol",
        "normative": True,
        "declared_range": {
            "current": 12,
            "minimum": 1,
            "previous_generation": None,
            "previous_absent_reason": "Synthetic test registry has one generation.",
        },
        "evidence_window": {
            "generations": [12],
            "runtimes": ["1.0.0"],
        },
        "capability_versions": {
            "relay_interrupt": 2,
            "failed_acp_handshake": 3,
            "container_recreate": 4,
            "machine_metadata": 5,
            "result_snapshot": 6,
            "represented_result_snapshot": 7,
            "provider_target_refresh": 8,
            "at_rest_projection": 9,
            "attention_wait": 10,
            "remote_operations": 11,
            "conditional_idle_end": 12,
        },
        "durable_records": [],
        "source_paths": [
            {
                "path": SOURCE,
                "sha256": _sha256(repo, SOURCE),
                "semantic": True,
                "non_semantic_reason": None,
            }
        ],
        "fixtures": [
            {
                "path": FIXTURE,
                "sha256": _sha256(repo, FIXTURE),
                "role": "current health",
                "generation": 12,
            }
        ],
        "provenance": [
            {
                "commit": commit,
                "plugin_version": "1.0.0",
                "generation": 12,
                "source_path": SOURCE,
                "source_git_blob": blob,
                "capture_method": "Read exact committed source.",
            }
        ],
        "support_window": "Generations 1 through 10.",
        "bridge_contract_rollback_window": "Retain generation 9.",
        "mixed_version_scenarios": ["old-client_new-daemon"],
        "removal_gate": "Prove zero references.",
    }
    host_blob = _git(repo, "rev-parse", f"{commit}:{HOST_SOURCE}")
    host_contract = {
        "id": "agent-bridge.session-host-wire",
        "authority": "agent-bridge/session-host-wire",
        "owner": "agent-bridge",
        "kind": "wire-protocol",
        "normative": True,
        "declared_range": {
            "current": 1,
            "minimum": 1,
            "previous_generation": None,
            "previous_absent_reason": "Generation 1 is the first envelope.",
        },
        "evidence_window": {
            "generations": [1],
            "runtimes": ["1.0.0"],
        },
        "capability_versions": {"length_prefixed_envelope": 1},
        "durable_records": [],
        "source_paths": [
            {
                "path": HOST_SOURCE,
                "sha256": _sha256(repo, HOST_SOURCE),
                "semantic": True,
                "non_semantic_reason": None,
            }
        ],
        "fixtures": [
            {
                "path": HOST_FIXTURE,
                "sha256": _sha256(repo, HOST_FIXTURE),
                "role": "current messages",
                "generation": 1,
            }
        ],
        "provenance": [
            {
                "commit": commit,
                "plugin_version": "1.0.0",
                "generation": 1,
                "source_path": HOST_SOURCE,
                "source_git_blob": host_blob,
                "capture_method": "Read exact committed source.",
            }
        ],
        "support_window": "Generation 1.",
        "bridge_contract_rollback_window": "Retain generation 1.",
        "mixed_version_scenarios": ["new-frontend_H1-host"],
        "removal_gate": "Prove zero references.",
    }
    return {
        "schema_version": 1,
        "contracts": [http_contract, host_contract],
        "deferred_contracts": [],
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "checkout", "-q", "-b", "main")

    _write(
        root,
        SOURCE,
        "\n".join(
            [
                "HTTP_PROTOCOL_VERSION = 12",
                "HTTP_PROTOCOL_MIN_SUPPORTED = 1",
                "RELAY_INTERRUPT_PROTOCOL_VERSION = 2",
                "FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION = 3",
                "CONTAINER_RECREATE_PROTOCOL_VERSION = 4",
                "MACHINE_METADATA_PROTOCOL_VERSION = 5",
                "RESULT_SNAPSHOT_PROTOCOL_VERSION = 6",
                "REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION = 7",
                "PROVIDER_TARGET_REFRESH_PROTOCOL_VERSION = 8",
                "AT_REST_PROJECTION_PROTOCOL_VERSION = 9",
                "ATTENTION_WAIT_PROTOCOL_VERSION = 10",
                "REMOTE_OPERATIONS_PROTOCOL_VERSION = 11",
                "CONDITIONAL_IDLE_END_PROTOCOL_VERSION = 12",
                "",
            ]
        ),
    )
    _write(
        root,
        "plugins/agent-bridge/plugin.json",
        {"name": "agent-bridge", "version": "1.0.0"},
    )
    _write(root, HOST_SOURCE, "PROTOCOL_VERSION = 1\n")
    fixture = {
        "captured_from": {
            "commit": "pending",
            "plugin_version": "1.0.0",
            "protocol_generation": 12,
        },
        "response": {"status_code": 200},
    }
    _write(root, FIXTURE, fixture)
    host_fixture = {
        "captured_from": {
            "commit": "pending",
            "plugin_version": "1.0.0",
            "protocol_generation": 1,
        },
        "messages": {},
    }
    _write(root, HOST_FIXTURE, host_fixture)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline source")
    commit = _git(root, "rev-parse", "HEAD")
    blob = _git(root, "rev-parse", f"{commit}:{SOURCE}")

    fixture["captured_from"]["commit"] = commit
    fixture["captured_from"]["source_path"] = SOURCE
    fixture["captured_from"]["source_git_blob"] = blob
    fixture["captured_from"]["source_sha256"] = _sha256(root, SOURCE)
    _write(root, FIXTURE, fixture)
    host_blob = _git(root, "rev-parse", f"{commit}:{HOST_SOURCE}")
    host_fixture["captured_from"].update(
        {
            "commit": commit,
            "source_path": HOST_SOURCE,
            "source_git_blob": host_blob,
            "source_sha256": _sha256(root, HOST_SOURCE),
        }
    )
    _write(root, HOST_FIXTURE, host_fixture)
    schema_path = root / "plugins/agent-bridge/contract/registry.schema.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_bytes(SCHEMA.read_bytes())
    _write(
        root,
        "plugins/agent-bridge/contract/registry.json",
        _registry(root, commit, blob),
    )
    return root


def _mutate_registry(
    repo: Path,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    path = repo / "plugins/agent-bridge/contract/registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    _write(repo, "plugins/agent-bridge/contract/registry.json", data)


def test_valid_registry_passes(repo: Path) -> None:
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    assert "OK (2 contracts, 2 fixtures)" in result.stdout


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda data: data.__setitem__("schema_version", 2), "only version 1"),
        (
            lambda data: data["contracts"][0].pop("owner"),
            "missing fields: owner",
        ),
        (
            lambda data: data["contracts"][0].__setitem__("owner", None),
            "owner: must be a non-empty string",
        ),
        (
            lambda data: data["contracts"][0]["fixtures"][0].__setitem__(
                "sha256", "not-a-hash"
            ),
            "sha256 must be 64 lowercase",
        ),
        (
            lambda data: data["contracts"][0].__setitem__("provenance", []),
            "provenance: must be a non-empty array",
        ),
    ],
)
def test_malformed_registry_fails_deterministically(
    repo: Path,
    mutation: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    _mutate_registry(repo, mutation)
    first = _run(repo)
    second = _run(repo)
    assert first.returncode == 1
    assert first.stderr == second.stderr
    assert expected in first.stderr


def test_duplicate_id_and_authority_fail(repo: Path) -> None:
    def duplicate(data: dict[str, Any]) -> None:
        data["contracts"].append(copy.deepcopy(data["contracts"][0]))

    _mutate_registry(repo, duplicate)
    result = _run(repo)
    assert result.returncode == 1
    assert "duplicate contract id" in result.stderr
    assert "duplicate authority" in result.stderr


def test_invalid_required_contract_id_cannot_bypass_checks(repo: Path) -> None:
    def rename(data: dict[str, Any]) -> None:
        data["contracts"][0]["id"] = "INVALID ID"

    _mutate_registry(repo, rename)
    result = _run(repo)
    assert result.returncode == 1
    assert "must match ^[a-z0-9][a-z0-9.-]+$" in result.stderr
    assert "missing required protocol contracts: agent-bridge.http-wire" in result.stderr


def test_external_reference_requires_source_and_hash(repo: Path) -> None:
    def add_reference(data: dict[str, Any]) -> None:
        data["deferred_contracts"].append(
            {
                "id": "external.example",
                "owner": "#1",
                "classification": "externally-owned",
                "tracked_issue": "https://example.com/issues/1",
                "reason": "Owned elsewhere.",
            }
        )

    _mutate_registry(repo, add_reference)
    result = _run(repo)
    assert result.returncode == 1
    assert "externally-owned references require" in result.stderr


def test_deep_schema_corruption_fails(repo: Path) -> None:
    path = repo / "plugins/agent-bridge/contract/registry.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["$defs"]["fixture"]["required"].remove("generation")
    _write(repo, "plugins/agent-bridge/contract/registry.schema.json", schema)

    result = _run(repo)
    assert result.returncode == 1
    assert "schema.$defs.fixture: required fields do not match" in result.stderr


def test_missing_fixture_fails(repo: Path) -> None:
    (repo / FIXTURE).unlink()
    result = _run(repo)
    assert result.returncode == 1
    assert f"missing file {FIXTURE}" in result.stderr


def test_fixture_path_escape_fails(repo: Path) -> None:
    _write(repo, "outside.json", {"value": 1})

    def escape(data: dict[str, Any]) -> None:
        fixture = data["contracts"][0]["fixtures"][0]
        fixture["path"] = "outside.json"
        fixture["sha256"] = _sha256(repo, "outside.json")

    _mutate_registry(repo, escape)
    result = _run(repo)
    assert result.returncode == 1
    assert "path escapes plugins/agent-bridge/contract" in result.stderr


def test_changed_registered_source_requires_registry_update(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "registry baseline")
    base = _git(repo, "rev-parse", "HEAD")
    source = repo / SOURCE
    source.write_text(source.read_text(encoding="utf-8") + "NEW_FIELD = 11\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change contract source only")

    result = _run(repo, "--base", base)
    assert result.returncode == 1
    assert "registered contract source changed without updating" in result.stderr
    assert SOURCE in result.stderr


def test_git_reads_ignore_contaminated_ambient_environment(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", str(repo / "wrong.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo / "wrong-worktree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(repo / "missing-global-config"))

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check-agent-bridge-contracts: OK (2 contracts, 2 fixtures)\n"


def test_semantic_source_hash_cannot_advance_without_fixture(repo: Path) -> None:
    source = repo / SOURCE
    source.write_text(
        source.read_text(encoding="utf-8") + "NEW_FIELD = 11\n",
        encoding="utf-8",
    )

    def refresh_hash_only(data: dict[str, Any]) -> None:
        data["contracts"][0]["source_paths"][0]["sha256"] = _sha256(repo, SOURCE)

    _mutate_registry(repo, refresh_hash_only)
    result = _run(repo)
    assert result.returncode == 1
    assert "semantic current sources lack matching current fixtures" in result.stderr
    assert SOURCE in result.stderr
