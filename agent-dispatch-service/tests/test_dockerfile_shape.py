"""Static shape checks for the container image definition.

These do not build or run the image (no Docker dependency in ordinary CI) --
they assert the Dockerfile's structural safety contract: no hardcoded
unauthenticated LAN bind, the coordinator runs directly as the entrypoint
(no systemd/service-manager layer), self-update is disabled, and a
non-editable install is used so the final stage is self-contained.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_text() -> str:
    return (PACKAGE_ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_entrypoint_runs_coordinator_directly() -> None:
    text = _dockerfile_text()
    assert 'ENTRYPOINT ["python", "-m", "agent_dispatch", "serve"]' in text
    # No systemd/Scheduled Task layer inside the container.
    assert "systemd" not in text.lower() or "no systemd" in text.lower()


def test_no_hardcoded_unauthenticated_lan_bind() -> None:
    text = _dockerfile_text()
    # The image must not default AGENT_DISPATCH_HOST to 0.0.0.0 -- the
    # coordinator itself refuses that bind without a token, and this image
    # must not default to a shape that pairs 0.0.0.0 with no token. Comments
    # documenting the opt-in pattern (an explicit `-e` at `docker run` time)
    # are fine; an actual ENV default is not.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "AGENT_DISPATCH_HOST=0.0.0.0" not in stripped


def test_self_update_disabled() -> None:
    text = _dockerfile_text()
    assert "AGENT_DISPATCH_SELF_UPDATE=0" in text


def test_non_editable_install_for_self_contained_final_stage() -> None:
    text = _dockerfile_text()
    assert "--no-editable" in text


def test_build_context_is_repo_root_documented() -> None:
    text = _dockerfile_text()
    assert "repository root" in text.lower() or "repo root" in text.lower()


def test_package_index_override_defaults_to_public_pypi() -> None:
    """The committed default must stay public PyPI -- this is a public repo;
    a managed-feed URL may only be supplied via --build-arg, never baked in
    as the default."""
    text = _dockerfile_text()
    assert "ARG PYTHON_INDEX_URL=https://pypi.org/simple/" in text
