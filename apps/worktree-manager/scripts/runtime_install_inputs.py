"""Admission of native installation inputs before Manager dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def validate_update_request(args: argparse.Namespace) -> None:
    """Recheck the admitted source and native invocation directory before install."""
    expected = getattr(args, "admitted_source", None)
    if expected is not None:
        from worktree_manager.update_driver_cli import _source_key, _update_context_directory

        if _source_key() != expected:
            raise RuntimeError("Manager source changed after submission; submit a new request")
        _update_context_directory(os.environ)


def describe_source_inputs(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    if not args.describe_source_inputs:
        return False
    if any((
        args.root, args.install_source, args.command, args.uninstall, args.apply,
        args.recover_adopted, args.describe_recovery, args.repair_update,
        args.recover_for_repair, args.json, args.install_only, args.update_participant,
        args.source_digest, args.skip_instance_reconcile, args.describe_install, args.stage_only,
    )):
        parser.error("Source-input discovery cannot be combined with an operation")
    print(json.dumps({
        "schema": "copilot-extensions.manager-source-inputs", "version": 1,
        "capabilities": ["install-source"],
    }))
    return True


def installation_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    if args.root is None:
        parser.error("--root is required for installation")
    command: list[str] = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.install_only and (command or args.source_digest is None):
        parser.error("Installation participant requires a source digest and no application command")
    if args.update_participant and (
        args.install_only or command[:1] != ["update"] or args.source_digest is None
        or args.recover_adopted is not None
    ):
        parser.error("Update participant requires a source digest and one update command")
    if args.skip_instance_reconcile and not args.install_only:
        parser.error("Instance reconciliation policy requires an installation participant")
    if args.source_digest is not None and (
        not (args.install_only or args.update_participant)
        or re.fullmatch("[0-9a-f]{64}", args.source_digest) is None
    ):
        parser.error("Source digest requires an installation participant and a SHA-256")
    if args.repair_update and not args.install_only and command[:1] != ["update"]:
        parser.error("Bootstrap repair is only valid for update")
    return command


def admit_bootstrap_uv(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.bootstrap_uv is None:
        return
    if (
        not args.bootstrap_uv.is_absolute() or not args.bootstrap_uv.is_file()
        or any((args.uninstall, args.recover_for_repair, args.recover_adopted,
                args.describe_recovery, args.describe_source_inputs, args.describe_install))
        or args.wheel_uv is not None
    ):
        parser.error("--bootstrap-uv requires an absolute existing installation tool, without recovery or wheel inputs")


def admit_native_wheels(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    values = (args.wheel_profile, args.wheel_directory, args.wheel_python, args.wheel_uv)
    if not any(value is not None for value in values) and args.wheel_profile_sha256 is None:
        return
    if (
        not all(values) or args.install_source is not None
        or any(not value.is_absolute() for value in values)
        or any((args.uninstall, args.recover_for_repair, args.recover_adopted,
                args.describe_recovery, args.describe_source_inputs))
        or args.wheel_python.resolve(strict=True) != Path(sys.executable).resolve(strict=True)
    ):
        parser.error("closed wheels require complete independent native installation inputs")
    from install_source import _read

    digest = hashlib.sha256(json.dumps(
        _read(args.wheel_profile), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    if args.wheel_profile_sha256 is not None and args.wheel_profile_sha256 != digest:
        raise RuntimeError("Manager wheel selection changed after driver admission")
    args.wheel_profile_sha256 = digest
