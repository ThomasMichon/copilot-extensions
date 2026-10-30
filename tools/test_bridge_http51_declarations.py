"""Declaration preparation binds sources without inventing capture evidence."""

from __future__ import annotations

import copy
import pytest

import agent_bridge_contract_registry as codec
import bridge_http51_declarations as declarations
import bridge_http51_evidence as evidence

COMMIT = "a" * 40


@pytest.fixture
def source(tmp_path, monkeypatch):
    committed = {}
    for path in (*evidence.AFFECTED, declarations.REFERENCE):
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


def test_plan_changes_only_exact_source_declarations_and_doc_reference(source):
    root, snapshot, _ = source
    original = copy.deepcopy(snapshot.materialized)
    outputs = declarations.declaration_plan(root, COMMIT, snapshot)
    assert set(outputs) == {codec.REGISTRY}
    updated = codec.decode(outputs[codec.REGISTRY])
    before, after = original["contracts"][0], updated["contracts"][0]
    assert before["provenance"] == after["provenance"]
    assert before["fixtures"] == after["fixtures"]
    assert {row["path"] for row in after["source_paths"]} == set(evidence.AFFECTED)
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
