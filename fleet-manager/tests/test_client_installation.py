"""Managed composition retains original incoming intent, not predecessor permission."""

import copy

import pytest

from fleet_manager import client_generation_frontdoor as frontdoor
from fleet_manager import client_generations as generations, client_installation as managed
from fleet_manager.planning import PlanError, canonical
from fleet_manager.private_state import private_directory
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity, retained, selection
from test_client_construction import signed_wheel as signed_wheel, target  # noqa: F401
from test_client_generation_frontdoor import installation as installation, pointer  # noqa: F401
from test_client_frontdoor import states
from test_client_generations import FIRST, lineage_at, reserve
from test_release_auth import artifact_fixture


def test_installation_requires_explicit_fixed_command_replacement_before_any_effect(tmp_path):
    root = lineage_at(tmp_path)
    with pytest.raises(PlanError, match="explicit replacement"):
        managed.install_client_generation(
            root, {}, {}, None, generation=FIRST, request="forward-1",
            command_directory=tmp_path, current_policy=lambda: {}, current_target=lambda: {},
        )
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("change", ["targets", "directory", "reservation"])
def test_installation_target_observation_is_saved_once_and_exactly_scoped(tmp_path, primitive, change):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    directory = tmp_path / "commands"
    private_directory(directory)
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
        options = {
            "command_directory": directory, "current_policy": lambda: trusted,
            "current_target": lambda: target(fixture), "clock": lambda: 150,
        }
        original = managed._installation_targets(root, identity(), reservation, staged, **options)
        saved = root / ("generation-" + FIRST) / managed.TARGETS
        before = saved.read_bytes()
        if change == "targets":
            command = frontdoor.frontdoor._command_paths(directory)[0]
            command.write_bytes(b"unrelated new command")
            assert managed._installation_targets(
                root, identity(), reservation, staged, **options,
            ) == original
            assert frontdoor.inspect_command_targets(
                root, identity(), reservation, command_directory=directory,
            ) != original
            assert command.read_bytes() == b"unrelated new command"
        else:
            if change == "directory":
                other = tmp_path / "other"
                private_directory(other)
                options["command_directory"] = other
            else:
                altered = copy.deepcopy(original)
                altered["reservation_digest"] = "0" * 64
                saved.write_bytes(canonical(altered))
                before = saved.read_bytes()
            with pytest.raises(PlanError, match="original command targets"):
                managed._installation_targets(root, identity(), reservation, staged, **options)
        assert saved.read_bytes() == before


def install(installation):
    root, directory, fixture, staged, trusted, _first = installation
    return managed.install_client_generation(
        root, identity(), selection(fixture), staged, generation=FIRST, request="forward-1",
        command_directory=directory, replace_commands=True,
        current_policy=lambda: trusted, current_target=lambda: target(fixture), clock=lambda: 150,
    )


@pytest.mark.timeout(90)
@pytest.mark.parametrize("boundary", ["copy", "health"])
def test_managed_interrupted_publication_uses_original_observation_and_real_retry(
    primitive, installation, monkeypatch, boundary,
):
    root, directory, _fixture, _staged, _trusted, _first = installation
    producer = primitive._runtime_component("versioned_runtime_publication")
    actual = producer.CopyPublication.verify if boundary == "copy" else frontdoor.frontdoor._health

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        if boundary == "health" or not args[0].published:
            raise OSError("injected after actual incoming effects")

    with monkeypatch.context() as injected:
        if boundary == "copy":
            injected.setattr(producer.CopyPublication, "verify", interrupted)
        else:
            injected.setattr(frontdoor.frontdoor, "_health", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            install(installation)
    assert pointer(root)[0] is None
    saved = root / ("generation-" + FIRST) / managed.TARGETS
    initial = saved.read_bytes()
    before = states([path for path in frontdoor.frontdoor._command_paths(directory) if path.exists()])
    result = install(installation)
    assert result["selected"] and result["registered"] and pointer(root)[0] == FIRST
    assert saved.read_bytes() == initial
    if boundary == "health":
        assert result["reused"] and states(frontdoor.frontdoor._command_paths(directory)) == before
    else:
        assert list((root / frontdoor.HISTORY).glob("*/copy-recovery.*.jsonl"))


@pytest.mark.timeout(90)
def test_missing_incoming_observation_never_reobserves_an_existing_publication(primitive, installation):
    root, directory, _fixture, _staged, _trusted, _first = installation
    installed = install(installation)
    saved = root / ("generation-" + FIRST) / managed.TARGETS
    saved.unlink()
    commands = states(frontdoor.frontdoor._command_paths(directory))
    before = database_bytes(root)
    with pytest.raises(PlanError, match="lost its original command targets"):
        install(installation)
    assert pointer(root)[0] == FIRST and database_bytes(root) == before
    assert states(frontdoor.frontdoor._command_paths(directory)) == commands
    assert installed["installed"] and not saved.exists()
