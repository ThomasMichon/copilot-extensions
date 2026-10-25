"""Scope and resource-limit validation for opt-in installer functional tests."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path


def validate_profile(args: Namespace) -> None:
    if args.test_profile == "standard":
        return
    selected = {Path(path.replace("\\", "/")) for path in args.test_file}
    allowed = {
        Path("tests") / "test_installer_engine_adoption.py",
        Path("tests") / "test_snapshot_copy_contract.py",
        Path("tests") / "test_installer_test_support.py",
    }
    if (
        args.plugins != ["agent-codespaces"] or not selected or not selected <= allowed
        or args.all or args.changed or args.prepare_only or args.selection_file
        or args.guards or args.kexpr or args.collect_only or args.list_only
        or args.exclude or args.pre_push or args.allow_host_state
        or args.allow_explicit_tiers
    ):
        raise ValueError("busy-host-functional requires agent-codespaces --test-file "
                         "with only installer adoption, snapshot-copy or installer-support files")
    if (
        any(value is not None for value in (
            args.subsuite_timeout, args.plugin_timeout, args.test_timeout,
            args.max_files_per_sub_suite,
        ))
        or (args.max_processes, args.max_memory_mb, args.max_temp_mb) != (128, 4096, 2048)
    ):
        raise ValueError("busy-host-functional cannot be combined with limit overrides")
