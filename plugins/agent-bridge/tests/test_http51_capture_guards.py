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
        "NATIVE_SEND_RECEIPT_CAPABILITY_VERSION": 1,
        "RETAINED_INBOX_PROTOCOL_VERSION": 48,
        "RETAINED_INBOX_PRODUCER_PROTOCOL_VERSION": 49,
        "RETAINED_INBOX_RECOVERY_PROTOCOL_VERSION": 50,
    }
    assert set(evidence.AFFECTED) == {
        previous.PREFIX + name for name in (
            "__main__.py", "app_routes.py", "events.py", "compaction_evidence.py",
            "session_observation_contract.py", "session_observations.py",
            "session_observation_cli.py", "routes/session_observations.py",
            "protocol.py", "native_send_admission.py", "native_bound_prompt.py",
            "native_keyed_admission.py", "routes/prompts.py", "send_outcome.py",
            "session_prompts.py", "session_targeting_cli.py",
            "native_send_receipt.py", "native_keyed_client.py", "routes/health.py",
            "db_core.py", "db_schema.py", "inbox_custody.py", "db_live_sessions.py", "db_live_session_aliases.py",
            "db_live_message_receipts.py", "live_message_enqueue.py", "live_message_models.py",
            "live_keyed_receipt.py", "live_exact_notify.py", "routes/live_inbox.py",
            "routes/live_sessions.py", "routes/live_message_receipts.py",
        )
    } | {
        "plugins/agent-bridge/extensions/agent-bridge/delivery.mjs",
        "plugins/agent-bridge/extensions/agent-bridge/extension.mjs",
    }


def test_http51_observation_expansion_retains_the_original_probe_counts():
    original = {
        "test_http51_capture_actual_observations": 2,
        "test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race": 1,
        "test_failed_resume_without_a_matching_receipt_remains_a_failure": 1,
        "test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint": 20,
    }
    assert {key: evidence.PROBES[key] for key in original} == original
    assert evidence.PROBES["test_http51_observation_boundaries"] == 2
    assert evidence.PROBES["test_observation_http_resource_boundary"] == 3
    assert evidence.PROBES["test_observation_cancellation_preserves_other_subscription"] == 1
    assert evidence.PROBES["test_observer_cli_http_eof"] == 8
    assert evidence.PROBES["test_http51_observer_payload_abbreviations"] == 2
    assert sum(evidence.PROBES.values()) == 40
    assert evidence.GENERATION == 51
    assert evidence.FAMILY == "http51-standard-native-send-asgi-v1"
    assert evidence.CAPABILITY_VERSIONS["bridge-session-observations-v1"] == 1
    assert evidence.CAPABILITY_VERSIONS["bridge-session-observation-history-v1"] == 1
    assert "tools/bridge_http51_declarations.py" in evidence.EXTRA_PATHS


@pytest.mark.parametrize("fault", [None, "bytes", "code"])
def test_http51_preloaded_containment_dependency_still_requires_exact_code(monkeypatch, fault):
    from pathlib import Path
    import agent_procutil.daemon_instances as module
    from bridge_http47_runtime import sha, verify_preloaded

    identity = {"sha256": sha(Path(module.__file__).read_bytes())}
    if fault == "bytes":
        identity["sha256"] = "0" * 64
    elif fault == "code":
        namespace = {}
        exec(compile(
            "def default_registry_root(): return None\n", module.__file__, "exec",
        ), namespace)
        monkeypatch.setattr(module, "default_registry_root", namespace["default_registry_root"])
    if fault is None:
        verify_preloaded(module, identity)
    else:
        with pytest.raises(ValueError, match="unproven"):
            verify_preloaded(module, identity)
