"""Explicit CLI requests and real installed-console generation transitions."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from fleet_manager import client_admission as admission, client_generations as generations
from fleet_manager.__main__ import main
from fleet_manager.planning import PlanError, canonical, digest
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity, independently_sign, retained, selection
from test_client_construction import signed_wheel as signed_wheel, target  # noqa: F401
from test_client_distribution import FLAGS
from test_client_generation_construction import direct_health
from test_client_generations import FIRST, SECOND, lineage_at, reserve, state
from test_release_auth import artifact_fixture


def inputs_at(area, fixture=None):
    values = {"identity": identity()}
    if fixture is not None:
        values.update({
            "selection": selection(fixture), "release": fixture["manifest"],
            "proof": fixture["proof"], "policy": fixture["policy"],
            "sources": fixture["sources"], "target": target(fixture),
        })
    paths = {}
    for name, value in values.items():
        paths[name] = area / (name + ".json")
        paths[name].write_bytes(canonical(value))
    return paths


def test_cli_explicit_initialization_inspection_and_legacy_state_remain_separate(
    tmp_path, primitive, capsys,
):
    root = lineage_at(tmp_path)
    paths = inputs_at(tmp_path)
    base = ["--root", str(root), "--identity", str(paths["identity"])]
    assert main(["client-generation", "inspect", *base]) == 1
    assert list(root.iterdir()) == []
    assert capsys.readouterr().out == ""
    assert main(["client-generation", "initialize", *base]) == 0
    capsys.readouterr()
    before = database_bytes(root)
    for command in ("client-generation", "client-state"):
        assert main([command, "inspect", *base]) == (0 if command == "client-generation" else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "fleet-manager.client-generation-inspection"
    assert report["reservations"] == {} and report["admitted"] is None
    assert report["publication"] is report["selected_generation"] is report["current_generation"] is None
    assert not report["mutations_performed"]
    assert not {"installed", "started", "healthy", "registered"} & report.keys()
    assert database_bytes(root) == before
    legacy = tmp_path / "legacy"
    legacy.mkdir(mode=0o700)
    admission.initialize_client_admission(legacy, identity())
    legacy_bytes = database_bytes(legacy)
    assert main([
        "client-generation", "initialize", *base, "--legacy-root", str(legacy),
    ]) == 1
    assert database_bytes(root) == before and database_bytes(legacy) == legacy_bytes
    fresh = tmp_path / "imported"
    fresh.mkdir(mode=0o700)
    imported = ["--root", str(fresh), "--identity", str(paths["identity"])]
    assert main([
        "client-generation", "initialize", *imported, "--legacy-root", str(legacy),
    ]) == 0
    assert generations.inspect_client_generations(fresh, identity())["origin"]["legacy"]["location"]["path"] == str(legacy)
    assert database_bytes(legacy) == legacy_bytes


@pytest.mark.parametrize("arguments", [
    ["construct"], ["construct", "--generation", FIRST], ["construct", "--request", "forward-1"],
    ["register-commands"], ["unregister-commands"], ["remove-runtime"],
    ["admit"], ["recover-retained"], ["deactivate-runtime"], ["retire-installation"],
    ["inspect", "--after", "invalid"], ["inspect", "--generation", "invalid"],
    ["inspect", "--after", FIRST, "--generation", FIRST],
])
def test_cli_missing_or_unsupported_intent_never_initializes(tmp_path, capsys, arguments):
    root = lineage_at(tmp_path)
    paths = inputs_at(tmp_path)
    assert main([
        "client-generation", *arguments, "--root", str(root), "--identity", str(paths["identity"]),
    ]) == 1
    captured = capsys.readouterr()
    assert captured.err and not captured.out and list(root.iterdir()) == []


def test_inspection_returns_exact_interrupted_request_without_runtime_or_exclusive_custody(
    tmp_path, primitive, monkeypatch,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
    before = database_bytes(root)
    names = set(root.iterdir())

    def forbidden(*args, **kwargs):
        pytest.fail("read-only inspection acquired mutation or runtime custody")

    monkeypatch.setattr(admission, "_owned", forbidden)
    monkeypatch.setattr(generations, "_generation_root", forbidden)
    report = generations.inspect_client_generations(root, identity())
    assert report["reservations"] == {FIRST: reservation}
    assert report["current_generation"] == FIRST and report["selected_generation"] is None
    assert database_bytes(root) == before and set(root.iterdir()) == names


@pytest.mark.parametrize("identity_value", [{}, None, {"authority": "wrong"}])
def test_cli_invalid_identity_is_a_reported_error(tmp_path, capsys, identity_value):
    root = lineage_at(tmp_path)
    paths = inputs_at(tmp_path)
    paths["identity"].write_bytes(canonical(identity_value))
    assert main([
        "client-generation", "inspect", "--root", str(root), "--identity", str(paths["identity"]),
    ]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("fleet-manager:") and not captured.out
    assert list(root.iterdir()) == []


def test_large_retained_history_is_paged_and_exact_request_remains_recoverable(
    tmp_path, primitive, capsys,
):
    from fleet_manager import client_generation_frontdoor

    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
    rows = []
    retired = set()
    for number in range(1, 514):
        generation = f"{number:032x}"
        previous = {**reservation, "generation": generation, "request": f"previous-{number}"}
        tombstone = {
            "schema": "fleet-manager.client-generation-retirement", "version": 1,
            "generation": generation, "request_digest": digest(previous), "retired_at": 150,
        }
        rows.append((generation, previous["request"], canonical(previous).decode(),
                     canonical(tombstone).decode()))
        retired.add(generation)
    with sqlite3.connect(root / admission.DATABASE) as connection:
        connection.executemany(
            "INSERT INTO generations VALUES (?, ?, ?, NULL, ?)", rows,
        )
    before = database_bytes(root)
    paths = inputs_at(tmp_path)
    base = ["client-generation", "inspect", "--root", str(root),
            "--identity", str(paths["identity"])]
    seen, seen_retired, after = set(), set(), None
    while True:
        assert main(base + ([] if after is None else ["--after", after])) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["reservation_count"] == 514 and not report["mutations_performed"]
        assert not seen.intersection(report["reservations"])
        if not seen:
            assert FIRST not in report["reservations"] and report["next_after"] is not None
        seen.update(report["reservations"])
        seen_retired.update(report["retired"])
        after = report["next_after"]
        if after is None:
            break
    assert len(seen) == 514 and seen_retired == retired
    assert main(base + ["--generation", FIRST]) == 0
    exact = json.loads(capsys.readouterr().out)
    assert exact["reservations"] == {FIRST: reservation} and exact["next_after"] is None
    assert main(base + ["--generation", "f" * 32]) == 1
    refused = capsys.readouterr()
    assert not refused.out and "reservation is absent" in refused.err
    directory = tmp_path / "commands"
    directory.mkdir(mode=0o700)
    targets = client_generation_frontdoor.inspect_command_targets(
        root, identity(), reservation, command_directory=directory,
    )
    assert targets["reservation_digest"] == digest(reservation)
    assert all(value is None for value in targets["targets"].values())
    assert database_bytes(root) == before


@pytest.mark.timeout(90)
def test_growing_sqlite_lineage_preserves_inspection_retry_and_retirement(tmp_path, primitive, capsys):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    paths = inputs_at(tmp_path)
    base = ["client-generation", "inspect", "--root", str(root),
            "--identity", str(paths["identity"]), "--generation"]
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        rows = []
        for number in range(1, 8001):
            generation = f"{number:032x}"
            request = f"history-{number:06d}-" + "x" * 100
            previous = {**first, "generation": generation, "request": request}
            rows.append((generation, request, canonical(previous).decode()))
        with sqlite3.connect(root / admission.DATABASE) as connection:
            connection.executemany("INSERT INTO generations VALUES (?, ?, ?, NULL, NULL)", rows)
        assert (root / admission.DATABASE).stat().st_size > 4 * 1024 * 1024
        assert main(base + [FIRST]) == 0
        assert json.loads(capsys.readouterr().out)["reservations"] == {FIRST: first}
        second = reserve(root, fixture, staged, trusted, generation=SECOND, request="large-retry")
        before = database_bytes(root)
        assert reserve(
            root, fixture, staged, trusted, generation=SECOND, request="large-retry",
        ) == second
        assert database_bytes(root) == before
        tombstone = generations._retire_generation(root, identity(), first, clock=lambda: 151)
        assert main(base + [FIRST]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["retired"][FIRST] == tombstone and report["current_generation"] == SECOND
    metadata = root / "bounded-metadata"
    with metadata.open("xb") as stream:
        os.chmod(metadata, 0o600)
        stream.truncate(4 * 1024 * 1024 + 1)
    with pytest.raises(PlanError, match="byte bound"):
        admission._ordinary_file(metadata)


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_command_target_inspection_reports_nonordinary_targets_without_traceback(
    tmp_path, primitive, capsys, kind,
):
    from fleet_manager import client_frontdoor

    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
    paths = inputs_at(tmp_path)
    saved = tmp_path / "reservation.json"
    saved.write_bytes(canonical(reservation))
    directory = tmp_path / "commands"
    directory.mkdir(mode=0o700)
    command = client_frontdoor._command_paths(directory)[0]
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"preserved target")
    if kind == "directory":
        command.mkdir(mode=0o700)
    else:
        try:
            command.symlink_to(foreign)
        except OSError:
            pytest.skip("native symlink creation is unavailable")
    before = database_bytes(root)
    assert main([
        "client-generation", "inspect-command-targets", "--root", str(root),
        "--identity", str(paths["identity"]), "--reservation", str(saved),
        "--command-directory", str(directory),
    ]) == 1
    refused = capsys.readouterr()
    assert not refused.out and refused.err.startswith("fleet-manager:")
    assert "Traceback" not in refused.err
    assert foreign.read_bytes() == b"preserved target" and database_bytes(root) == before


@pytest.mark.parametrize("boundary", ["lease", "read"])
def test_command_target_inspection_normalizes_native_failures(
    tmp_path, primitive, monkeypatch, capsys, boundary,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
    paths = inputs_at(tmp_path)
    saved = tmp_path / "reservation.json"
    saved.write_bytes(canonical(reservation))
    directory = tmp_path / "commands"
    directory.mkdir(mode=0o700)
    publication = primitive._runtime_component("versioned_runtime_publication")

    def unavailable(*_args, **_kwargs):
        raise OSError("private injected native failure")

    monkeypatch.setattr(publication, "target_leases" if boundary == "lease" else "state_path", unavailable)
    before = database_bytes(root)
    assert main([
        "client-generation", "inspect-command-targets", "--root", str(root),
        "--identity", str(paths["identity"]), "--reservation", str(saved),
        "--command-directory", str(directory),
    ]) == 1
    refused = capsys.readouterr()
    assert not refused.out and refused.err.startswith("fleet-manager:")
    assert "private injected native failure" not in refused.err and "Traceback" not in refused.err
    assert database_bytes(root) == before


@pytest.mark.timeout(90)
@pytest.mark.parametrize("transition", ["construct", "register-commands", "unregister-commands", "remove-runtime"])
def test_actual_installed_console_generation_transitions(
    tmp_path_factory, primitive, signed_wheel, transition,
):
    # Keep the native pip/console executable below Win32's command-token limit.
    tmp_path = tmp_path_factory.mktemp("g")
    fixture = copy.deepcopy(signed_wheel)
    now = int(time.time())
    fixture["proof"]["payload"].update(issued_at=now - 10, expires_at=now + 300)
    fixture["policy"].update(valid_from=now - 20, valid_until=now + 600)
    fixture["policy"]["keys"][0].update(valid_from=now - 20, valid_until=now + 600)
    signed = independently_sign([fixture["proof"]["payload"]], tmp_path)
    fixture["proof"] = {**fixture["proof"], **signed["proofs"][0]}
    fixture["policy"]["keys"][0]["public_key"] = signed["public_key"]
    root = lineage_at(tmp_path)
    directory = tmp_path / "commands"
    directory.mkdir(mode=0o700)
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    paths = inputs_at(tmp_path, fixture)
    console = Path(sys.executable).with_name("fleet-manager.exe" if os.name == "nt" else "fleet-manager")
    assert console.is_file(), "actual installed owner console required"
    base = ["--root", str(root), "--identity", str(paths["identity"])]
    sources = [
        value for name in ("release", "proof", "policy", "sources", "target")
        for value in ("--" + name, str(paths[name]))
    ] + ["--staging-parent", str(stage)]
    construction = [
        *sources, "--selection", str(paths["selection"]), "--generation", FIRST, "--request", "forward-1",
    ]

    def invoke(operation, *arguments, success=True):
        started = time.monotonic()
        result = subprocess.run(
            [str(console), "client-generation", operation, *base, *arguments],
            cwd=tmp_path, capture_output=True, text=True, timeout=45, **FLAGS,
        )
        print("GENERATION_CLI_TIMING " + json.dumps({
            "operation": operation, "seconds": time.monotonic() - started,
            "returncode": result.returncode,
        }), flush=True)
        assert result.returncode == (0 if success else 1), result.stderr
        if success:
            return json.loads(result.stdout)
        assert not result.stdout and result.stderr.startswith("fleet-manager:")

    invoke("construct", *construction, success=False)
    assert list(root.iterdir()) == []
    invoke("initialize")
    first = invoke("construct", *construction)
    assert first["installed"] and first["started"] and first["healthy"]
    assert not first["selected"] and not first["registered"]
    assert first["reservation"] == state(root)[2][FIRST]
    direct_health(first, tmp_path)
    before = database_bytes(root)
    inspected = invoke("inspect")
    assert inspected["reservations"][FIRST] == first["reservation"]
    assert database_bytes(root) == before
    reservation = tmp_path / "reservation.json"
    reservation.write_bytes(canonical(first["reservation"]))
    exact = ["--reservation", str(reservation)]
    if transition == "construct":
        retry = invoke("construct", *construction)
        assert retry["reused"] and retry["reservation"] == first["reservation"]
        assert database_bytes(root) == before
        return
    observed_targets = invoke(
        "inspect-command-targets", *exact, "--command-directory", str(directory),
    )
    assert not observed_targets["mutations_performed"]
    replacement = tmp_path / "replacement-targets.json"
    replacement.write_bytes(canonical(observed_targets))
    registered = invoke(
        "register-commands", *exact, *sources, "--command-directory", str(directory),
        "--replacement-targets", str(replacement),
    )
    assert registered["selected"] and registered["registered"]
    assert invoke("inspect")["selected_generation"] == FIRST
    if transition == "register-commands":
        assert invoke(
            "register-commands", *exact, *sources, "--command-directory", str(directory),
            "--replacement-targets", str(replacement),
        )["reused"]
        return
    invoke("remove-runtime", *exact, success=False)
    assert not invoke("inspect")["retired"]
    withdrawal = [
        *exact, "--publication", registered["publication"], "--command-directory", str(directory),
    ]
    for _ in range(2):
        removed = invoke("unregister-commands", *withdrawal)
        assert removed["commands_absent"] and not removed["runtime_removed"]
    assert invoke("inspect")["selected_generation"] is None
    direct_health(first, tmp_path)
    if transition == "remove-runtime":
        for _ in range(2):
            removed = invoke("remove-runtime", *exact)
            assert removed["generation_retired"] and removed["runtime_removed"]
            assert not removed["installation_retired"]
        final = invoke("inspect")
        assert FIRST in final["retired"] and final["admitted"] == inspected["admitted"]
        assert Path(first["generation_root"]).is_dir() and not Path(first["direct_command"]).exists()
