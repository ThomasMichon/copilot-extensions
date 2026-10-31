"""Actual advertised retained-producer admission, not SDK/native qualification."""

import json
from pathlib import Path

import pytest

import bridge_http49_evidence as evidence
from bridge_http_retained_probes import capture_request, observe, record_observations
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from test_live_inbox_runtime import installation as installation, serving as serving

ROOT = Path(__file__).resolve().parents[3]


def test_http49_capture_actual_observations(serving, request) -> None:
    bound = capture_request(evidence, ROOT)
    if HTTP_PROTOCOL_VERSION != evidence.GENERATION:
        if bound is not None:
            raise ValueError("HTTP-49 execution requires its matching source generation")
        pytest.skip("Historical HTTP-49 execution requires matching source; current capture is HTTP-50")
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    observed = observe(serving, vector)
    assert observed == vector["expected"]
    if bound is not None:
        record_observations(evidence, bound, request.node.callspec.params["installation"], observed)
