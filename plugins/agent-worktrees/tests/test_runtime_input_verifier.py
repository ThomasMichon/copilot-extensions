from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def verifier(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return importlib.import_module("runtime_input_verifier")


_CHILD = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import runtime_input_verifier as verification
identity = json.loads(sys.argv[2])
marker = pathlib.Path(sys.argv[3])
try:
    client = verification.current_client(identity)
    assert verification.ENVIRONMENT not in __import__("os").environ
    client.check()
    marker.write_text("first-check")
    client.check()
    marker.write_text("published")
except verification.VerificationRefused:
    marker.write_text("refused")
finally:
    verification.close_clients()
"""


def _launch(guard, verifier, temporary, *, identity=None):
    output = (temporary / "stdout").open("wb")
    error = (temporary / "stderr").open("wb")
    try:
        with guard.child_launch(os.environ) as launch:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", _CHILD,
                 str(Path(verifier.__file__).parent),
                 json.dumps(guard.identity if identity is None else identity), str(temporary / "result")],
                **launch, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
    finally:
        output.close()
        error.close()
    return process


@pytest.mark.parametrize("outcome", ["accepted", "revoked", "cancelled"])
def test_native_candidate_checks_live_owner_and_settles_before_cancellation(
    verifier, tmp_path, outcome,
):
    checks = []

    def verify():
        checks.append(True)
        if len(checks) == 4:
            if outcome == "revoked":
                raise ValueError("fixture inputs revoked")
            if outcome == "cancelled":
                raise KeyboardInterrupt

    with verifier.LiveInputVerifier(verify, deadline=time.monotonic() + 15) as guard:
        process = _launch(guard, verifier, tmp_path)
        if outcome == "cancelled":
            with pytest.raises(KeyboardInterrupt):
                guard.wait(process)
        else:
            guard.wait(process)
        assert process.poll() == 0, (tmp_path / "stderr").read_text()
        assert (tmp_path / "result").read_text() == (
            "published" if outcome == "accepted" else "refused"
        )
        assert len(checks) == 4
        read, write = guard.requests, guard.replies
    for descriptor in (read, write):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(verifier.VerificationRefused, match="scope is closed"):
        guard.check()


def test_closed_native_owner_cannot_be_replaced_by_its_retained_identity(verifier, tmp_path):
    with verifier.LiveInputVerifier(lambda: None, deadline=time.monotonic() + 15) as guard:
        process = _launch(guard, verifier, tmp_path)
    process.wait(timeout=15)
    assert process.returncode == 0, (tmp_path / "stderr").read_text()
    assert (tmp_path / "result").read_text() == "refused"


@pytest.mark.timeout(10)
def test_idle_poll_expiry_cannot_discard_a_started_request(verifier, tmp_path, monkeypatch):
    offset = [0.0]
    checks = []
    actual_read = os.read
    with verifier.LiveInputVerifier(
        lambda: checks.append(True), deadline=time.monotonic() + 3,
    ) as guard:
        def delayed_read(descriptor, count):
            content = actual_read(descriptor, count)
            if descriptor == guard.requests and content and not offset[0]:
                offset[0] = 0.2
            return content

        monkeypatch.setattr(verifier, "time", SimpleNamespace(
            monotonic=lambda: time.monotonic() + offset[0], sleep=time.sleep,
        ))
        monkeypatch.setattr(os, "read", delayed_read)
        process = _launch(guard, verifier, tmp_path)
        guard.wait(process)
        assert process.poll() == 0, (tmp_path / "stderr").read_text()
        assert (tmp_path / "result").read_text() == "published"
        assert len(checks) == 4
        assert offset[0] == 0.2


def test_native_child_refuses_a_mismatched_verification_scope(verifier, tmp_path):
    checks = []
    with verifier.LiveInputVerifier(
        lambda: checks.append(True), deadline=time.monotonic() + 15,
    ) as guard:
        other = {**guard.identity, "id": "0" * 32}
        process = _launch(guard, verifier, tmp_path, identity=other)
        guard.wait(process)
    assert process.returncode == 0, (tmp_path / "stderr").read_text()
    assert (tmp_path / "result").read_text() == "refused"
    assert len(checks) == 2


def test_expired_input_scope_refuses_before_native_launch(verifier):
    with pytest.raises(verifier.VerificationRefused, match="deadline"):
        with verifier.LiveInputVerifier(lambda: pytest.fail("expired verification"), deadline=0):
            pytest.fail("expired scope entered")


@pytest.mark.parametrize("declaration", ["", "INPUT_VERIFIER_ABI = 0", "INPUT_VERIFIER_ABI = True"])
def test_old_source_cannot_silently_discard_required_input_verification(
    verifier, tmp_path, declaration,
):
    candidate = tmp_path / "src" / "agent_worktrees" / "runtime_candidate.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(declaration + "\n", encoding="utf-8")
    with pytest.raises(verifier.VerificationRefused, match="does not support"):
        verifier.require_source_contract(tmp_path)
