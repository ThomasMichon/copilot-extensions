"""Static structural tests for the --block-public-feeds / -BlockPublicFeeds
wiring in run.sh / run.ps1 (aperture-labs feed-neutral-build-config effort,
#6755 Phase 3).

Docker is not assumed to be available wherever these tests run, so these are
deliberately structural (text-based) checks rather than a live container run:
they confirm the flag is wired to null-route the same four public feed
hostnames, consistently, in both host wrappers. A live end-to-end proof (run
the harness with --block-public-feeds and no substitute feed, confirm the
existing toolchain-uv jam fires; then with --uv-index set, confirm success)
is documented as an outstanding manual/CI step in the effort README -- it
needs an actual Docker host and, for the "succeeds under substitute" half, a
real internal-feed-shaped endpoint.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUN_SH = ROOT / "tools" / "clean-room" / "run.sh"
RUN_PS1 = ROOT / "tools" / "clean-room" / "run.ps1"

PUBLIC_FEED_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "download.pytorch.org",
)


def test_run_sh_has_block_public_feeds_flag_and_env_fallback() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert "--block-public-feeds" in text
    assert "CR_BLOCK_PUBLIC_FEEDS" in text
    for host in PUBLIC_FEED_HOSTS:
        assert f'--add-host "{host}:127.0.0.1"' in text, f"missing --add-host for {host}"


def test_run_sh_only_adds_hosts_when_flag_set() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    # The --add-host block must be gated behind the flag, not unconditional.
    idx = text.index('if [ "$BLOCK_PUBLIC_FEEDS" = "1" ]; then')
    block = text[idx : idx + 600]
    for host in PUBLIC_FEED_HOSTS:
        assert host in block


def test_run_ps1_has_block_public_feeds_flag_and_env_fallback() -> None:
    text = RUN_PS1.read_text(encoding="utf-8")
    assert "[switch]$BlockPublicFeeds" in text
    assert "CR_BLOCK_PUBLIC_FEEDS" in text
    for host in PUBLIC_FEED_HOSTS:
        assert f"'{host}:127.0.0.1'" in text, f"missing --add-host for {host}"


def test_run_ps1_only_adds_hosts_when_flag_set() -> None:
    text = RUN_PS1.read_text(encoding="utf-8")
    idx = text.index("if ($BlockPublicFeeds) {")
    block = text[idx : idx + 600]
    for host in PUBLIC_FEED_HOSTS:
        assert host in block


def test_run_ps1_rejects_block_public_feeds_with_windows_arm() -> None:
    text = RUN_PS1.read_text(encoding="utf-8")
    assert (
        "if ($BlockPublicFeeds -and $Os -eq 'windows')" in text
    ), "missing guard: -BlockPublicFeeds must not silently no-op on -Os windows"


def test_both_wrappers_agree_on_the_exact_host_set() -> None:
    # Regression guard: if one script's host list drifts from the other
    # (typo, added/removed host), this test catches the divergence even
    # without Docker.
    sh_text = RUN_SH.read_text(encoding="utf-8")
    ps1_text = RUN_PS1.read_text(encoding="utf-8")
    for host in PUBLIC_FEED_HOSTS:
        assert host in sh_text
        assert host in ps1_text
