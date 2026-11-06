"""Normal selected SSH setup through the packaged Source application owner."""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import shutil
import sys

from . import repository_setup as protocol

CAPABILITY = "continuity-ssh-setup-v1"


def _installer():
    helper = Path(__file__).parent / "_runtime_assets" / "scripts" / "install_source.py"
    protocol._plain(helper)
    if not helper.is_file():
        raise protocol.SetupError("Selected engine has no packaged Source application installer.")
    spec = importlib.util.spec_from_file_location("_repository_ssh_source_installer", helper)
    if spec is None or spec.loader is None:
        raise protocol.SetupError("Selected engine Source application installer is unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if getattr(module, "APPLICATION_INSTALL_CONTRACT", None) != {
        "schema": "copilot-extensions.prepared-application-install", "version": 1,
    } or not callable(getattr(module, "install_application", None)):
        raise protocol.SetupError("Selected engine lacks prepared-application-install v1.")
    return module


def _description(value: dict) -> dict:
    protocol._fields(value, {
        "schema", "version", "snapshot", "root", "selection", "request_digest", "host",
    })
    if value["schema"] != "fleet-manager.continuity-ssh-setup" or type(value["version"]) is not int or value["version"] != 1:
        raise protocol.SetupError("Selected Fleet returned unsupported SSH setup inputs.")
    return value


@contextmanager
def selected_inputs(description: dict):
    try:
        with _selected_inputs(description) as selected:
            yield selected
    except protocol.SetupError:
        raise
    except (ImportError, ValueError, RuntimeError, OSError):
        raise protocol.SetupError(
            "Selected SSH Source/profile setup refused or has an unsettled outcome; preserve its original inputs."
        ) from None


@contextmanager
def _selected_inputs(description: dict):
    """Keep the original complete Source/profile association through installation."""
    module = _installer()
    snapshot = Path(description["snapshot"])
    with module.project_inputs(snapshot, "fleet-manager") as (selected, inputs):
        selection = module.native_selection(selected, inputs)
        expected = description["selection"]
        actual = {
            "source_commit": selected.identity.source_commit,
            "source_tree": selected.identity.source_tree,
            "runtime_version": selection["runtime_version"],
            "payload_hash": selection["payload_hash"],
        }
        if actual != expected or not {"gateway-host", "ssh-client"} <= set(selection["extras"]):
            raise protocol.SetupError("SSH setup requires the exact complete selected Source/profile.")
        if "application" not in selected.project:
            raise protocol.SetupError("SSH setup requires a prepared Source application.")
        application = module._member(snapshot, selected.project["application"])
        declaration = module._read(application / "application.json")
        if declaration.get("construction") != module.application_inputs(selected, inputs):
            raise protocol.SetupError("SSH setup application construction differs from its owned Source/profile.")
        if (
            declaration.get("name") != "fleet-manager"
            or declaration.get("module") != "fleet_manager.__main__:main"
            or declaration.get("setup") != ["__source-setup"]
            or declaration.get("start") != ["__source-start"]
        ):
            raise protocol.SetupError("SSH setup application lacks the supported Fleet hosting entrypoints.")
        yield module, selected


def status(description: dict, module) -> bool:
    from .repository_continuity import _invoke

    root = Path(description["root"])
    version = description["selection"]["runtime_version"]
    if not (root / "current-version").exists():
        return False
    if module.engine.runtime.current_version(root) != version:
        return False
    if not module.engine.runtime.is_complete(
        root, version, expect_hash=description["selection"]["payload_hash"],
    ):
        return False
    slot = root / "versions" / version
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = _invoke(
        ["gateway-host", "status"], python=python,
        environment={"FLEET_MANAGER_HOME": str(root)}, timeout=30,
    )
    if result.get("state") == "launch-unresolved":
        raise protocol.SetupError("SSH host launch is unsettled; reconcile its original owner before installation.")
    return (
        result.get("ready") is True and result.get("selected") is True
        and type(result.get("revision")) is int
    )


def run(setup, context, arguments: list[str], description: dict, *, apply: bool):
    from .repository_continuity import _invoke

    with selected_inputs(description) as (module, selected):
        setup.verify()
        if status(description, module):
            current = _description(_invoke(arguments))
            if current != description:
                raise protocol.SetupError("SSH setup inputs changed during readiness inspection.")
            return protocol.Outcome("ready", "Selected SSH application has converged its authenticated directory.")
        if not apply:
            return protocol.Outcome("needed", "Selected SSH application requires its normal installation.")
        current = _description(_invoke([*arguments, "--apply"]))
        if current != description:
            raise protocol.SetupError("SSH setup inputs changed before application construction.")
        setup.verify()
        uv = shutil.which("uv")
        if uv is None:
            raise protocol.SetupError("SSH application construction requires the owner's existing UV executable.")
        receipt = module.install_application(
            Path(description["snapshot"]), "fleet-manager", selected.payload, Path(description["root"]),
            uv=Path(uv).absolute(), base_python=Path(sys.executable),
            health_module="fleet_manager", expected_version=description["selection"]["runtime_version"],
            expected_hash=description["selection"]["payload_hash"],
        )
        setup.verify()
        current = _description(_invoke(arguments))
        if (
            current != description or not isinstance(receipt, dict) or receipt.get("state") != "installed"
            or not status(description, module)
        ):
            raise protocol.SetupError("SSH application installation has no current converged readiness.")
        return protocol.Outcome("ready", "Selected SSH application has converged its authenticated directory.")
