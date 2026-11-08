"""Reuse-only materialization through the existing selected Vault owner."""

from __future__ import annotations

import json
from pathlib import Path

from .identity_key import IdentityKeyError
from .ssh_key import FAILURE_HINTS, _crypto, decode_record, public_metadata, validate_request


def command(args) -> int:
    from . import cli, config
    from .ssh_key_files import install

    stage = "context"
    try:
        validate_request(args.entry, False, False)
        _crypto()
        directory = Path(args.directory).expanduser()
        if not directory.is_absolute():
            raise IdentityKeyError("invalid_directory", "Select an absolute local SSH key directory.")
        context = config.resolve_context(strict=True)
        if not context.kpdb:
            raise IdentityKeyError("database_unconfigured", "Configure an existing KeePass database first.")
        database = Path(context.kpdb).expanduser().resolve(strict=True)
        if not database.is_file():
            raise IdentityKeyError("database_unavailable", "Configured KeePass database is not a file.")
        entry = config.normalize_entry(args.entry, context.group)
        validate_request(entry, False, False)
        stage = "service"
        if args.require_running:
            running = cli.send_command({"action": "ping"}, timeout=5.0, strict_endpoint=True)
            if not isinstance(running, dict) or running.get("ok") is not True:
                raise IdentityKeyError("service_unavailable", FAILURE_HINTS["service_unavailable"])
        elif not cli.ensure_service():
            raise IdentityKeyError("service_unavailable", FAILURE_HINTS["service_unavailable"])
        response = cli.send_command({
            "action": "ssh-key-read", "entry": entry, "kpdb": str(database), "group": "",
            "allow_prompt": args.prompt,
        }, timeout=120.0 if args.prompt else 90.0, strict_endpoint=True)
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = response.get("code") if isinstance(response, dict) else None
            if not isinstance(code, str) or code not in FAILURE_HINTS:
                code = "service_unavailable"
            raise IdentityKeyError(code, FAILURE_HINTS[code])
        record = decode_record(response.get("value"))
        stage = "files"
        result = install(
            directory, record["private_key"].encode("ascii"),
            (record["public_key"] + "\n").encode("ascii"),
        )
        print(json.dumps({"version": 1, **result, **public_metadata(record["public_key"])}, sort_keys=True))
        return 0
    except IdentityKeyError as exc:
        payload = {"version": 1, "error": exc.code, "message": str(exc)}
    except PermissionError:
        payload = {
            "version": 1, "error": "unsafe_destination",
            "message": "SSH key location is not an ordinary owner-private directory/file; inspect permissions and links.",
        }
    except FileExistsError:
        payload = {
            "version": 1, "error": "key_conflict",
            "message": "SSH key location contains a different key; preserve it and select a separate directory.",
        }
    except (OSError, RuntimeError, ValueError):
        payload = {
            "version": 1,
            "error": "local_install_failed" if stage == "files" else "context_or_service_unavailable",
            "message": (
                "Local installation is unconfirmed. Inspect the selected directory and permissions; "
                "retry with the same Vault entry and location, never overwrite or rotate the key."
                if stage == "files" else
                "Selected Vault context or service is unusable; repair it without replacing the key."
            ),
        }
    print(json.dumps(payload, sort_keys=True))
    return 1
