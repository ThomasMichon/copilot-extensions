"""Static structural tests for the `prune` / -Mode prune bulk-cleanup command
in run.sh / run.ps1.

Docker is not assumed to be available wherever these tests run, so these are
deliberately structural (text-based) checks rather than a live container run.
A live end-to-end proof was performed manually against real leftover
cr-base-*/cr-pristine containers (see the effort notes); these tests guard
the wiring so it can't silently regress.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUN_SH = ROOT / "tools" / "clean-room" / "run.sh"
RUN_PS1 = ROOT / "tools" / "clean-room" / "run.ps1"

CLEAN_ROOM_LABEL = "copilot-extensions.clean-room=1"


def test_run_sh_accepts_prune_mode() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert "|prune|" in text, "prune must be a recognized MODE token"
    assert "prune) do_prune ;;" in text


def test_run_sh_labels_every_container_it_creates() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert f'CLEAN_ROOM_LABEL="{CLEAN_ROOM_LABEL}"' in text
    # The scenario container and the short-lived auth login box both carry it.
    assert '--label "$CLEAN_ROOM_LABEL"' in text
    idx_auth = text.index("docker run -it --name cr-auth")
    assert "--label" in text[idx_auth : idx_auth + 200]
    idx_main = text.index('docker run -d --name "$CONTAINER"')
    assert "--label" in text[idx_main : idx_main + 200]


def test_run_sh_prune_falls_back_to_name_prefix_for_legacy_containers() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index("do_prune() {")
    block = text[idx : idx + 1400]
    assert '--filter "label=$CLEAN_ROOM_LABEL"' in block
    assert "--filter 'name=^cr-'" in block


def test_run_sh_prune_does_not_touch_unrelated_containers() -> None:
    # Regression guard: prune must filter by label/name-prefix, never by a
    # bare `docker ps -a -q` (which would sweep every container on the box,
    # including unrelated fleets like agent-containers').
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index("do_prune() {")
    end = text.index("\n}\n", idx)
    block = text[idx:end]
    assert "docker ps -a -q\n" not in block
    assert "docker ps -a -q " not in block or "--filter" in block


def test_run_ps1_accepts_prune_mode() -> None:
    text = RUN_PS1.read_text(encoding="utf-8")
    assert "'down','prune'," in text, "prune must be added to the -Mode ValidateSet"
    assert "'prune' { Invoke-Prune }" in text


def test_run_ps1_labels_every_container_it_creates() -> None:
    text = RUN_PS1.read_text(encoding="utf-8")
    assert f"$CleanRoomLabel = '{CLEAN_ROOM_LABEL}'" in text
    idx_auth = text.index("docker run -it --name cr-auth")
    assert "--label $CleanRoomLabel" in text[idx_auth : idx_auth + 200]
    idx_main = text.index("docker run -d --name $Container")
    assert "--label $CleanRoomLabel" in text[idx_main : idx_main + 200]


def test_run_ps1_prune_falls_back_to_name_prefix_for_legacy_containers() -> None:
    text = RUN_PS1.read_text(encoding="utf-8")
    idx = text.index("function Invoke-Prune {")
    block = text[idx : idx + 1600]
    assert 'label=$CleanRoomLabel' in block
    assert "name=^cr-" in block


def test_both_wrappers_agree_on_the_label() -> None:
    sh_text = RUN_SH.read_text(encoding="utf-8")
    ps1_text = RUN_PS1.read_text(encoding="utf-8")
    assert CLEAN_ROOM_LABEL in sh_text
    assert CLEAN_ROOM_LABEL in ps1_text
