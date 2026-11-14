"""Declaration preparation binds sources without inventing capture evidence."""

from __future__ import annotations

import copy
import pytest

import agent_bridge_contract_registry as codec
import bridge_http51_declarations as declarations
import bridge_http51_evidence as evidence
import bridge_http52_evidence as membership_evidence

COMMIT = "a" * 40


@pytest.fixture
def source(tmp_path, monkeypatch):
    committed = {}
    for path in sorted({*evidence.AFFECTED, *membership_evidence.AFFECTED, declarations.REFERENCE}):
        data = (path + "\n").encode()
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        committed[path] = data
    packet = "plugins/agent-bridge/contract/fixtures/http/current/frozen.json"
    (tmp_path / packet).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / packet).write_bytes(b'{"frozen":true}\n')
    registry = {
        "schema_version": 1,
        "contracts": [{
            "id": "agent-bridge.http-wire",
            "source_paths": [{
                "path": evidence.AFFECTED[0], "sha256": "0" * 64,
                "semantic": True, "non_semantic_reason": None,
            }],
            "provenance": [{"commit": "b" * 40}],
            "fixtures": [{"path": packet, "sha256": codec.digest((tmp_path / packet).read_bytes())}],
        }],
        "deferred_contracts": [{
            "id": declarations.DEFERRED, "reference_path": declarations.REFERENCE,
            "reference_sha256": "0" * 64, "reason": "retained deferred scope",
        }],
    }
    (tmp_path / codec.REGISTRY).write_bytes(codec.encode(registry))

    def git(root, *args):
        assert root == tmp_path
        assert args[0] == "show" and args[1].startswith(COMMIT + ":")
        return committed[args[1].split(":", 1)[1]]

    monkeypatch.setattr(evidence.previous, "git", git)
    return tmp_path, codec.load(tmp_path), committed


@pytest.mark.parametrize("policy", [evidence, membership_evidence])
def test_plan_changes_only_exact_source_declarations_and_doc_reference(source, policy):
    root, snapshot, _ = source
    original = copy.deepcopy(snapshot.materialized)
    outputs = declarations.declaration_plan(root, COMMIT, snapshot, evidence=policy)
    assert set(outputs) == {codec.REGISTRY}
    updated = codec.decode(outputs[codec.REGISTRY])
    before, after = original["contracts"][0], updated["contracts"][0]
    assert before["provenance"] == after["provenance"]
    assert before["fixtures"] == after["fixtures"]
    assert all(after["capability_versions"][key] == value
               for key, value in policy.CAPABILITY_VERSIONS.items())
    assert {row["path"] for row in after["source_paths"]} == set(policy.AFFECTED)
    for row in after["source_paths"]:
        assert row["semantic"] is True and row["non_semantic_reason"] is None
        assert row["sha256"] == codec.digest((root / row["path"]).read_bytes())
    reference = updated["deferred_contracts"][0]
    assert reference["reference_sha256"] == codec.digest(
        (root / declarations.REFERENCE).read_bytes(),
    )
    assert reference["reason"] == original["deferred_contracts"][0]["reason"]
    assert snapshot.materialized == original
    assert codec.load(root).materialized == original


def test_plan_refuses_dirty_source_without_publishing(source):
    root, snapshot, _ = source
    (root / evidence.AFFECTED[0]).write_bytes(b"dirty\n")
    with pytest.raises(ValueError, match="dirty HTTP-51 declaration source"):
        declarations.declaration_plan(root, COMMIT, snapshot)
    assert codec.load(root).materialized == snapshot.materialized


def test_plan_refuses_unclassified_changed_http_owner(source):
    root, snapshot, _ = source
    path = "plugins/agent-bridge/src/agent_bridge/other.py"
    (root / path).write_bytes(b"other\n")
    snapshot.materialized["contracts"][0]["source_paths"].append({
        "path": path, "sha256": "0" * 64, "semantic": False,
        "non_semantic_reason": "original classification",
    })
    with pytest.raises(ValueError, match="unclassified HTTP-51 source drift"):
        declarations.declaration_plan(root, COMMIT, snapshot)


def relocated_owner(snapshot):
    canonical = "libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/remote-driver.mjs"
    alias = "plugins/agent-bridge/extensions/agent-bridge/remote-driver.mjs"
    owner = {
        "path": canonical, "sha256": "0" * 64,
        "semantic": True, "non_semantic_reason": None,
    }
    relocated = {
        "path": alias, "sha256": "0" * 64, "semantic": False,
        "non_semantic_reason": "Relocated implementation; canonical owner is registered separately.",
        "current_path": canonical,
    }
    snapshot.materialized["contracts"][0]["source_paths"].extend((owner, relocated))
    return owner, relocated


def test_plan_refreshes_relocated_alias_without_reclassifying_or_capturing_it(source):
    root, snapshot, _ = source
    owner, alias = relocated_owner(snapshot)
    (root / alias["path"]).unlink(missing_ok=True)
    before = copy.deepcopy(snapshot.materialized["contracts"][0])
    outputs = declarations.declaration_plan(root, COMMIT, snapshot, evidence=membership_evidence)
    after = codec.decode(outputs[codec.REGISTRY])["contracts"][0]
    actual = {row["path"]: row for row in after["source_paths"]}
    expected = codec.digest((root / owner["path"]).read_bytes())
    assert actual[owner["path"]]["sha256"] == actual[alias["path"]]["sha256"] == expected
    assert actual[alias["path"]] == {**alias, "sha256": expected}
    assert after["provenance"] == before["provenance"]
    assert after["fixtures"] == before["fixtures"]


@pytest.mark.parametrize("classification", ["missing", "non-semantic", "semantic-alias"])
def test_relocated_alias_cannot_borrow_undeclared_or_nonsemantic_authority(source, classification):
    root, snapshot, _ = source
    owner, alias = relocated_owner(snapshot)
    if classification == "missing":
        snapshot.materialized["contracts"][0]["source_paths"].remove(owner)
    elif classification == "non-semantic":
        owner.update(semantic=False, non_semantic_reason="Not a semantic owner.")
    else:
        alias.update(semantic=True, non_semantic_reason=None)
    with pytest.raises(ValueError, match="relocated HTTP-51 source lacks its semantic owner"):
        declarations.declaration_plan(root, COMMIT, snapshot, evidence=membership_evidence)


def test_plan_refuses_dirty_doc_or_foreign_doc_owner(source):
    root, snapshot, _ = source
    (root / declarations.REFERENCE).write_bytes(b"dirty\n")
    with pytest.raises(ValueError, match="dirty HTTP-51 deferred documentation"):
        declarations.declaration_plan(root, COMMIT, snapshot)
    snapshot.materialized["deferred_contracts"][0]["reference_path"] = "foreign.md"
    with pytest.raises(ValueError, match="documentation owner changed"):
        declarations.declaration_plan(root, COMMIT, snapshot)


def test_plan_refuses_foreign_contract_source_drift(source):
    root, snapshot, _ = source
    path = "foreign.py"
    (root / path).write_bytes(b"foreign\n")
    snapshot.materialized["contracts"].append({
        "id": "other", "source_paths": [{
            "path": path, "sha256": "0" * 64, "semantic": True, "non_semantic_reason": None,
        }], "provenance": [{"commit": "b" * 40}], "fixtures": [],
    })
    with pytest.raises(ValueError, match="foreign contract source drift"):
        declarations.declaration_plan(root, COMMIT, snapshot)


def test_prepare_refuses_frozen_fixture_drift_before_publication(source, monkeypatch):
    root, snapshot, _ = source
    monkeypatch.setattr(evidence, "baseline", lambda *args: snapshot)
    monkeypatch.setattr(
        declarations, "publish", lambda *args, **kwargs: pytest.fail("published dirty evidence"),
    )
    packet = snapshot.materialized["contracts"][0]["fixtures"][0]["path"]
    (root / packet).write_bytes(b'{"frozen":false}\n')
    with pytest.raises(ValueError, match="immutable capture fixture drift"):
        declarations.prepare(root, COMMIT)


def test_semantic_portfolio_requires_receipt_sources_and_probe():
    assert evidence.previous.PREFIX + "native_send_receipt.py" in evidence.AFFECTED
    assert evidence.previous.PREFIX + "routes/health.py" in evidence.AFFECTED
    assert "plugins/agent-bridge/tests/http51_receipt_probes.py" in evidence.EXTRA_PATHS
    assert ("NATIVE_SEND_RECEIPT_CAPABILITY_VERSION", 1) in evidence.SPEC.capability_floors
    assert evidence.previous.PREFIX + "live_exact_notify.py" in evidence.AFFECTED
    assert evidence.previous.PREFIX + "live_keyed_receipt.py" in evidence.AFFECTED
    assert "plugins/agent-bridge/tests/http51_live_cli_probes.py" in evidence.EXTRA_PATHS
    for name in (
        "__main__.py", "app_routes.py", "events.py", "compaction_evidence.py",
        "session_observation_contract.py", "session_observations.py",
        "session_observation_cli.py", "routes/session_observations.py",
    ):
        assert evidence.previous.PREFIX + name in evidence.AFFECTED
    assert evidence.PROBES["test_http51_observation_boundaries"] == 2
    assert evidence.PROBES["test_observation_http_resource_boundary"] == 3
    assert evidence.PROBES["test_observation_cancellation_preserves_other_subscription"] == 1
    assert evidence.PROBES["test_observer_cli_http_eof"] == 8
    assert evidence.PROBES["test_http51_observer_payload_abbreviations"] == 2
    assert evidence.PROBES["test_http51_owner_membership_observations"] == 2
    assert evidence.PROBES["test_adopted_writable_connection_preserves_membership_commits"] == 1
    assert evidence.previous.PREFIX + "db_adoption.py" in evidence.AFFECTED
    for name in (
        "owner_membership.py", "owner_membership_records.py", "owner_membership_contract.py",
        "owner_membership_cli.py", "routes/owner_membership.py",
        "owner_membership_read.py", "owner_membership_signal.py",
        "db_sessions.py", "db_relationships.py", "db_live_sessions.py",
    ):
        assert evidence.previous.PREFIX + name in evidence.AFFECTED
