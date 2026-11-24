"""Compose declared Logger sync support through the real publication owner."""
from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import sys

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parents[1]


def compose_sync_scripts(destination: Path) -> Path:
    scripts = destination / "scripts"
    scripts.mkdir(parents=True)
    for name in ("resolve-runtime.ps1", "logger-sync.ps1", "logger-sync-publication.ps1"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    sys.path.insert(0, str(ROOT / "tools"))
    materializer = importlib.import_module("materialize_installer_engine")
    names = frozenset({"bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe"})
    log = materializer.materialize_derived_files(
        destination, canonical_root=ROOT, copied_consumer=(PLUGIN, destination), file_names=names,
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    assert all((scripts / name).is_file() for name in names)
    return scripts
