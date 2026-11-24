"""Stage the actual packaged native service closure without installing a service."""
from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import sys

PLUGIN = Path(__file__).resolve().parents[1]
REPOSITORY = PLUGIN.parents[1]


def stage_service_launchers(destination: Path) -> None:
    payload = destination.parent / ".service-launcher-payload"
    member = Path("src/agent_dispatch/service_launchers")
    shutil.copytree(PLUGIN / member, payload / member)
    sys.path.insert(0, str(REPOSITORY / "tools"))
    materializer = importlib.import_module("materialize_launch_wrapper_assets")
    report = materializer.materialize_into(
        source_consumer_dir=PLUGIN, dest_consumer_dir=payload,
        canonical_root=REPOSITORY, dest_root=payload,
    )
    assert report and not any(line.startswith("SKIP") for line in report), report
    shutil.copytree(payload / member, destination)
