"""Canonical source inventory must not revive or silently omit removed owners."""

import pytest

import bridge_http47_evidence as previous
import bridge_http51_evidence as evidence


@pytest.mark.parametrize("fault", [None, "present", "missing", "hash", "chain", "escape"])
def test_capture_relocation_requires_exact_absent_registered_owner(tmp_path, fault):
    old = "plugins/removed.py"
    current = "libs/canonical.py"
    target = tmp_path / current
    target.parent.mkdir()
    target.write_text("unchanged\n")
    canonical = {"path": current, "sha256": "a" * 64}
    relocated = {"path": old, "sha256": "a" * 64, "current_path": current}
    sources = [canonical, relocated]
    if fault == "present":
        historical = tmp_path / old
        historical.parent.mkdir()
        historical.write_text("unchanged\n")
    elif fault == "missing":
        sources.remove(canonical)
    elif fault == "hash":
        canonical["sha256"] = "b" * 64
    elif fault == "chain":
        canonical["current_path"] = "libs/other.py"
    elif fault == "escape":
        relocated["current_path"] = "../outside.py"
        canonical["path"] = "../outside.py"
    registry = {"contracts": [{"source_paths": sources}]}
    if fault is None:
        paths = previous.proof_paths(tmp_path, registry)
        assert current in paths and old not in paths
    else:
        with pytest.raises(ValueError):
            previous.proof_paths(tmp_path, registry)


def test_http51_uses_the_existing_capture_owner_and_keeps_prior_floors():
    assert evidence.SPEC.generation == 51
    assert dict(evidence.SPEC.capability_floors) == {
        "NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION": 51,
        "RETAINED_INBOX_PROTOCOL_VERSION": 48,
        "RETAINED_INBOX_PRODUCER_PROTOCOL_VERSION": 49,
        "RETAINED_INBOX_RECOVERY_PROTOCOL_VERSION": 50,
    }
    assert set(evidence.AFFECTED) == {
        previous.PREFIX + name for name in (
            "protocol.py", "native_send_admission.py", "native_bound_prompt.py",
            "native_keyed_admission.py", "routes/prompts.py", "send_outcome.py",
            "session_prompts.py", "session_targeting_cli.py",
        )
    }
