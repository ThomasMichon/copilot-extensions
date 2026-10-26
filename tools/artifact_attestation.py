"""Authenticate immutable artifact subjects without importing publication tools."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from artifact_source_build import PromotionArtifactError

INDEX_NAME = "python-artifacts.json"
INDEX_SCHEMA = "copilot-extensions.promotion-python-artifacts"


def _gh(
    argv: list[str], *, verification: bool = False, binary: bool = False,
    timeout: int = 120,
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
    _check((runner or _gh)([
        "attestation", "verify", str(path), "--repo", repository,
        "--signer-workflow", f"{repository}/.github/workflows/validate-and-promote.yml",
        "--signer-digest", signer_digest, "--source-digest", source_digest,
        "--source-ref", source_ref, "--deny-self-hosted-runners",
    ], verification=True))
