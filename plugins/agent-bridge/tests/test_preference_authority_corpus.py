"""Consume frozen v2 authority vectors without widening the H1 envelope."""

import copy
import json
import struct
from pathlib import Path

import pytest

from agent_bridge import preference_attestation as a
from agent_bridge import session_preferences as p
from agent_bridge.session_host import protocol

ROOT = Path(__file__).parents[1] / "contract" / "fixtures" / "session-host" / "current"


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


@pytest.mark.guard
def test_frozen_authority_and_legacy_prefix():
    fixture = read("wrapper-authority-v1.json")
    receipt = fixture["receipt"]
    assert fixture["token"] == a.LAUNCH_TOKEN
    assert fixture["maximum_private_frame_bytes"] == a.MAX_FRAME
    assert receipt["authority"]["digest"] == a.component_digest()
    assert receipt["authority"]["target"] == a.target_digest(fixture["selected_target"])
    assert p.validate_receipt(receipt, 321) == receipt
    payload = struct.pack("!QQ", 0, 321) + json.dumps(receipt).encode()
    encoded = protocol.encode(protocol.MsgType.HELLO, payload)
    assert struct.unpack("!QQ", encoded[5:5 + fixture["hello_prefix_bytes"]]) == (0, 321)
    assert protocol.PROTOCOL_VERSION == fixture["captured_from"]["protocol_generation"]
    for vector in fixture["invalid_vectors"]:
        altered = copy.deepcopy(receipt)
        cursor = altered
        for key in vector["path"][:-1]:
            cursor = cursor[key]
        cursor[vector["path"][-1]] = vector["value"]
        assert p.validate_receipt(altered, 321) is None, vector


@pytest.mark.guard
def test_frozen_reader_precedence(monkeypatch, tmp_path):
    fixture = read("wrapper-settings-reader.json")
    assert fixture["keys"] == p.KEYS and fixture["flags"] == p.FLAGS
    assert fixture["source_policies"] == list(p.SOURCES)
    for key in ("COPILOT_MODEL", "COPILOT_OFFLINE", "COPILOT_PROVIDER_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for index, case in enumerate(fixture["cases"]):
        home = tmp_path / str(index)
        monkeypatch.setattr(Path, "home", lambda: home)
        if case["settings"] is not None:
            settings = home / ".copilot" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps(case["settings"]))
        candidate = p.execution_settings(case["argv"], case["env"], 321)
        assert candidate["status"] == case["status"]
        assert candidate["values"] == case["values"]
        assert candidate.get("provider_selected", False) == case.get("provider_selected", False)
        assert p.validate_receipt(candidate, 321) is None


@pytest.mark.guard
def test_frozen_terminal_component_contract():
    fixture = read("wrapper-exec.json")
    assert fixture["component_digest"] == a.component_digest()
    assert fixture["binding_environment"] == [a.FD_ENV, a.NONCE_ENV, a.DIGEST_ENV, a.MODE_ENV, a.TARGET_ENV]
    assert fixture["exec_requires_host_consent"] and fixture["exec_preserves_pid"]
    consent = read("wrapper-consent.json")
    assert consent["authority_modules"] == list(a.AUTHORITY_MODULES)
    assert consent["component_digest"] == a.component_digest()
