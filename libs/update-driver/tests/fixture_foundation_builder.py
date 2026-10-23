"""Fixed-builder boundary control, never evidence of actual immutable construction."""

import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time

source = Path(__file__).absolute().parent.parent
sys.path[:0] = [
    str(source / "scripts"), str(source / "src"), str(source.parent / "agent-procutil" / "src"),
    str(source.parent / "single-instance-lease" / "src"),
]
from update_driver import borrowed_custody  # noqa: E402
from update_driver.foundation_inputs import inspect  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--source-hash", required=True)
parser.add_argument("--uv", required=True)
parser.add_argument("--base-python", required=True)
parser.add_argument("--admitted-source", type=Path, required=True)
args = parser.parse_args()
with borrowed_custody(root=args.root) as custody:
    custody.assert_owned()
    assert "UPDATE_DRIVER_WITNESS" not in os.environ
    assert args.admitted_source != source
    assert inspect(source).digest == inspect(args.admitted_source).digest == args.source_hash
    if os.environ.get("FOUNDATION_FIXTURE_CHECK_UV") == "1":
        from installer_dependency_config import select_uv_executable
        import agent_procutil as pu

        selected_uv = select_uv_executable(args.uv, error_type=RuntimeError)
        expected_uv = os.environ["FOUNDATION_FIXTURE_UV_TARGET"]
        assert os.path.samefile(selected_uv, expected_uv), (args.uv, selected_uv, expected_uv)
        result = subprocess.run(
            [selected_uv, "--version"], capture_output=True, text=True,
            timeout=15, **pu.no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout.startswith("uv "), result
        (args.root / "uv-selected").write_text(selected_uv)
    progress = json.loads((args.root / "foundation-operation.json").read_text())
    assert progress["state"] == "running" and progress["source_hash"] == args.source_hash
    (args.root / "builder-started").write_text(str(os.getpid()))
    if os.environ.get("FOUNDATION_FIXTURE_HOLD") == "1":
        deadline = time.monotonic() + 60
        while not (args.root / "release-builder").exists():
            assert time.monotonic() < deadline
            custody.assert_owned()
            time.sleep(.02)
    custody.assert_owned()
    (args.root / "builder-finished").write_text("owned builder boundary returned")
raise SystemExit(int(os.environ.get("FOUNDATION_FIXTURE_EXIT", "0")))
