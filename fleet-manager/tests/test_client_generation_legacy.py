"""An already-running legacy writer resumes at the actual copy-producer boundary."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager import client_admission as admission, client_generations as generations
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate
from test_client_admission import database_bytes, identity, selection
from test_client_construction import (
    built_client as built_client, signed_wheel as signed_wheel, target,  # noqa: F401
)
from test_client_distribution import FLAGS
from test_client_frontdoor import command_directory, register, states
from test_client_generation_construction import construct_generation, direct_health
from test_client_generation_frontdoor import publish, pointer
from test_client_generations import FIRST, lineage_at, state


_DELAYED_LEGACY = """
from contextlib import contextmanager
import json,sys
from pathlib import Path
from fleet_manager import artifact_staging, client_frontdoor
from fleet_manager.planning import PlanError, canonical
value=json.loads(sys.stdin.readline())
original=artifact_staging._primitive
@contextmanager
def producer():
    with original() as primitive:
        publication=primitive._runtime_component("versioned_runtime_publication")
        publish=publication.CopyPublication.publish
        def delayed(copy):
            print("LEGACY_WRITER_BEFORE_REAL_COPY",flush=True)
            if sys.stdin.readline() != "\\n":
                raise RuntimeError("legacy release barrier missing")
            return publish(copy)
        publication.CopyPublication.publish=delayed
        yield primitive
artifact_staging._primitive=producer
with artifact_staging.stage_authenticated_artifacts(
    canonical(value["manifest"]),value["proof"],value["policy"],value["sources"],
    authority=value["identity"]["authority"],installation=value["identity"]["installation"],
    role="client",staging_parent=Path(value["stage"]),clock=lambda:150,
) as staged:
    try:
        client_frontdoor.register_client_frontdoor(
            Path(value["root"]),value["identity"],value["selection"],staged,
            command_directory=Path(value["directory"]),
            current_policy=lambda:value["policy"],current_target=lambda:value["target"],
            clock=lambda:150,
        )
    except PlanError as error:
        print(json.dumps({"refused":str(error)}),flush=True)
    else:
        raise AssertionError("legacy writer unexpectedly published after successor")
"""


@pytest.fixture(scope="module", params=[False, True], ids=["unregistered", "registered"])
def legacy_ready(tmp_path_factory, built_client, request):
    legacy, fixture, staged, trusted, user, _floor, _report = built_client
    area = tmp_path_factory.mktemp("l")
    directory = command_directory(area)
    activate(legacy, fixture, staged, trusted)
    if request.param:
        register(legacy, fixture, staged, trusted, directory)
    return legacy, fixture, staged, trusted, user, area, directory, request.param


@pytest.mark.timeout(90)
def test_legacy_installed_default_commands_prerequisite(legacy_ready):
    legacy, _fixture, _staged, _trusted, user, _area, directory, registered = legacy_ready
    assert legacy.is_dir() and user.read_bytes() == b"preserved configuration\n"
    assert bool(states(list(directory.glob("fleet-manager*")))) is registered


@pytest.mark.timeout(90)
def test_already_running_legacy_writer_cannot_republish_over_forward_selected_commands(
    primitive, legacy_ready, monkeypatch,
):
    legacy, fixture, staged, trusted, user, area, directory, registered = legacy_ready
    before = database_bytes(legacy)
    stage = command_directory(area, "delayed-stage")
    changed_target = {**target(fixture), "revision": target(fixture)["revision"] + 1}
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _DELAYED_LEGACY], cwd=area,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        process.stdin.write(json.dumps({
            **fixture, "policy": trusted, "root": str(legacy), "identity": identity(),
            "selection": selection(fixture), "target": changed_target,
            "stage": str(stage), "directory": str(directory),
        }) + "\n")
        process.stdin.flush()
        barrier = process.stdout.readline()
        assert barrier.strip() == "LEGACY_WRITER_BEFORE_REAL_COPY", (
            barrier, process.stderr.read() if process.poll() is not None else process.poll(),
        )
        root = lineage_at(area)
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
        first = construct_generation(root, fixture, staged, trusted)
        installation = root, directory, fixture, staged, trusted, first
        if registered:
            original_objects = states(list(directory.glob("fleet-manager*")))
            commit = admission._commit

            def interrupted(connection):
                value = connection.execute("SELECT publication FROM lineage").fetchone()[0]
                commit(connection)
                if value is not None:
                    raise OSError("injected first migration intent acknowledgement")

            with monkeypatch.context() as injected:
                injected.setattr(admission, "_commit", interrupted)
                with pytest.raises(PlanError, match="outcome is unknown"):
                    publish(installation)
            assert pointer(root)[0] is None and pointer(root)[2] is not None
            assert states([Path(path) for path in original_objects]) == original_objects
        assert publish(installation)["selected"]
        selected = pointer(root)
        commands = states(list(directory.glob("fleet-manager*")))
        assert state(root)[0]["selection"] == selection(fixture)
        assert process.poll() is None and database_bytes(legacy) == before
        stdout, stderr = process.communicate("\n", timeout=45)
        assert process.returncode == 0, stderr
        refusal = json.loads(stdout)
        expected = "producer-created command object" if registered else "gained foreign commands"
        assert expected in refusal["refused"], refusal
        assert pointer(root) == selected and state(root)[2][FIRST]["selection"] == selection(fixture)
        assert states([Path(path) for path in commands]) == commands
        assert database_bytes(legacy) == before and user.read_bytes() == b"preserved configuration\n"
        direct_health(first, area)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
