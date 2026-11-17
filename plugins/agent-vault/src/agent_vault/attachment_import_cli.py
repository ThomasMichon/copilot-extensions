"""Opt-in non-activating entrypoint for create-only attachment imports."""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from .attachment_import import MAX_ATTACHMENT_BYTES, AttachmentImportError, validate_names
from .transport_binding import MutationBinding

_REPARSE_POINT = 0x400

NO_EFFECT_CODES = frozenset(
    {
        "invalid_import",
        "input_custody_invalid",
        "input_custody_unsupported",
        "input_unavailable",
        "require_running_required",
        "database_unconfigured",
        "service_unavailable",
        "create_only_unsupported",
        "mutation_transport_unsupported",
        "backend_unsupported",
        "needs_unlock",
        "vault_read_failed",
        "entry_exists",
        "parent_unavailable",
        "operation_expired",
    }
)
UNCERTAIN_CODES = frozenset(
    {
        "creation_unconfirmed",
        "attachment_creation_unconfirmed",
        "import_unconfirmed",
    }
)


def _read_file(path: Path, *, private: bool) -> bytes:
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise AttachmentImportError("input_custody_invalid")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or getattr(before, "st_file_attributes", 0) & _REPARSE_POINT
        or before.st_size > MAX_ATTACHMENT_BYTES
        or (
            os.name != "nt"
            and (
                before.st_uid != os.getuid()
                or (stat.S_IMODE(before.st_mode) != 0o600 if private else before.st_mode & 0o022)
            )
        )
    ):
        raise AttachmentImportError("input_custody_invalid")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    )
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise AttachmentImportError("input_custody_invalid")
        value = handle.read(MAX_ATTACHMENT_BYTES + 1)
    after = path.lstat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise AttachmentImportError("input_custody_invalid")
    if not 1 <= len(value) <= MAX_ATTACHMENT_BYTES:
        raise AttachmentImportError("invalid_import")
    return value


def command(args: Any) -> int:
    from . import cli, config

    effect_possible = False
    try:
        if not getattr(args, "require_running", False):
            raise AttachmentImportError("require_running_required")
        if os.name == "nt":
            # POSIX mode checks do not establish native Windows private-file custody.
            raise AttachmentImportError("input_custody_unsupported")
        private_path = Path(os.path.abspath(os.path.expanduser(args.key_file)))
        context = config.resolve_context(strict=True)
        if not context.kpdb:
            raise AttachmentImportError("database_unconfigured")
        entry, key_name = validate_names(
            config.normalize_entry(args.entry, context.group),
            private_path.name,
        )
        private = _read_file(private_path, private=True)
        public = _read_file(Path(str(private_path) + ".pub"), private=False)
        binding = MutationBinding()
        response = cli.send_command(
            {"action": "ping", "health_only": True},
            timeout=5.0,
            strict_endpoint=True,
            require_endpoint=True,
            binding=binding,
        )
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise AttachmentImportError("service_unavailable")
        if response.get("accepting_work") is not True:
            raise AttachmentImportError("service_unavailable")
        versions = response.get("key_attachment_import_versions")
        if not isinstance(versions, list) or not any(
            type(version) is int and version == 1 for version in versions
        ):
            raise AttachmentImportError("create_only_unsupported")
        binding.accept_health(response)
        if not binding.ready:
            raise AttachmentImportError("mutation_transport_unsupported")
        effect_possible = True
        response = binding.send_once(
            {
                "action": "import-key-create-only",
                "entry": entry,
                "group": "",
                "kpdb": context.kpdb,
                "key_name": key_name,
                "key_data": base64.b64encode(private).decode("ascii"),
                "pub_data": base64.b64encode(public).decode("ascii"),
                "allow_prompt": False,
            },
            timeout=75.0,
        )
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = response.get("code") if isinstance(response, dict) else None
            if code in NO_EFFECT_CODES or code in UNCERTAIN_CODES:
                raise AttachmentImportError(code)
            raise AttachmentImportError("import_unconfirmed")
        if response.get("status") != "created":
            raise AttachmentImportError("import_unconfirmed")
        print(json.dumps({"version": 1, "status": "created"}, sort_keys=True))
        return 0
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, AttachmentImportError)
            else "import_unconfirmed"
            if effect_possible
            else "input_unavailable"
        )
        if code not in NO_EFFECT_CODES and code not in UNCERTAIN_CODES:
            code = "import_unconfirmed" if effect_possible else "invalid_import"
        print(
            json.dumps(
                {
                    "version": 1,
                    "error": code,
                    "effect_status": "not_started" if code in NO_EFFECT_CODES else "unknown",
                    "message": "Inspect the selected owner and retained operation before retrying; no automatic retry is safe.",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
