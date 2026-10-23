"""Thin fleet planning, explicit client lifecycle and opt-in NEW Gateway serving."""

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .planning import MAX_BYTES, PlanError, canonical, decode, plan
from .release import ROLES, inspect_release


def read(path, label):
    try:
        with path.open("rb") as stream:
            return decode(stream.read(MAX_BYTES + 1))
    except OSError:
        raise PlanError(f"{label} file could not be read") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"fleet-manager {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan", help="Compare explicit non-secret snapshots; never provision")
    planning.add_argument("--template", type=Path, required=True)
    planning.add_argument("--config", type=Path, help="Existing adopter config; omitted means no config")
    planning.add_argument("--inventory", type=Path, required=True)
    planning.add_argument("--fleet-id", required=True)
    planning.add_argument("--machine-limit", type=int, required=True)
    inspection = commands.add_parser("inspect", help="Inspect explicit release snapshots; never probe or install")
    inspection.add_argument("--release", type=Path, required=True)
    inspection.add_argument("--observation", type=Path, help="Explicit installation observation; absent means unknown")
    inspection.add_argument("--role", choices=ROLES, required=True)
    inspection.add_argument("--authority", required=True)
    inspection.add_argument("--installation", required=True)
    diagnostic = commands.add_parser("doctor", help="Check this installed thin client; never install or probe a controller")
    diagnostic.add_argument("--role", choices=("client",), default="client")
    hosting = commands.add_parser("serve", help="Host the explicitly configured NEW Gateway over TLS")
    hosting.add_argument("--role", choices=("gateway",), required=True)
    profile = hosting.add_mutually_exclusive_group(required=True)
    profile.add_argument("--config", type=Path)
    profile.add_argument("--service-policy", type=Path)
    hosting.add_argument("--tls-config", type=Path)
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
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
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
        if args.service_policy is not None:
            if args.tls_config is None:
                print("fleet-manager: --service-policy requires --tls-config", file=sys.stderr)
                return 1
            try:
                from .gateway_service_server import serve as serve_service
            except ImportError:
                print("fleet-manager: service serving requires the service-gateway extra", file=sys.stderr)
                return 1
            return serve_service(args.service_policy, args.tls_config)
        if args.tls_config is not None:
            print("fleet-manager: --tls-config requires --service-policy", file=sys.stderr)
            return 1
        try:
            from .gateway_server import serve
        except ImportError:
            print("fleet-manager: serve requires the server/auth extras", file=sys.stderr)
            return 1
        return serve(args.config)
    try:
        if args.command == "client-state":
            from .artifact_staging import _bytes, stage_authenticated_artifacts
            from .client_admission import (
                admit_client_release, initialize_client_admission, inspect_client_admission,
                recover_client_admission,
            )

            def local(path):
                return decode(_bytes(path.absolute(), MAX_BYTES))

            identity = local(args.identity)
            if args.state_command == "initialize":
                result = initialize_client_admission(args.root, identity)
            elif args.state_command == "inspect":
                result = inspect_client_admission(args.root, identity)
            elif args.state_command == "unregister-commands":
                from .client_command_removal import unregister_client_frontdoor

                result = unregister_client_frontdoor(
                    args.root, identity, command_directory=args.command_directory,
                )
            elif args.state_command == "deactivate-runtime":
                from .client_deactivation import deactivate_client_runtime

                result = deactivate_client_runtime(args.root, identity)
            elif args.state_command == "remove-runtime":
                from .client_runtime_removal import remove_client_runtime

                result = remove_client_runtime(args.root, identity, candidate=args.candidate)
            else:
                recover_client_admission(args.root, identity)
                if (
                    args.staging_parent.absolute().is_relative_to(args.root.absolute())
                    or args.root.absolute().is_relative_to(args.staging_parent.absolute())
                ):
                    raise PlanError("client admission requires a separate explicit staging parent")
                policy = local(args.policy)
                selection = local(args.selection)
                with stage_authenticated_artifacts(
                    _bytes(args.release.absolute(), MAX_BYTES), local(args.proof), policy,
                    local(args.sources), authority=identity["authority"],
                    installation=identity["installation"], role="client",
                    staging_parent=args.staging_parent,
                ) as staged:
                    if args.state_command in (
                        "construct", "activate-runtime", "register-commands", "recover-retained",
                    ):
                        from .client_activation import activate_client_runtime
                        from .client_construction import construct_client_release
                        from .client_frontdoor import register_client_frontdoor
                        from .client_recovery import recover_client_release

                        execute = {
                            "construct": construct_client_release,
                            "activate-runtime": activate_client_runtime,
                            "register-commands": register_client_frontdoor,
                            "recover-retained": recover_client_release,
                        }[args.state_command]
                        options = ({"command_directory": args.command_directory}
                                   if args.state_command in ("register-commands", "recover-retained") else {})
                        if args.state_command in ("register-commands", "recover-retained") and (
                            args.recovery_decision is not None
                        ):
                            options["current_recovery"] = lambda: local(args.recovery_decision)
                        result = execute(
                            args.root, identity, selection, staged,
                            current_policy=lambda: local(args.policy),
                            current_target=lambda: local(args.target),
                            **options,
                        )
                    else:
                        result = admit_client_release(args.root, identity, selection, staged, policy)
        elif args.command == "doctor":
            from .client_diagnostics import diagnose_client

            result = diagnose_client()
        elif args.command == "inspect":
            result = inspect_release(
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
    if args.command in ("doctor", "client-state"):
        return 0
    if args.command == "inspect":
        return 0 if result["status"] in ("installed", "ready") else 2
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
