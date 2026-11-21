"""Thin fleet planning, explicit client lifecycle and opt-in NEW Gateway serving."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from . import __version__
from .planning import MAX_BYTES, PlanError, canonical, decode, plan
from .release import ROLES, inspect_release

if TYPE_CHECKING:
    from .artifact_staging import StagedArtifacts


class _ClientIdentity(TypedDict):
    schema: str
    version: int
    authority: str
    installation: str
    role: str
    initialized_by: str
    receipt: str
    selection_owner: str


_Snapshot = dict[str, object]
_ReadSnapshot = Callable[[], object]


# The legacy lifecycle owners validate decoded input themselves. Keep their
# CLI-facing signatures narrow without typing their internal state here.
class _InitializeLineage(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, /, *,
                 legacy_root: Path | None = None) -> _Snapshot: ...


class _InspectGenerations(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, /, *,
                 generation: str | None = None, after: str | None = None) -> _Snapshot: ...


class _CommandTargets(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, reservation: _Snapshot, /, *,
                 command_directory: Path) -> _Snapshot: ...


class _RemovePublication(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, reservation: _Snapshot, /, *,
                 publication: str, command_directory: Path) -> _Snapshot: ...


class _ConstructGeneration(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, selection: object,
                 staged: StagedArtifacts, /, *, generation: str, request: str,
                 current_policy: _ReadSnapshot, current_target: _ReadSnapshot) -> _Snapshot: ...


class _InstallGeneration(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, selection: object,
                 staged: StagedArtifacts, /, *, generation: str, request: str,
                 command_directory: Path, replace_commands: bool, legacy_root: Path | None,
                 current_policy: _ReadSnapshot, current_target: _ReadSnapshot) -> _Snapshot: ...


class _PublishGeneration(Protocol):
    def __call__(self, root: Path, identity: _ClientIdentity, reservation: _Snapshot,
                 staged: StagedArtifacts, /, *, command_directory: Path,
                 replacement_targets: object, current_policy: _ReadSnapshot,
                 current_target: _ReadSnapshot) -> _Snapshot: ...


class _UnregisterFrontdoor(Protocol):
    def __call__(self, root: Path, identity: object, /, *, command_directory: Path) -> _Snapshot: ...


class _RemoveRuntime(Protocol):
    def __call__(self, root: Path, identity: object, /, *, candidate: str) -> _Snapshot: ...


class _SelectRuntime(Protocol):
    def __call__(self, root: Path, identity: object, selection: object,
                 staged: StagedArtifacts, /, *, current_policy: _ReadSnapshot,
                 current_target: _ReadSnapshot) -> _Snapshot: ...


class _RegisterFrontdoor(Protocol):
    def __call__(self, root: Path, identity: object, selection: object,
                 staged: StagedArtifacts, /, *, command_directory: Path,
                 current_policy: _ReadSnapshot, current_target: _ReadSnapshot,
                 current_recovery: _ReadSnapshot | None = None) -> _Snapshot: ...


class _RecoverRelease(Protocol):
    def __call__(self, root: Path, identity: object, selection: object,
                 staged: StagedArtifacts, /, *, command_directory: Path,
                 current_policy: _ReadSnapshot, current_target: _ReadSnapshot,
                 current_recovery: _ReadSnapshot) -> _Snapshot: ...


class _InspectRelease(Protocol):
    def __call__(self, release: object, observation: object, /, *, role: str,
                 authority: str, installation: str, now: float) -> _Snapshot: ...


def read(path: Path, label: str) -> object:
    try:
        with path.open("rb") as stream:
            return decode(stream.read(MAX_BYTES + 1))
    except OSError:
        raise PlanError(f"{label} file could not be read") from None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in ("__source-setup", "__source-start"):
        if len(arguments) != 1:
            return 1
        try:
            from .gateway_host import callback

            return callback(arguments[0])
        except Exception as error:
            print("fleet-manager: selected Gateway hosting callback failed ("
                  + type(error).__name__ + ")", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"fleet-manager {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    location = commands.add_parser("continuity-state-path", help="Print the legacy enrollment path; never inspect or create it")
    location.add_argument("--repository", required=True)
    location.add_argument("--harness", required=True)
    planning = commands.add_parser("plan", help="Compare explicit non-secret snapshots; never provision")
    planning.add_argument("--template", type=Path, required=True)
    planning.add_argument("--config", type=Path, help="Existing adopter config; omitted means no config")
    planning.add_argument("--inventory", type=Path, required=True)
    planning.add_argument("--fleet-id", required=True)
    planning.add_argument("--machine-limit", type=int, required=True)
    designer = commands.add_parser("design", help="Actualize loadout slots and named-host resource grid; never deploy")
    designer.add_argument("--config", type=Path, required=True)
    designer.add_argument("--inventory", type=Path, required=True)
    designer.add_argument("--catalog", type=Path, help="Explicit additional non-secret contributed templates")
    inspection = commands.add_parser("inspect", help="Inspect explicit release snapshots; never probe or install")
    inspection.add_argument("--release", type=Path, required=True)
    inspection.add_argument("--observation", type=Path, help="Explicit installation observation; absent means unknown")
    inspection.add_argument("--role", choices=ROLES, required=True)
    inspection.add_argument("--authority", required=True)
    inspection.add_argument("--installation", required=True)
    diagnostic = commands.add_parser("doctor", help="Check this installed thin client; never install or probe a controller")
    diagnostic.add_argument("--role", choices=("client",), default="client")
    gateway_host = commands.add_parser("gateway-host", help="Control an explicitly selected installed Gateway")
    gateway_host.add_argument("action", choices=("capabilities", "status", "start", "stop"))
    hosting = commands.add_parser("serve", help="Run an explicitly configured foreground Fleet role")
    hosting.add_argument("--role", choices=("gateway", "ssh-client"), required=True)
    profile = hosting.add_mutually_exclusive_group(required=True)
    profile.add_argument("--config", type=Path)
    profile.add_argument("--service-policy", type=Path)
    hosting.add_argument("--tls-config", type=Path)
    hosting.add_argument("--operation-journal", type=Path)
    hosting.add_argument("--operation-authority")
    hosting.add_argument("--operation-installation")
    commands.add_parser("capabilities", help="Report this executable's available bootstrap contracts")
    bootstrap_parser = commands.add_parser(
        "bootstrap", help="Use a selected private local continuity projection; never install or SSH")
    bootstrap_parser.add_argument("--source", type=Path, required=True)
    bootstrap_parser.add_argument("--harness", required=True)
    bootstrap_parser.add_argument("--state-root", type=Path, required=True)
    bootstrap_parser.add_argument("--apply", action="store_true")
    reconnect = commands.add_parser(
        "continuity-reconnect", help="Authenticate with an existing repository enrollment; never enroll")
    reconnect.add_argument("--source", type=Path, required=True)
    reconnect.add_argument("--repository", required=True)
    reconnect.add_argument("--harness", required=True)
    ssh_setup = commands.add_parser(
        "ssh-setup", help="Prepare explicitly selected SSH host inputs; never construct or install")
    ssh_setup.add_argument("--request", type=Path, required=True)
    ssh_setup.add_argument("--source", type=Path, required=True)
    ssh_setup.add_argument("--harness", required=True)
    ssh_setup.add_argument("--state-root", type=Path, required=True)
    ssh_setup.add_argument("--apply", action="store_true")
    ssh_setup.add_argument("--expected-request-digest")
    ssh_setup.add_argument("--key-references-only", action="store_true")
    ssh_setup.add_argument("--expected-key-fingerprint")
    credential = commands.add_parser(
        "credential", help="Acquire one typed forge credential; secret JSON is for a captured pipe only",
    )
    credential.add_argument("--config", type=Path, required=True)
    credential.add_argument("--source", required=True)
    credential.add_argument("--account", required=True)
    credential.add_argument("--forge", required=True, help="Exact non-secret ForgeScope JSON")
    credential.add_argument("--timeout", type=float, default=15)
    admission = commands.add_parser(
        "client-state", help="Explicit client admission, construction, runtime selectors and commands",
    )
    state_commands = admission.add_subparsers(dest="state_command", required=True)
    for operation in (
        "initialize", "inspect", "admit", "construct", "activate-runtime",
        "register-commands", "unregister-commands", "deactivate-runtime", "remove-runtime",
        "recover-retained",
    ):
        state = state_commands.add_parser(operation)
        state.add_argument("--root", type=Path, required=True)
        state.add_argument("--identity", type=Path, required=True)
        if operation == "remove-runtime":
            state.add_argument("--candidate", required=True)
        if operation in ("admit", "construct", "activate-runtime", "register-commands", "recover-retained"):
            state.add_argument("--selection", type=Path, required=True)
            state.add_argument("--release", type=Path, required=True)
            state.add_argument("--proof", type=Path, required=True)
            state.add_argument("--policy", type=Path, required=True)
            state.add_argument("--sources", type=Path, required=True)
            state.add_argument("--staging-parent", type=Path, required=True)
        if operation in ("construct", "activate-runtime", "register-commands", "recover-retained"):
            state.add_argument("--target", type=Path, required=True)
        if operation in ("register-commands", "unregister-commands", "recover-retained"):
            state.add_argument("--command-directory", type=Path, required=True)
        if operation == "recover-retained":
            state.add_argument("--recovery-decision", type=Path, required=True)
        elif operation == "register-commands":
            state.add_argument("--recovery-decision", type=Path)
    generation = commands.add_parser(
        "client-generation", help="Explicit forward-generation lifecycle; never retire an installation",
    )
    generation_commands = generation.add_subparsers(dest="generation_command", required=True)
    for operation in (
        "initialize", "inspect", "inspect-command-targets", "construct",
        "register-commands", "unregister-commands", "remove-runtime", "install", "uninstall",
    ):
        forward = generation_commands.add_parser(operation)
        forward.add_argument("--root", type=Path, required=True)
        forward.add_argument("--identity", type=Path, required=True)
        if operation in ("initialize", "install"):
            forward.add_argument("--legacy-root", type=Path)
        if operation == "inspect":
            generation_filter = forward.add_mutually_exclusive_group()
            generation_filter.add_argument("--generation")
            generation_filter.add_argument("--after")
        if operation in ("construct", "install"):
            forward.add_argument("--generation", required=True)
            forward.add_argument("--request", required=True)
            forward.add_argument("--selection", type=Path, required=True)
        if operation in (
            "inspect-command-targets", "register-commands", "unregister-commands", "remove-runtime", "uninstall",
        ):
            forward.add_argument("--reservation", type=Path, required=True)
        if operation in ("construct", "register-commands", "install"):
            for name in ("release", "proof", "policy", "sources", "staging-parent", "target"):
                forward.add_argument("--" + name, type=Path, required=True)
        if operation in ("inspect-command-targets", "register-commands", "unregister-commands", "install", "uninstall"):
            forward.add_argument("--command-directory", type=Path, required=True)
        if operation == "install":
            forward.add_argument("--replace-commands", action="store_true", required=True)
        if operation == "register-commands":
            forward.add_argument("--replacement-targets", type=Path)
        if operation in ("unregister-commands", "uninstall"):
            forward.add_argument("--publication", required=True)
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    if args.command == "continuity-state-path":
        from .continuity_location import continuity_state_path

        try:
            print(continuity_state_path(args.repository, args.harness))
        except ValueError as error:
            print(f"fleet-manager: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "gateway-host":
        try:
            from .gateway_host import main as hosting_main

            return hosting_main(args.action)
        except Exception:
            print("fleet-manager: selected Gateway hosting control failed", file=sys.stderr)
            return 1
    if args.command in ("capabilities", "bootstrap", "ssh-setup", "continuity-reconnect"):
        continuity_result: Mapping[str, object]
        try:
            import asyncio
            from .continuity_bootstrap import (
                CAPABILITY, MACHINE_CALLER_CAPABILITY, MACHINE_REFERENCE_CAPABILITY, PROVIDER, bootstrap,
            )
            from .gateway_service_channel import TransportError
            from .ssh_setup import (
                CAPABILITY as SSH_SETUP_CAPABILITY, PIN_CAPABILITY as SSH_SETUP_PIN_CAPABILITY,
                KEY_REFERENCE_CAPABILITY as SSH_KEY_REFERENCE_CAPABILITY,
                KEY_PIN_CAPABILITY as SSH_KEY_PIN_CAPABILITY,
            )
        except ImportError:
            print("fleet-manager: continuity bootstrap requires the service-gateway extra",
                  file=sys.stderr)
            return 1
        try:
            if args.command == "capabilities":
                continuity_result = {"schema": "fleet-manager.capabilities", "version": 1,
                          "capabilities": [
                              CAPABILITY, PROVIDER, MACHINE_CALLER_CAPABILITY, MACHINE_REFERENCE_CAPABILITY,
                              SSH_SETUP_CAPABILITY, SSH_SETUP_PIN_CAPABILITY, SSH_KEY_REFERENCE_CAPABILITY,
                              SSH_KEY_PIN_CAPABILITY, "repository-continuity-reconnect-v1",
                          ]}
            elif args.command == "ssh-setup":
                from .ssh_setup import run as setup_ssh

                continuity_result = setup_ssh(
                    args.request.expanduser().absolute(), source=args.source.expanduser().absolute(),
                    harness=args.harness, state_root=args.state_root.expanduser().absolute(),
                    apply=args.apply, expected_request_digest=args.expected_request_digest,
                    key_references_only=args.key_references_only,
                    expected_key_fingerprint=args.expected_key_fingerprint,
                )
            elif args.command == "continuity-reconnect":
                from .continuity_location import continuity_state_path

                continuity_result = asyncio.run(bootstrap(
                    args.source.expanduser().absolute(), harness=args.harness,
                    state_root=continuity_state_path(args.repository, args.harness), apply=False))
                if continuity_result != {"status": "ready", "stage": "connected"}:
                    print("fleet-manager: existing enrollment did not reconnect", file=sys.stderr)
                    return 1
            else:
                continuity_result = asyncio.run(bootstrap(
                    args.source.expanduser().absolute(), harness=args.harness,
                    state_root=args.state_root.expanduser().absolute(), apply=args.apply))
            print(canonical(continuity_result).decode("utf-8"))
            return 0
        except ImportError:
            print("fleet-manager: selected continuity runtime closure is unavailable", file=sys.stderr)
            return 1
        except (TransportError, PlanError, OSError, ValueError, KeyError, TypeError) as exc:
            code = exc.code if isinstance(exc, TransportError) else (
                "continuity-ssh-setup-refused" if args.command == "ssh-setup" else "continuity-bootstrap-refused"
            )
            print("fleet-manager: " + code, file=sys.stderr)
            return 1
    if args.command == "credential":
        from .gateway_contract import ForgeScope, GatewayError, encode, parse, require

        try:
            from .gateway_worker import credential_provider

            require(not sys.stdout.isatty(), "captured-output-required")
            require(0 < args.timeout <= 60, "invalid-deadline")
            scope = ForgeScope.load(parse(args.forge.encode("utf-8")))
            material = credential_provider(
                args.config, source=args.source, account=args.account, forge=scope,
                timeout=args.timeout,
            )()
            require(material.forge == scope, "credential-scope-mismatch")
            print(encode({
                "schema": "fleet-manager.forge-credential", "version": 1,
                "token": material.token, "expires_at": material.expires_at,
                "forge": scope.to_dict(),
            }).decode("utf-8"))
        except ImportError:
            print("fleet-manager: credential requires the auth extra", file=sys.stderr)
            return 1
        except GatewayError as exc:
            print(f"fleet-manager: {exc}", file=sys.stderr)
            return 1
        except UnicodeError:
            print("fleet-manager: invalid-credential-reference-encoding", file=sys.stderr)
            return 1
        return 0
    if args.command == "serve":
        journal = (args.operation_journal, args.operation_authority, args.operation_installation)
        if any(item is not None for item in journal) and (
            not all(item is not None for item in journal) or args.service_policy is None
        ):
            print("fleet-manager: operation journal requires service policy and exact owner/installation",
                  file=sys.stderr)
            return 1
        if args.role == "ssh-client":
            if args.config is None or args.tls_config is not None:
                print("fleet-manager: ssh-client requires only --config", file=sys.stderr)
                return 1
            from .ssh_machine import serve as serve_ssh

            return serve_ssh(args.config)
        if args.service_policy is not None:
            if args.tls_config is None:
                print("fleet-manager: --service-policy requires --tls-config", file=sys.stderr)
                return 1
            try:
                from .gateway_service_server import serve as serve_service
            except ImportError:
                print("fleet-manager: service serving requires the service-gateway extra", file=sys.stderr)
                return 1
            if args.operation_journal is None:
                return serve_service(args.service_policy, args.tls_config)
            return serve_service(
                args.service_policy, args.tls_config, operation_journal=args.operation_journal,
                operation_authority=args.operation_authority,
                operation_installation=args.operation_installation)
        if args.tls_config is not None:
            print("fleet-manager: --tls-config requires --service-policy", file=sys.stderr)
            return 1
        try:
            from .gateway_server import serve
        except ImportError:
            print("fleet-manager: serve requires the server/auth extras", file=sys.stderr)
            return 1
        return cast(Callable[[Path], int], serve)(args.config)
    try:
        result: _Snapshot
        if args.command == "client-generation":
            from .artifact_staging import _bytes, stage_authenticated_artifacts
            from .client_admission import _identity
            from .client_generations import (
                _initialize_lineage, _reservation, construct_client_generation,
                inspect_client_generations, remove_client_generation,
            )

            def local(path: Path) -> object:
                return decode(_bytes(path.absolute(), MAX_BYTES))

            identity = cast(Callable[[object], _ClientIdentity], _identity)(local(args.identity))
            operation = args.generation_command
            if operation == "initialize":
                result = cast(_InitializeLineage, _initialize_lineage)(
                    args.root, identity, legacy_root=args.legacy_root,
                )
            elif operation == "inspect":
                result = cast(_InspectGenerations, inspect_client_generations)(
                    args.root, identity, generation=args.generation, after=args.after,
                )
            else:
                reservation = (
                    None if operation in ("construct", "install") else
                    cast(Callable[[object, _ClientIdentity], _Snapshot], _reservation)(
                        local(args.reservation), identity,
                    )
                )
                if operation == "inspect-command-targets":
                    assert reservation is not None
                    from .client_generation_frontdoor import inspect_command_targets

                    result = cast(_CommandTargets, inspect_command_targets)(
                        args.root, identity, reservation, command_directory=args.command_directory,
                    )
                elif operation == "remove-runtime":
                    assert reservation is not None
                    result = cast(
                        Callable[[Path, _ClientIdentity, _Snapshot], _Snapshot], remove_client_generation,
                    )(args.root, identity, reservation)
                elif operation in ("unregister-commands", "uninstall"):
                    assert reservation is not None
                    from .client_generation_frontdoor import unregister_client_generation
                    from .client_installation import uninstall_client_generation

                    remove_publication = uninstall_client_generation if operation == "uninstall" else unregister_client_generation
                    result = cast(_RemovePublication, remove_publication)(
                        args.root, identity, reservation, publication=args.publication,
                        command_directory=args.command_directory,
                    )
                else:
                    if (
                        args.staging_parent.absolute().is_relative_to(args.root.absolute())
                        or args.root.absolute().is_relative_to(args.staging_parent.absolute())
                    ):
                        raise PlanError("client generation requires a separate explicit staging parent")
                    with stage_authenticated_artifacts(
                        _bytes(args.release.absolute(), MAX_BYTES), local(args.proof), local(args.policy),
                        local(args.sources), authority=identity["authority"],
                        installation=identity["installation"], role="client",
                        staging_parent=args.staging_parent,
                    ) as staged:
                        def current_policy() -> object:
                            return local(args.policy)

                        def current_target() -> object:
                            return local(args.target)

                        if operation == "construct":
                            result = cast(_ConstructGeneration, construct_client_generation)(
                                args.root, identity, local(args.selection), staged,
                                generation=args.generation, request=args.request,
                                current_policy=current_policy, current_target=current_target,
                            )
                        elif operation == "install":
                            from .client_installation import install_client_generation

                            result = cast(_InstallGeneration, install_client_generation)(
                                args.root, identity, local(args.selection), staged,
                                generation=args.generation, request=args.request,
                                command_directory=args.command_directory,
                                replace_commands=args.replace_commands,
                                legacy_root=args.legacy_root,
                                current_policy=current_policy, current_target=current_target,
                            )
                        else:
                            assert reservation is not None
                            from .client_generation_frontdoor import publish_client_generation

                            result = cast(_PublishGeneration, publish_client_generation)(
                                args.root, identity, reservation, staged,
                                command_directory=args.command_directory,
                                replacement_targets=(
                                    None if args.replacement_targets is None else local(args.replacement_targets)
                                ), current_policy=current_policy, current_target=current_target,
                            )
        elif args.command == "client-state":
            from .artifact_staging import _bytes, stage_authenticated_artifacts
            from .client_admission import (
                admit_client_release, initialize_client_admission, inspect_client_admission,
                recover_client_admission,
            )

            def state_input(path: Path) -> object:
                return decode(_bytes(path.absolute(), MAX_BYTES))

            state_identity = state_input(args.identity)
            if args.state_command == "initialize":
                result = cast(Callable[[Path, object], _Snapshot], initialize_client_admission)(
                    args.root, state_identity,
                )
            elif args.state_command == "inspect":
                result = cast(Callable[[Path, object], _Snapshot], inspect_client_admission)(
                    args.root, state_identity,
                )
            elif args.state_command == "unregister-commands":
                from .client_command_removal import unregister_client_frontdoor

                result = cast(_UnregisterFrontdoor, unregister_client_frontdoor)(
                    args.root, state_identity, command_directory=args.command_directory,
                )
            elif args.state_command == "deactivate-runtime":
                from .client_deactivation import deactivate_client_runtime

                result = cast(Callable[[Path, object], _Snapshot], deactivate_client_runtime)(
                    args.root, state_identity,
                )
            elif args.state_command == "remove-runtime":
                from .client_runtime_removal import remove_client_runtime

                result = cast(_RemoveRuntime, remove_client_runtime)(
                    args.root, state_identity, candidate=args.candidate,
                )
            else:
                cast(Callable[[Path, object], None], recover_client_admission)(args.root, state_identity)
                # Recovery validates the decoded installation record before use.
                validated_identity = cast(_ClientIdentity, state_identity)
                if (
                    args.staging_parent.absolute().is_relative_to(args.root.absolute())
                    or args.root.absolute().is_relative_to(args.staging_parent.absolute())
                ):
                    raise PlanError("client admission requires a separate explicit staging parent")
                policy = state_input(args.policy)
                selection = state_input(args.selection)
                with stage_authenticated_artifacts(
                    _bytes(args.release.absolute(), MAX_BYTES), state_input(args.proof), policy,
                    state_input(args.sources), authority=validated_identity["authority"],
                    installation=validated_identity["installation"], role="client",
                    staging_parent=args.staging_parent,
                ) as staged:
                    if args.state_command in (
                        "construct", "activate-runtime", "register-commands", "recover-retained",
                    ):
                        from .client_activation import activate_client_runtime
                        from .client_construction import construct_client_release
                        from .client_frontdoor import register_client_frontdoor
                        from .client_recovery import recover_client_release

                        def state_policy() -> object:
                            return state_input(args.policy)

                        def state_target() -> object:
                            return state_input(args.target)

                        def current_recovery() -> object:
                            return state_input(args.recovery_decision)

                        if args.state_command == "recover-retained":
                            result = cast(_RecoverRelease, recover_client_release)(
                                args.root, state_identity, selection, staged,
                                command_directory=args.command_directory, current_recovery=current_recovery,
                                current_policy=state_policy, current_target=state_target,
                            )
                        elif args.state_command == "register-commands":
                            result = cast(_RegisterFrontdoor, register_client_frontdoor)(
                                args.root, state_identity, selection, staged,
                                command_directory=args.command_directory,
                                current_recovery=None if args.recovery_decision is None else current_recovery,
                                current_policy=state_policy, current_target=state_target,
                            )
                        else:
                            select_runtime = (construct_client_release if args.state_command == "construct"
                                              else activate_client_runtime)
                            result = cast(_SelectRuntime, select_runtime)(
                                args.root, state_identity, selection, staged,
                                current_policy=state_policy, current_target=state_target,
                            )
                    else:
                        result = cast(
                            Callable[[Path, object, object, "StagedArtifacts", object], _Snapshot],
                            admit_client_release,
                        )(args.root, state_identity, selection, staged, policy)
        elif args.command == "design":
            from .loadouts import design_loadout

            result = design_loadout(
                read(args.config, "config"), read(args.inventory, "inventory"),
                read(args.catalog, "catalog") if args.catalog else None, now=time.time(),
            )
        elif args.command == "doctor":
            from .client_diagnostics import diagnose_client

            result = diagnose_client()
        elif args.command == "inspect":
            result = cast(_InspectRelease, inspect_release)(
                read(args.release, "release"),
                read(args.observation, "observation") if args.observation else None,
                role=args.role, authority=args.authority, installation=args.installation,
                now=time.time(),
            )
        else:
            result = plan(read(args.template, "template"), read(args.config, "config") if args.config else None,
                          read(args.inventory, "inventory"), fleet_id=args.fleet_id,
                          machine_limit=args.machine_limit, now=time.time())
        serialized = canonical(result, output=True).decode("utf-8")
    except PlanError as exc:
        print(f"fleet-manager: {exc}", file=sys.stderr)
        return 1
    print(serialized)
    if args.command in ("doctor", "client-state", "client-generation"):
        return 0
    if args.command == "inspect":
        return 0 if result["status"] in ("installed", "ready") else 2
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
