"""Bounded recovery of the CLI's unavailable Public Good verifier."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

import publish_python_artifacts as publisher


INIT_ERROR = (
    "Error: failed to choose verifier based on provided bundle issuer: "
    "public good verifier is not available (initialization may have failed)"
)
IDENTITY = {
    "repository": "example/plugins",
    "signer_digest": "a" * 40,
    "source_digest": "b" * 40,
    "source_ref": "refs/heads/main",
}


@pytest.fixture
def subject(tmp_path: Path) -> Path:
    path = tmp_path / "python-artifacts.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _result(stderr: str = "", returncode: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["gh"], returncode, "", stderr)


@pytest.mark.parametrize("stderr", [INIT_ERROR, "Loaded 1 attestation\n\n" + INIT_ERROR + "\n"])
def test_init_failure_retries_identical_policy_with_remaining_deadline(
    subject: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    stderr: str,
) -> None:
    gh = Mock(side_effect=[_result(stderr), _result(returncode=0)])
    monkeypatch.setattr(publisher, "_gh", gh)
    monkeypatch.setattr(publisher.time, "monotonic", Mock(side_effect=[10.0, 27.5]))

    publisher.verify_attestation_subject(subject, **IDENTITY)

    assert gh.call_count == 2
    first, retry = gh.call_args_list
    assert first.args == retry.args
    assert first.kwargs == {"verification": True}
    assert retry.kwargs == {"verification": True, "timeout": 102.5}
    argv = first.args[0]
    assert argv[:3] == ["attestation", "verify", str(subject)]
    for flag, expected in (
        ("--repo", IDENTITY["repository"]),
        ("--signer-workflow", "example/plugins/.github/workflows/validate-and-promote.yml"),
        ("--signer-digest", IDENTITY["signer_digest"]),
        ("--source-digest", IDENTITY["source_digest"]),
        ("--source-ref", IDENTITY["source_ref"]),
    ):
        assert argv[argv.index(flag) + 1] == expected
    assert "--deny-self-hosted-runners" in argv
    assert "retrying once" in capsys.readouterr().err


@pytest.mark.parametrize("stderr,returncode", [
    ("bad signer", 1),
    ("signature verification failed", 1),
    ("source digest mismatch", 1),
    ("self-hosted runner rejected", 1),
    ("unsupported bundle issuer", 1),
    ("public good verifier is not available", 1),
    (INIT_ERROR + " with another failure", 1),
    (INIT_ERROR + "\nsignature verification failed", 1),
    (INIT_ERROR, 2),
])
def test_other_failures_never_retry(
    subject: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    stderr: str, returncode: int,
) -> None:
    gh = Mock(return_value=_result(stderr, returncode))
    monkeypatch.setattr(publisher, "_gh", gh)
    with pytest.raises(publisher.PromotionArtifactError):
        publisher.verify_attestation_subject(subject, **IDENTITY)
    assert gh.call_count == 1
    assert "retrying" not in capsys.readouterr().err


@pytest.mark.parametrize("stderr", [INIT_ERROR, "signature verification failed"])
def test_second_failure_is_fatal_without_third_attempt(
    subject: Path, monkeypatch: pytest.MonkeyPatch, stderr: str,
) -> None:
    gh = Mock(side_effect=[_result(INIT_ERROR), _result(stderr)])
    monkeypatch.setattr(publisher, "_gh", gh)
    with pytest.raises(publisher.PromotionArtifactError, match="gh failed"):
        publisher.verify_attestation_subject(subject, **IDENTITY)
    assert gh.call_count == 2


@pytest.mark.parametrize("elapsed", [120.0, 121.0])
def test_exhausted_original_deadline_never_starts_retry(
    subject: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    elapsed: float,
) -> None:
    gh = Mock(return_value=_result(INIT_ERROR))
    monkeypatch.setattr(publisher, "_gh", gh)
    monkeypatch.setattr(publisher.time, "monotonic", Mock(side_effect=[0.0, elapsed]))
    with pytest.raises(publisher.PromotionArtifactError, match="gh failed"):
        publisher.verify_attestation_subject(subject, **IDENTITY)
    assert gh.call_count == 1
    assert "retrying" not in capsys.readouterr().err


def test_success_keeps_original_single_call(
    subject: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    gh = Mock(return_value=_result(returncode=0))
    monkeypatch.setattr(publisher, "_gh", gh)
    publisher.verify_attestation_subject(subject, **IDENTITY)
    assert gh.call_count == 1
    assert gh.call_args.kwargs == {"verification": True}
    assert not capsys.readouterr().err


@pytest.mark.parametrize("initializes_first", [False, True])
def test_subprocess_timeout_is_not_retried_or_converted_to_success(
    subject: Path, monkeypatch: pytest.MonkeyPatch, initializes_first: bool,
) -> None:
    timeout = subprocess.TimeoutExpired(["gh", "attestation", "verify"], 120)
    gh = Mock(side_effect=[_result(INIT_ERROR), timeout] if initializes_first else [timeout])
    monkeypatch.setattr(publisher, "_gh", gh)
    with pytest.raises(subprocess.TimeoutExpired):
        publisher.verify_attestation_subject(subject, **IDENTITY)
    assert gh.call_count == (2 if initializes_first else 1)
