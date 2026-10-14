from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
import subprocess
import sys

import pytest

from fleet_manager.planning import MAX_BYTES, MAX_ITEMS, PlanError, canonical, digest
from fleet_manager.release_selection import ReleaseSelection
from test_release import manifest


def choice(release=None, role="client", **changes):
    release = manifest() if release is None else release
    value = {
        "schema": "fleet-manager.release-selection", "version": 1,
        "owner": "owner-1", "decision": "decision-1", "revision": 1,
        "authority": "fleet-1", "installation": "install-1", "role": role,
        "release_digest": digest(release), "sequence": 7,
        "components": sorted(release["roles"][role]),
    }
    value.update(changes)
    return value


def check(selected, current=None, release=None, **changes):
    arguments = {"authority": "fleet-1", "installation": "install-1",
                 "role": selected.to_dict()["role"], "sequence": 7}
    arguments.update(changes)
    return selected.check_selected(
        selected if current is None else current,
        canonical(manifest() if release is None else release), **arguments,
    )


@pytest.mark.parametrize("role", ["client", "gateway", "node"])
def test_explicit_selected_role_binds_exact_manifest_sequence_and_closure(role):
    release = manifest()
    value = choice(release, role)
    before = deepcopy((release, value))
    selected = ReleaseSelection.from_dict(value)
    assert check(selected, current=ReleaseSelection(selected.encoded), release=release) is None
    assert selected.to_dict() == value
    assert selected.selection_digest == hashlib.sha256(selected.encoded).hexdigest()
    assert (release, value) == before


def test_selection_retains_no_mutable_input_or_output_alias():
    value = choice()
    selected = ReleaseSelection.from_dict(value)
    original = selected.encoded
    value["components"].append("foreign")
    value["revision"] = 2
    output = selected.to_dict()
    output["components"].clear()
    output["owner"] = "other"
    assert selected.encoded == original
    assert selected.to_dict() == choice()
    with pytest.raises(FrozenInstanceError):
        selected.encoded = b"changed"


@pytest.mark.parametrize("field", [
    "schema", "version", "owner", "decision", "revision", "authority", "installation",
    "role", "release_digest", "sequence", "components",
])
def test_missing_selection_fields_refuse(field):
    value = choice()
    del value[field]
    with pytest.raises(PlanError):
        ReleaseSelection.from_dict(value)


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 2}, {"schema": "other"}, {"extra": "field"},
    {"owner": ""}, {"owner": "../owner"}, {"decision": False},
    {"authority": "https://example.invalid"}, {"installation": "x" * 129},
    {"role": "controller"}, {"role": None}, {"role": []},
    {"revision": True}, {"revision": 0}, {"revision": 2**63},
    {"sequence": False}, {"sequence": 0}, {"sequence": -1}, {"sequence": 2**63},
    {"release_digest": "F" * 64}, {"release_digest": "a" * 63},
    {"release_digest": None}, {"components": []}, {"components": ("cli",)},
    {"components": ["cli", "cli"]}, {"components": ["core", "cli"]},
    {"components": [True]}, {"components": ["../cli"]},
    {"components": [f"c{i:03}" for i in range(MAX_ITEMS + 1)]},
])
def test_invalid_selection_shape_refuses_without_normalizing_intent(changes):
    value = choice()
    value.update(changes)
    before = deepcopy(value)
    with pytest.raises(PlanError):
        ReleaseSelection.from_dict(value)
    assert value == before


@pytest.mark.parametrize("encoding", [
    lambda v: bytearray(canonical(v)),
    lambda v: canonical(v).decode(),
    lambda v: json.dumps(v, indent=1).encode(),
    lambda v: b'{"owner":"first","owner":"second"}',
    lambda v: b'{"revision":NaN}',
    lambda v: b"\xff",
    lambda v: b" " * (MAX_BYTES + 1),
])
def test_encoded_selection_requires_exact_immutable_bounded_canonical_json(encoding):
    with pytest.raises(PlanError):
        ReleaseSelection(encoding(choice()))


@pytest.mark.parametrize("changes", [
    {"owner": "owner-2"}, {"decision": "decision-2"}, {"revision": 2},
    {"authority": "fleet-2"}, {"installation": "install-2"}, {"sequence": 8},
    {"components": ["core"]}, {"release_digest": "f" * 64},
    {"role": "gateway", "components": ["cli", "core"]},
])
def test_stale_or_replaced_current_intent_is_not_an_idempotent_retry(changes):
    selected = ReleaseSelection.from_dict(choice())
    current = ReleaseSelection.from_dict(choice(**changes))
    with pytest.raises(PlanError, match="current selected intent"):
        check(selected, current=current)
    assert selected.selection_digest != current.selection_digest
    with pytest.raises(PlanError, match="current selected intent"):
        selected.check_selected(current.to_dict(), canonical(manifest()),
                                authority="fleet-1", installation="install-1",
                                role="client", sequence=7)


@pytest.mark.parametrize("changes", [
    {"authority": "other"}, {"installation": "other"}, {"role": "gateway"},
    {"sequence": 8}, {"sequence": True}, {"role": "controller"},
    {"authority": None}, {"installation": False}, {"sequence": 2**63},
    {"sequence": -1}, {"role": []},
])
def test_expected_scope_and_freshly_authenticated_sequence_must_match(changes):
    with pytest.raises(PlanError):
        check(ReleaseSelection.from_dict(choice()), **changes)


def test_higher_or_same_display_release_with_changed_content_is_not_selected():
    selected = ReleaseSelection.from_dict(choice())
    other = manifest()
    other["components"]["core"]["artifact_sha256"] = "d" * 64
    with pytest.raises(PlanError, match="manifest content"):
        check(selected, release=other)
    other["id"] = "newer-r2"
    with pytest.raises(PlanError):
        check(selected, release=other, sequence=8)
    assert selected.to_dict() == choice()


@pytest.mark.parametrize("components", [["core"], ["cli", "core"], ["foreign"]])
def test_partial_or_widened_component_closure_refuses(components):
    selected = ReleaseSelection.from_dict(choice(components=components))
    with pytest.raises(PlanError, match="component closure"):
        check(selected)


@pytest.mark.parametrize("raw", [
    lambda: bytearray(canonical(manifest())),
    lambda: json.dumps(manifest(), indent=1).encode(),
    lambda: canonical({**manifest(), "extra": "field"}),
    lambda: b'{"id":"first","id":"second"}',
])
def test_manifest_must_itself_be_valid_immutable_canonical_bytes(raw):
    selected = ReleaseSelection.from_dict(choice())
    with pytest.raises(PlanError):
        selected.check_selected(selected, raw(), authority="fleet-1",
                                installation="install-1", role="client", sequence=7)


@pytest.mark.parametrize("role", ["client", "gateway", "node"])
def test_installed_isolated_selection_consumer_uses_explicit_bytes_without_creating_state(tmp_path, role):
    script = """
import json, socket, sys
from pathlib import Path
import fleet_manager.release_selection as module
from fleet_manager.release_selection import ReleaseSelection
assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
def forbidden(*args, **kwargs):
    raise AssertionError('selection correlation must not access the network')
socket.socket = socket.create_connection = forbidden
selected = ReleaseSelection(sys.argv[1].encode())
current = ReleaseSelection(sys.argv[1].encode())
selected.check_selected(current, sys.argv[2].encode(), authority='fleet-1',
                        installation='install-1', role=sys.argv[3], sequence=7)
assert list(Path.cwd().iterdir()) == []
print(json.dumps({'digest': selected.selection_digest, 'role': selected.to_dict()['role']}))
"""
    selected = ReleaseSelection.from_dict(choice(role=role))
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, selected.encoded.decode(),
         canonical(manifest()).decode(), role],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"digest": selected.selection_digest, "role": role}
    assert list(tmp_path.iterdir()) == []
