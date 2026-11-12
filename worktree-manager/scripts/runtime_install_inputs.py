"""Admission of independent native wheel inputs before Manager dispatch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def admit_native_wheels(args, parser) -> None:
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
