"""Real finite production work settles without a test lease or application GC."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PRODUCER = r"""
import dataclasses, json, os, sys
from pathlib import Path
from agent_procutil import daemon_instances as api

kind, runtime, registry = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
runtime.mkdir(mode=0o700, parents=True)
artifact = runtime / "production-output.txt"
payload = (
    "from pathlib import Path; import sys; "
    "from agent_procutil.daemon_instances import admit_daemon_instance; "
    "p=Path(sys.argv[1]); p.write_text('production output', encoding='utf-8'); "
    "g=admit_daemon_instance('finite-payload'); g.add_artifact(p); g.close()"
)
processes = []
real = api.spawn_finite_worker

def launch(argv, **options):
    spec = options["instance"]
    assert spec.lifecycle == "finite-worker"
    options["instance"] = dataclasses.replace(spec, registry_root=registry)
    process = real([sys.executable, "-c", payload, str(artifact)], **options)
    processes.append(process)
    return process

if kind == "manager":
    from worktree_manager import mux_callback
    caller = runtime / "caller"
    caller.mkdir()
    os.chdir(caller)
    os.environ["WORKTREE_MANAGER_ROOT"] = ".."
    mux_callback.spawn_finite_worker = launch
    assert mux_callback.main(["register"]) == 0
else:
    from agent_dispatch import coordinator_loops, procutil
    procutil.runtime_root = lambda: runtime
    procutil.spawn_finite_worker = launch
    coordinator_loops._spawn_self_deploy(sys.executable)

assert len(processes) == 1
process = processes[0]
assert process.wait(timeout=30) == 0
receipt = process.instance_receipt
row = next(item for item in api.inspect_instances(registry)
           if item["instance_id"] == receipt.instance_id)
assert row["purpose"] == "production", row
assert row["lifecycle"] == "finite-worker", row
assert row["state"] == "finished", row
assert row.get("deadline") in (None, "", "0", "0.0"), row
result = api.cleanup_instance(receipt)
assert result.status == "settled", result
assert not receipt.record_path.exists()
assert artifact.read_text(encoding="utf-8") == "production output"
print(json.dumps({"caller": kind, "lifecycle": row["lifecycle"],
                  "state": row["state"], "cleanup": result.status}))
"""


@pytest.mark.parametrize("caller", ["manager", "dispatch"])
def test_production_finite_callers_finish_and_retire_only_control_metadata(tmp_path, caller):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    for name in (
        "COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT",
        "COPILOT_EXTENSIONS_DAEMON_INSTANCE",
        "COPILOT_EXTENSIONS_DAEMON_REGISTRY",
    ):
        environment.pop(name, None)
    environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    paths = [
        root / "worktree-manager" / "src",
        root / "plugins" / "agent-dispatch" / "src",
        *sorted((root / "libs").glob("*/src")),
    ]
    environment["PYTHONPATH"] = os.pathsep.join(map(str, paths))
    result = subprocess.run(
        [
            sys.executable, "-B", "-c", PRODUCER, caller,
            str(tmp_path / "runtime"), str(tmp_path / "registry"),
        ],
        env=environment, capture_output=True, text=True, timeout=45,
        **(
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt" else {}
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "finished"
