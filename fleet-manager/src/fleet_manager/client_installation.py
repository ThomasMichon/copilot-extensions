"""Explicit thin-client composition through the existing generation lifecycle owners."""

import os
from pathlib import Path
import sqlite3
import time

from . import client_admission as admission, client_construction as construction
from . import client_generation_frontdoor as frontdoor, client_generations as generations
from .planning import PlanError, digest, identifier, record

TARGETS = "client-installation-targets.json"


def _installation_targets(root, identity, reservation, staged, *, command_directory,
                          current_policy, current_target, clock):
    generation_root, proof, binding = generations._generation_root(root, identity, reservation)
    with generations._generation_custody(
        root, generation_root, proof, binding, identity, reservation, staged,
        current_policy, current_target, clock,
    ) as (path, unchanged, checkpoint):
        saved = path / TARGETS
        if os.path.lexists(saved):
            targets = construction._read(saved)
        else:
            observed = generations.inspect_client_generations(
                root, identity, generation=reservation["generation"],
            )
            previous = observed["publication"]
            if previous is not None and previous["generation"] == reservation["generation"]:
                raise PlanError("client installation retry has lost its original command targets")
            targets = frontdoor.inspect_command_targets(
                root, identity, reservation, command_directory=command_directory,
            )
            checkpoint()
            construction._write(saved, targets)
        record(targets, "fleet-manager.client-command-targets", {
            "identity", "reservation_digest", "directory", "targets", "mutations_performed",
        })
        if (
            targets["identity"] != identity or targets["reservation_digest"] != digest(reservation)
            or targets["directory"] != generations._location(Path(command_directory))
            or targets["mutations_performed"] is not False
        ):
            raise PlanError("client installation retry differs from its original command targets")
        unchanged()
        return targets


def install_client_generation(root, identity, selection, staged, *, generation, request,
                              command_directory, replace_commands=False,
                              current_policy, current_target, clock=lambda: int(time.time())):
    """Realize supplied explicit intent; never choose a release or recover a predecessor."""
    if replace_commands is not True:
        raise PlanError("client installation requires explicit replacement of the addressed command family")
    identity, selection, _ = construction._request(
        identity, selection, staged, current_policy, current_target,
    )
    construction._verified(identity, selection, staged, current_policy, current_target, clock)
    generations._generation(generation)
    identifier(request, "forward installation request")
    try:
        root = Path(root)
        if not os.path.lexists(root / admission.DATABASE):
            generations._initialize_lineage(root, identity)
        constructed = generations.construct_client_generation(
            root, identity, selection, staged, generation=generation, request=request,
            current_policy=current_policy, current_target=current_target, clock=clock,
        )
        reservation = constructed["reservation"]
        targets = _installation_targets(
            root, identity, reservation, staged, command_directory=command_directory,
            current_policy=current_policy, current_target=current_target, clock=clock,
        )
        published = frontdoor.publish_client_generation(
            root, identity, reservation, staged, command_directory=command_directory,
            replacement_targets=targets, current_policy=current_policy,
            current_target=current_target, clock=clock,
        )
        return {
            **published, "schema": "fleet-manager.client-generation-installation",
            "reservation": reservation, "generation_root": constructed["generation_root"],
            "direct_command": constructed["direct_command"],
            "construction_reused": constructed["reused"], "intent": "explicit",
            "runtime_removed": False, "installation_retired": False,
            "mutations_performed": (
                constructed["mutations_performed"] or published["mutations_performed"]
            ),
        }
    except PlanError:
        raise
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        raise PlanError(
            "client installation is incomplete; retained exact request requires inspection or retry"
        ) from None


def uninstall_client_generation(root, identity, reservation, *, publication, command_directory,
                                clock=lambda: int(time.time())):
    """Withdraw and remove only the explicitly addressed generation, preserving its lineage."""
    withdrawn = frontdoor.unregister_client_generation(
        root, identity, reservation, publication=publication, command_directory=command_directory,
    )
    removed = generations.remove_client_generation(root, identity, reservation, clock=clock)
    return {
        **removed, "schema": "fleet-manager.client-generation-uninstallation",
        "publication": withdrawn["publication"], "command_directory": withdrawn["command_directory"],
        "commands_superseded": withdrawn["superseded"], "commands_absent": withdrawn["commands_absent"],
        "mutations_performed": withdrawn["mutations_performed"] or removed["mutations_performed"],
    }
