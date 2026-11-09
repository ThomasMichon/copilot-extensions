"""Normal selected SSH setup through the packaged Source application owner."""

from __future__ import annotations

from contextlib import contextmanager
import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from agent_procutil.owned_process import run_caller_owned

from . import repository_setup as protocol

CAPABILITY = "continuity-ssh-setup-v1"
PIN_CAPABILITY = "continuity-ssh-setup-pin-v1"
KEY_REFERENCE_CAPABILITY = "continuity-ssh-key-reference-v1"
KEY_PIN_CAPABILITY = "continuity-ssh-key-pin-v1"


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
    version = value.get("version")
    protocol._fields(value, {
        "schema", "version", "snapshot", "root", "selection", "request_digest", "host",
    } | ({"key_files"} if version == 2 else set()))
    if value["schema"] != "fleet-manager.continuity-ssh-setup" or type(version) is not int or version not in (1, 2):
        raise protocol.SetupError("Selected Fleet returned unsupported SSH setup inputs.")
    if version == 2:
        references = protocol._fields(value["key_files"], {"identity_file", "public_key_file"})
        if any(
            not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts
            for path in references.values()
        ):
            raise protocol.SetupError("Selected Fleet returned invalid SSH key references.")
    if (
        not isinstance(value["request_digest"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["request_digest"]) is None
    ):
        raise protocol.SetupError("Selected Fleet returned an invalid SSH request digest.")
    return value


def key_binding(context, description: dict) -> None:
    selection = context.ssh_key
    directory = Path(selection.directory).expanduser()
    if description.get("key_files") != {
        "identity_file": str(directory / "id_ed25519"),
        "public_key_file": str(directory / "id_ed25519.pub"),
    }:
        raise protocol.SetupError("Selected Vault directory differs from the SSH request's exact key references.")


def _vault_call(selection, arguments: list[str], *, timeout: int, invocation=None) -> tuple[int, dict]:
    if invocation is not None:
        invocation.verify()
    environment = (
        dict(invocation.environment) if invocation is not None else protocol._environment(attended=False)
    )
    environment["AGENT_VAULT_NO_SELFPROVISION"] = "1"
    environment.pop("AGENT_VAULT_PINNED_CONTEXT", None)
    # A routing-only guard must not clear the caller's explicit provisioning inhibition.
    environment.pop("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", None)
    try:
        completed = run_caller_owned(
            [*(invocation.argv if invocation is not None else
               (str(Path(selection.argv[0]).expanduser()), *selection.argv[1:])),
             "--configuration-root", str(Path(selection.configuration_root).expanduser()), *arguments],
            env=environment, timeout=timeout, max_stdout_bytes=16384, max_stderr_bytes=16384,
        )
        if invocation is not None:
            invocation.verify()
        value = json.loads(completed.stdout, object_pairs_hook=protocol._object)
        if not isinstance(value, dict):
            raise ValueError
        return completed.returncode, value
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, UnicodeError):
        raise protocol.SetupError(
            "Selected Vault outcome is unconfirmed; preserve the original entry and key location for reconciliation."
        ) from None


def _key_fingerprint(value: object) -> str:
    try:
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError
        algorithm, encoded = value.split(" ")
        wire = base64.b64decode(encoded, validate=True)
        if (
            algorithm != "ssh-ed25519" or len(wire) != 51
            or wire[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
            or base64.b64encode(wire).decode("ascii") != encoded
        ):
            raise ValueError
    except (ValueError, binascii.Error, UnicodeError):
        raise protocol.SetupError("Selected Vault returned invalid public SSH key metadata.") from None
    return "SHA256:" + base64.b64encode(hashlib.sha256(wire).digest()).decode("ascii").rstrip("=")


def prepare_key(setup, context, description: dict, *, invocation=None) -> protocol.Outcome | None:
    """Reuse the selected owning Vault before any new enrollment or installation."""
    selection = context.ssh_key
    key_binding(context, description)
    root = Path(selection.configuration_root).expanduser()
    try:
        protocol._plain(root)
        if not root.is_dir():
            raise OSError
    except OSError:
        raise protocol.SetupError("Selected Vault configuration root is unavailable.") from None
    setup.verify()
    invocation_options = {}
    if selection.owner is not None:
        if invocation is None:
            from . import repository_vault

            invocation = repository_vault.selected()
        invocation_options["invocation"] = invocation
    code, contract = _vault_call(selection, ["invocation-contract", "--json"], timeout=30, **invocation_options)
    if (
        code != 0 or contract.get("schema") != "agent-vault.invocation"
        or type(contract.get("version")) is not int or contract["version"] != 1
        or not isinstance(contract.get("capabilities"), list)
        or not all(isinstance(item, str) for item in contract["capabilities"])
        or not {"configuration-root-v1", "ssh-key-install-pin-v1"} <= set(contract["capabilities"])
    ):
        return protocol.Outcome("refused", "Selected Vault lacks scoped, pinned SSH installation support; update its owner.")
    public = None
    for operation in ("prepare", "install"):
        arguments = ["ssh-key", operation, "--entry=" + selection.entry, "--require-running", "--json"]
        if operation == "install":
            arguments.extend([
                "--directory", str(Path(selection.directory).expanduser()),
                "--expected-fingerprint", selection.fingerprint,
            ])
        setup.verify()
        code, result = _vault_call(selection, arguments, timeout=120, **invocation_options)
        if operation == "install":
            setup.verify()
        if code != 0:
            error = result.get("error")
            hints = {
                "needs_unlock": ("waiting", "Unlock the selected owning Vault explicitly, then resume the same selection."),
                "service_unavailable": ("waiting", "Start the selected owning Vault separately, then resume the same selection."),
                "database_unavailable": ("waiting", "Synchronize the selected existing database; do not create a replacement."),
                "database_unconfigured": ("refused", "Configure the owning Vault's existing database before resuming."),
                "entry_missing": ("waiting", "Synchronize the original SSH key entry; setup never initializes another identity."),
                "malformed_key": ("refused", "Selected SSH key record is malformed; inspect it without replacing the identity."),
                "ambiguous_entry": ("refused", "Selected SSH entry is ambiguous; reconcile it through the owning Vault."),
                "invalid_entry": ("refused", "Selected SSH entry is invalid; select its original unambiguous Vault path."),
                "crypto_unavailable": ("refused", "The selected Vault needs its SSH extra through its normal installation owner."),
                "key_mismatch": ("refused", "Vault key differs from the selected fingerprint; preserve and reconcile the original identity."),
                "key_conflict": ("refused", "Selected key files conflict; preserve them and reconcile through their owner."),
                "unsafe_destination": ("refused", "Selected key location is not owner-private; setup does not repair permissions."),
                "local_install_failed": ("waiting", "Key installation is unconfirmed; inspect the same entry and location before resuming."),
            }
            if not isinstance(error, str):
                error = ""
            status_value, message = hints.get(error, (
                "waiting", "Selected Vault outcome is unconfirmed; reconcile the same entry and key location before resuming.",
            ))
            return protocol.Outcome(status_value, message)
        expected = {"version", "status", "public_key", "fingerprint"}
        if operation == "install":
            expected |= {"private_key_path", "public_key_path"}
        protocol._fields(result, expected)
        if (
            type(result["version"]) is not int or result["version"] != 1
            or result["status"] not in (("reused",) if operation == "prepare" else ("installed", "reused", "recovered"))
            or result["fingerprint"] != selection.fingerprint
            or _key_fingerprint(result["public_key"]) != selection.fingerprint
            or (public is not None and result["public_key"] != public)
        ):
            raise protocol.SetupError("Selected Vault did not confirm reuse of the exact pinned public SSH identity.")
        public = result["public_key"]
        if operation == "install" and (
            result["private_key_path"] != description["key_files"]["identity_file"]
            or result["public_key_path"] != description["key_files"]["public_key_file"]
        ):
            raise protocol.SetupError("Selected Vault installation has an unconfirmed key location; preserve its original inputs.")
    return None


def same_description(value: dict, description: dict) -> bool:
    expected = {name: item for name, item in description.items() if name != "key_files"} | {"version": 1}
    return _description(value) == expected


@contextmanager
def selected_inputs(description: dict):
    try:
        with _selected_inputs(description) as selected:
            yield selected
    except protocol.SetupError:
        raise
    except (ImportError, ValueError, RuntimeError, OSError, subprocess.SubprocessError):
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


def status(description: dict, module, *, require_ready: bool = False) -> bool:
    from .repository_continuity import _invoke

    def unavailable(stage):
        if require_ready:
            raise protocol.SetupError("SSH application readiness refused: " + stage + ".")
        return False

    root = Path(description["root"])
    version = description["selection"]["runtime_version"]
    if not (root / "current-version").exists():
        return unavailable("missing-selection")
    if module.engine.runtime.current_version(root) != version:
        return unavailable("different-generation")
    if not module.engine.runtime.is_complete(
        root, version, expect_hash=description["selection"]["payload_hash"],
    ):
        return unavailable("incomplete-construction")
    slot = root / "versions" / version
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = _invoke(
        ["gateway-host", "status"], python=python,
        environment={"FLEET_MANAGER_HOME": str(root)}, timeout=30,
    )
    if result.get("state") == "launch-unresolved":
        raise protocol.SetupError("SSH host launch is unsettled; reconcile its original owner before installation.")
    if result.get("ready") is not True:
        return unavailable("not-running" if result.get("state") == "not-running" else "not-converged")
    if result.get("selected") is not True:
        return unavailable("selection-or-input-mismatch")
    if type(result.get("revision")) is not int:
        return unavailable("missing-directory-revision")
    return True


def run(setup, context, arguments: list[str], description: dict, *, apply: bool, invocation=None):
    from .repository_continuity import _invoke

    arguments = [*arguments, "--expected-request-digest", description["request_digest"]]
    arguments = [item for item in arguments if item != "--key-references-only"]
    if context.ssh_key is not None:
        arguments.extend(["--expected-key-fingerprint", context.ssh_key.fingerprint])
    with selected_inputs(description) as (module, selected):
        setup.verify()
        if invocation is not None:
            invocation.verify()
        if status(description, module):
            if not same_description(_invoke(arguments), description):
                raise protocol.SetupError("SSH setup inputs changed during readiness inspection.")
            setup.verify()
            return protocol.Outcome("ready", "Selected SSH application has converged its authenticated directory.")
        if not apply:
            return protocol.Outcome("needed", "Selected SSH application requires its normal installation.")
        if invocation is not None:
            invocation.verify()
        current = _invoke([*arguments, "--apply"])
        if not same_description(current, description):
            raise protocol.SetupError("SSH setup inputs changed before application construction.")
        setup.verify()
        uv = shutil.which("uv")
        if uv is None:
            raise protocol.SetupError("SSH application construction requires the owner's existing UV executable.")
        if invocation is not None:
            invocation.verify()
        receipt = module.install_application(
            Path(description["snapshot"]), "fleet-manager", selected.payload, Path(description["root"]),
            uv=Path(uv).absolute(), base_python=Path(sys.executable),
            health_module="fleet_manager", expected_version=description["selection"]["runtime_version"],
            expected_hash=description["selection"]["payload_hash"],
        )
        setup.verify()
        if invocation is not None:
            invocation.verify()
        current = _invoke(arguments)
        if not same_description(current, description):
            raise protocol.SetupError("SSH setup inputs changed during application construction.")
        if not isinstance(receipt, dict) or receipt.get("state") != "installed":
            raise protocol.SetupError("Source application installation has no confirmed installed result.")
        status(description, module, require_ready=True)
        return protocol.Outcome("ready", "Selected SSH application has converged its authenticated directory.")
