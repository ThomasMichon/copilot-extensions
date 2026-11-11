"""Standing-charter declarations are validated records, never supervisor processes."""

from __future__ import annotations

import pytest

from agent_dispatch.registrar import RegistrarError, load_declaration
from agent_dispatch.registrar_discovery import read_location
from agent_dispatch.registrar_reconcile import declared_registrations
from agent_dispatch.registrar_standing import standing_source_entries


def _document(**overrides):
    spec = {
        "charter_key": "dispatcher", "lane_key": "primary", "effort_ref": "efforts/dispatcher",
        "materialization_ref": "roles/dispatcher", "source_revision": "template-1",
        "expected_materialization_revision": "effective-1",
    }
    spec.update(overrides.pop("spec", {}))
    return {"name": "dispatcher-primary", "kind": "standing-charter", "repos": "example/harness",
            "title": "Dispatcher", "spec": spec, **overrides}


def test_valid_document_normalizes_without_becoming_a_registration():
    declaration = load_declaration(_document())
    assert declaration.kind == "standing-charter"
    assert declaration.spec["enabled"] is True
    assert declared_registrations([declaration], machine="host-a") == []


@pytest.mark.parametrize("document", [
    _document(repos="all"),
    _document(owner="self-asserted"),
    _document(labels=["general"]),
    _document(spec={"enabled": "no"}),
    _document(spec={"extra": "value"}),
    _document(spec={"lane_key": " padded"}),
    {k: v for k, v in _document().items() if k != "repos"},
])
def test_invalid_documents_are_refused(document):
    with pytest.raises(RegistrarError):
        load_declaration(document)


def test_namespace_comes_from_discovery_provenance(tmp_path):
    import json

    (tmp_path / "dispatcher.json").write_text(json.dumps(_document(spec={"enabled": False})))
    declarations = read_location(tmp_path, owner="repo:harness")
    [entry] = standing_source_entries(declarations)
    assert entry.namespace == "repo:harness"
    assert entry.declaration["namespace"] == "repo:harness"
    assert entry.repo == "example/harness" and entry.enabled is False
    assert entry.source_path.endswith("dispatcher.json")
    with pytest.raises(RegistrarError, match="owner provenance"):
        standing_source_entries([load_declaration(_document())])
