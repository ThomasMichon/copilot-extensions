"""Tests for versioned_runtime.py's ``invalidate`` completion-marker removal.

A slot's completion marker can be written by an older/buggier installer
whose health gate wasn't strict enough (dotfiles #7561): a later, stricter
gate can prove the slot's payload is actually broken even though
``is_complete`` still reports it healthy. ``invalidate`` removes just the
marker (never the slot's other files) so a future run stops trusting it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

# versioned_runtime.py is deliberately stdlib-only and kept OUT of every
# runtime venv (its own module docstring), so it's not a package module --
# load it by path, same as the other scripts/ helpers under test.
_VR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"
_spec = importlib.util.spec_from_file_location("versioned_runtime", _VR_PATH)
assert _spec and _spec.loader, f"cannot load {_VR_PATH}"
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)


def test_invalidate_removes_an_existing_marker(tmp_path: Path) -> None:
    vr.mark_complete(tmp_path, "1.2.3")
    assert vr.is_complete(tmp_path, "1.2.3")

    removed = vr.invalidate(tmp_path, "1.2.3")

    assert removed is True
    assert not vr.marker_path(tmp_path, "1.2.3").exists()
    assert not vr.is_complete(tmp_path, "1.2.3")


def test_invalidate_is_a_noop_when_no_marker_exists(tmp_path: Path) -> None:
    # versions/<version> doesn't even exist yet -- must not raise or create it.
    removed = vr.invalidate(tmp_path, "9.9.9")

    assert removed is False
    assert not vr.version_dir(tmp_path, "9.9.9").exists()


def test_invalidate_leaves_the_slots_other_files_untouched(tmp_path: Path) -> None:
    vr.mark_complete(tmp_path, "1.2.3", payload_hash="deadbeef")
    slot = vr.version_dir(tmp_path, "1.2.3")
    sentinel = slot / "Scripts" / "python.exe"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"not a real interpreter, just a sentinel")

    vr.invalidate(tmp_path, "1.2.3")

    assert sentinel.exists(), "invalidate must remove ONLY the completion marker"


def test_invalidated_slot_falls_back_to_last_known_good_on_resolve(
    tmp_path: Path,
) -> None:
    """Mirrors the real recovery path: a bad slot gets activated once (by an
    old/buggy gate), a good slot follows it, then the bad one is invalidated
    -- resolve_python must land on the good slot, not the invalidated one.
    """
    good, bad = "1.0.0", "1.0.1"
    for version in (good, bad):
        (vr.version_dir(tmp_path, version) / "bin").mkdir(parents=True)
        (vr.version_dir(tmp_path, version) / "bin" / "python").write_bytes(b"")
        vr.mark_complete(tmp_path, version)

    vr.activate(tmp_path, good, link_free=True)
    vr.activate(tmp_path, bad, link_free=True)
    assert vr.resolve_python(tmp_path) == vr.slot_python(tmp_path, bad)

    vr.invalidate(tmp_path, bad)

    # current-version still names the bad slot (invalidate doesn't touch it),
    # but it's no longer "complete", so resolution must fall through the
    # tiered fallback (current -> last-known-good -> newest complete slot)
    # to the still-healthy good slot instead of staying stuck on the bad one.
    assert vr.resolve_python(tmp_path) == vr.slot_python(tmp_path, good)


def test_cli_invalidate_subcommand(tmp_path: Path) -> None:
    vr.mark_complete(tmp_path, "2.0.0")
    assert vr.is_complete(tmp_path, "2.0.0")

    result = subprocess.run(
        [
            sys.executable,
            str(_VR_PATH),
            "--root",
            str(tmp_path),
            "--json",
            "invalidate",
            "2.0.0",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload == {"version": "2.0.0", "invalidated": True}
    assert not vr.is_complete(tmp_path, "2.0.0")
