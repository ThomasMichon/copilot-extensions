"""Authenticate immutable artifact subjects without importing publication tools."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import time

from artifact_source_build import PromotionArtifactError

INDEX_NAME = "python-artifacts.json"
INDEX_SCHEMA = "copilot-extensions.promotion-python-artifacts"
INDEX_LIMIT = 1 << 20
_ATTESTATION_TIMEOUT = 120
_VERIFIER_INIT_ERRORS = {
    "Error: failed to choose verifier based on provided bundle issuer: "
    "public good verifier is not available (initialization may have failed)",
    "error creating Sigstore verifier: no valid Sigstore verifiers could be initialized",
}


def _gh(
    argv: list[str], *, verification: bool = False, binary: bool = False,
    timeout: float = _ATTESTATION_TIMEOUT,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if verification and "ARTIFACT_VERIFY_TOKEN" in env:
        env["GH_TOKEN"] = env["ARTIFACT_VERIFY_TOKEN"]
    env.pop("ARTIFACT_VERIFY_TOKEN", None)
    return subprocess.run(
        ["gh", *argv], capture_output=True, text=not binary, env=env, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _check(result: subprocess.CompletedProcess) -> str:
    if result.returncode:
        raise PromotionArtifactError(
            f"gh failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def verify_attestation_subject(
    path: Path, *, repository: str, signer_digest: str, source_digest: str,
    source_ref: str, runner=None,
) -> None:
    """Authenticate one immutable subject against the pinned hosted producer."""
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not re.fullmatch(r"[0-9a-f]{40}", signer_digest)
        or not re.fullmatch(r"[0-9a-f]{40}", source_digest)
        or not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", source_ref)
    ):
        raise PromotionArtifactError("invalid expected attestation identity")
    if path.is_symlink() or not path.is_file():
        raise PromotionArtifactError(f"{path.name}: unsafe attestation subject")
    argv = [
        "attestation", "verify", str(path), "--repo", repository,
        "--signer-workflow", f"{repository}/.github/workflows/validate-and-promote.yml",
        "--signer-digest", signer_digest, "--source-digest", source_digest,
        "--source-ref", source_ref, "--deny-self-hosted-runners",
    ]
    invoke = runner or _gh
    deadline = time.monotonic() + _ATTESTATION_TIMEOUT
    result = invoke(argv, verification=True)
    error_lines = result.stderr.strip().splitlines()
    if result.returncode == 1 and error_lines and error_lines[-1] in _VERIFIER_INIT_ERRORS:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            print(
                "gh attestation: Sigstore verifier initialization unavailable; "
                "retrying once with unchanged policy and remaining deadline",
                file=sys.stderr,
            )
            result = invoke(argv, verification=True, timeout=remaining)
    _check(result)
