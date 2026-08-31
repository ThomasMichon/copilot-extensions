from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "tools" / "clean-room" / "scenarios" / "context-injection-eval"


def _fixture_module():
    path = SCENARIO / "fixture.py"
    spec = importlib.util.spec_from_file_location("context_injection_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_room_manifest_requires_supported_authority_and_fresh_sessions() -> None:
    manifest = json.loads((SCENARIO / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["tier"] == "E"
    assert manifest["runs"]["count"] >= 2
    assert manifest["runs"]["aggregate"] == "unanimous"
    evaluation = manifest["eval"]
    assert evaluation["acp_cwd_file"] == (
        "/home/operator/context-injection-eval/acp-cwd"
    )
    plugin_root = "/home/operator/context-injection-eval/marketplace/plugins"
    assert evaluation["acp_plugin_dirs"] == [
        f"{plugin_root}/context-injection",
        f"{plugin_root}/selected-alpha",
        f"{plugin_root}/selected-beta",
        f"{plugin_root}/synthetic-side-effect",
    ]
    assert evaluation["payload_fingerprint_dirs"] == [plugin_root]
    assert manifest["tier_p_precondition"].endswith(
        "verify-tier-p --root /home/operator/context-injection-eval"
    )
    assert "Do not call tools, read files" in manifest["prompt"]


def test_fixture_declares_side_effect_only_hook_without_context() -> None:
    source = (SCENARIO / "fixture.py").read_text(encoding="utf-8")

    assert "schema: copilot-extensions.context-injection" in source
    assert '".context-injection" / "config.yaml"' in source
    assert "sessionContextAggregation" not in source
    assert '"sideEffects": "restart-safe-idempotent"' in source
    assert '"context": "none"' in source
    assert '"contributors": []' in source
    assert "session_identity_hashes" in source
    assert "cwd_identity_hashes" in source


def test_transcript_summary_requires_exact_tokens_and_no_tool_markers() -> None:
    fixture = _fixture_module()
    tokens = [
        "CRCTX_CANARY_A_ALPHA_" + "1" * 48,
        "CRCTX_CANARY_A_BETA_" + "2" * 48,
    ]
    strict = json.dumps({"tokens": sorted(tokens)}, separators=(",", ":"))

    passed = fixture.summarize_transcript(strict + "\n", tokens)
    duplicate = fixture.summarize_transcript(strict + "\n" + tokens[0], tokens)
    workaround = fixture.summarize_transcript("Tool call: view\n" + strict, tokens)

    assert passed["pass"] is True
    assert duplicate["pass"] is False
    assert workaround["pass"] is False


def test_failure_class_separates_response_format_from_transport(
    tmp_path: Path,
) -> None:
    fixture = _fixture_module()
    evidence = tmp_path / "eval" / "context-evidence.json"
    evidence.parent.mkdir()
    evidence.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "occurrence_counts": {"one": 1, "two": 1},
                        "invented_token_count": 0,
                        "tool_marker_count": 0,
                        "strict_response_count": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert fixture.failure_class(tmp_path) == "literal-response-contract"

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["runs"][0]["occurrence_counts"]["one"] = 0
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    assert fixture.failure_class(tmp_path) == "scenario-transport-gap"
