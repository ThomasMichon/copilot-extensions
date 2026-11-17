"""Opt-in non-activating export to new descriptor-pinned private files."""

from __future__ import annotations

import json
import sys
from typing import Any

from .attachment_export_files import ExclusivePair, ExportFileError
from .attachment_import import AttachmentImportError, decode_attachment, validate_names
from .transport_binding import MutationBinding

_CODES = frozenset({
    "require_running_required", "invalid_export", "database_unconfigured",
    "output_custody_invalid", "output_custody_unsupported", "destination_exists",
    "service_unavailable", "exclusive_export_unsupported", "mutation_transport_unsupported",
    "backend_unsupported", "needs_unlock", "export_failed", "export_owner_changed",
    "export_unconfirmed",
})


def command(args: Any) -> int:
    from . import cli, config

    destination: ExclusivePair | None = None
    read_attempted = False
    try:
        if not getattr(args, "require_running", False):
            raise ExportFileError("require_running_required")
        context = config.resolve_context(strict=True)
        if not context.kpdb:
            raise ExportFileError("database_unconfigured")
        try:
            _, name = validate_names(
                config.normalize_entry(args.entry, context.group or ""), args.key_name,
            )
        except AttachmentImportError:
            raise ExportFileError("invalid_export") from None
        destination = ExclusivePair(args.dest_dir, name)
        with destination:
            binding = MutationBinding("export")
            health = cli.send_command(
                {"action": "ping", "health_only": True},
                timeout=5.0, strict_endpoint=True, require_endpoint=True, binding=binding,
            )
            if (
                not isinstance(health, dict) or health.get("ok") is not True
                or health.get("accepting_work") is not True
            ):
                raise ExportFileError("service_unavailable")
            versions = health.get("key_attachment_export_versions")
            if not isinstance(versions, list) or not any(
                type(version) is int and version == 1 for version in versions
            ):
                raise ExportFileError("exclusive_export_unsupported")
            binding.accept_health(health)
            if not binding.ready or not binding.matches_context({
                "kpdb": context.kpdb, "group": context.group, "vault": context.vault_name,
            }):
                raise ExportFileError("mutation_transport_unsupported")
            destination.preflight()
            read_attempted = True
            response = binding.send_once({
                "action": "export-key-nonactivating", "entry": args.entry, "key_name": name,
                "allow_prompt": False,
            }, timeout=30.0)
            if not isinstance(response, dict) or response.get("ok") is not True:
                code = response.get("code") if isinstance(response, dict) else None
                raise ExportFileError(
                    code if isinstance(code, str) and code in _CODES else "export_unconfirmed"
                )
            try:
                private = decode_attachment(response.get("key_data"))
                public = decode_attachment(response.get("pub_data"))
            except AttachmentImportError:
                raise ExportFileError("invalid_export") from None
            destination.materialize(private, public)
        print(json.dumps({"version": 1, "status": "created"}, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        possible = destination is not None and destination.effect_possible
        code = error.code if isinstance(error, ExportFileError) else "export_unconfirmed"
        if code not in _CODES:
            code = "export_unconfirmed"
        print(json.dumps({
            "version": 1, "error": code,
            "effect_status": "unknown" if possible or read_attempted else "not_started",
            "file_effect_status": "unknown" if possible else "not_started",
            "message": "Retain created files and inspect the selected owner and destination custody before retrying; no automatic retry or cleanup is performed.",
        }, sort_keys=True), file=sys.stderr)
        return 1
