"""A real independent publisher retains targets, never the central writer, during health."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import identity
from test_client_construction import signed_wheel as signed_wheel, target  # noqa: F401
from test_client_distribution import FLAGS
from test_client_frontdoor import states
from test_client_generation_construction import construct_generation, direct_health
from test_client_generation_frontdoor import installation as installation, pointer, publish  # noqa: F401
from test_client_generations import FIRST, SECOND, state


_DELAYED_PUBLISHER = """
import json,sys
from pathlib import Path
from fleet_manager import artifact_staging, client_frontdoor, client_generation_frontdoor
from fleet_manager.planning import PlanError, canonical
value=json.loads(sys.stdin.readline())
actual=client_frontdoor._health
def delayed(*args,**kwargs):
    actual(*args,**kwargs)
    print("REAL_DEFAULT_HEALTH_BEFORE_ACK",flush=True)
    if sys.stdin.readline() != "\\n":
        raise RuntimeError("publisher release barrier missing")
client_frontdoor._health=delayed
with artifact_staging.stage_authenticated_artifacts(
    canonical(value["manifest"]),value["proof"],value["policy"],value["sources"],
    authority=value["identity"]["authority"],installation=value["identity"]["installation"],
    role="client",staging_parent=Path(value["stage"]),clock=lambda:150,
) as staged:
    try:
        client_generation_frontdoor.publish_client_generation(
            Path(value["root"]),value["identity"],value["reservation"],staged,
            command_directory=Path(value["directory"]),
            current_policy=lambda:value["policy"],current_target=lambda:value["target"],
            clock=lambda:150,
        )
    except PlanError as error:
        print(json.dumps({"refused":str(error)}),flush=True)
    else:
        raise AssertionError("superseded independent publisher unexpectedly acknowledged")
"""


@pytest.mark.timeout(90)
def test_independent_publisher_cannot_ack_or_rollback_after_successor_constructs(
    primitive, installation,
):
    root, directory, fixture, staged, trusted, first = installation
    area = directory.parent
    stage = area / "publisher-stage"
    stage.mkdir(mode=0o700)
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _DELAYED_PUBLISHER], cwd=area,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        process.stdin.write(json.dumps({
            **fixture, "policy": trusted, "root": str(root), "identity": identity(),
            "reservation": state(root)[2][FIRST], "target": target(fixture),
            "stage": str(stage), "directory": str(directory),
        }) + "\n")
        process.stdin.flush()
        barrier = process.stdout.readline()
        assert barrier.strip() == "REAL_DEFAULT_HEALTH_BEFORE_ACK", (
            barrier, process.stderr.read() if process.poll() is not None else process.poll(),
        )
        pending = pointer(root)
        assert pending[:2] == (None, None) and pending[2] is not None
        commands = states(list(directory.glob("fleet-manager*")))
        second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
        assert state(root, fences=True)[3]["current_generation"] == SECOND
        assert pointer(root) == pending and process.poll() is None
        direct_health(first, area)
        direct_health(second, area)
        stdout, stderr = process.communicate("\n", timeout=45)
        assert process.returncode == 0, stderr
        assert "outcome is unknown" in json.loads(stdout)["refused"]
        assert pointer(root) == pending and states([Path(path) for path in commands]) == commands
        assert publish(installation, SECOND)["selected"]
        assert pointer(root)[0] == SECOND
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
