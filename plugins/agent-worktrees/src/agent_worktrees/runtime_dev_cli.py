"""Deployed dev-slot operations using the retained installation's own code."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from . import config as cfg
from . import output, registry_paths


def add_parsers(sub) -> None:
    status = sub.add_parser("dev-status", help="Read the selected runtime's dev claim")
    status.add_argument("--json", action="store_true")
    release = sub.add_parser("dev-release", help="Restore the original runtime, then release dev")
    release.add_argument("--owner", type=Path, help="Claiming worktree root (may no longer exist)")
    release.add_argument("--json", action="store_true")


def _lifecycle():
    slot = Path(sys.prefix).resolve()
    bootstrap = slot / ".runtime-bootstrap"
    helper = bootstrap / "runtime_install.py"
    if bootstrap.is_symlink() or not helper.is_file() or helper.is_symlink():
        raise RuntimeError(
            "Installed lifecycle unavailable; update agent-worktrees before release"
        )
    spec = importlib.util.spec_from_file_location("runtime_install", helper)
    if spec is None or spec.loader is None:
        raise ImportError("Installed runtime lifecycle cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _error(args, error: Exception, code: int = 1) -> int:
    message = str(error)
    if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        diagnostic = error.stderr or error.stdout
        if diagnostic:
            message += f"\n{diagnostic}"
    if getattr(args, "json", False):
        return output._json_error(message, code)
    output.err(message)
    return code


def cmd_dev_status(args: argparse.Namespace) -> int:
    try:
        lifecycle = _lifecycle()
        root = cfg.install_dir().resolve()
        claim = lifecycle.runtime.read_dev_claim(root, strict=True)
        selected = lifecycle.runtime.current_version(root)
        record = {"claim": claim, "runtime_slot": selected}
        if getattr(args, "json", False):
            output._json_output(record)
        else:
            print(json.dumps(record, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        return _error(args, error)


def cmd_dev_release(args: argparse.Namespace) -> int:
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        return _cmd_dev_release(args)
    from .update_driver_json_cli import _run_request

    execution = argparse.Namespace(**vars(args))
    execution.json = True
    flags = ["--json"]
    if getattr(args, "owner", None) is not None:
        execution.owner = args.owner.resolve()
        flags.extend(["--owner", str(execution.owner)])

    def execute(request):
        with output.capture_json_output() as captured:
            code = _cmd_dev_release(request)
        print(captured.getvalue(), end="")
        return code

    buffer = io.StringIO()
    try:
        lifecycle = _lifecycle()
        root, slot = cfg.install_dir().resolve(), Path(sys.prefix).resolve()
        if slot.parent != root / "versions":
            raise RuntimeError("Dev release must execute from this installation's retained slot")
        # The mutable dev slot cannot use the immutable launcher's admission.
        # Keep its code pinned until the canonical caller-owned drain settles.
        with lifecycle.runtime.retention_lease(root, slot.name):
            with redirect_stdout(buffer):
                code = _run_request(
                    "dev-release", flags, execution, execute, retained_dev=slot.name == "dev",
                )
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        return _error(args, error)
    text = buffer.getvalue()
    if getattr(args, "json", False):
        output.write_real_stdout(text)
    elif code:
        record = json.loads(text)
        output.err(record.get("error") or record.get("message") or "Dev release failed")
    else:
        result = json.loads(text)
        output.info(f"Dev released; current-version -> {result['runtime_version']}")
    return code


def _cmd_dev_release(args: argparse.Namespace) -> int:
    lifecycle = None
    try:
        lifecycle = _lifecycle()
        import runtime_install_modes as modes

        root = cfg.install_dir().resolve()
        if Path(sys.prefix).resolve().parent != root / "versions":
            raise RuntimeError("Dev release must execute from this installation's retained slot")
        payload = registry_paths._payload_root()
        # Legacy mode needs no checkout identity. Namespaced mode still requires
        # the explicitly authorized installed payload, not the claiming worktree.
        if not lifecycle.os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
            payload = lifecycle.SCRIPTS
        governance = lifecycle.Governance(root, payload)
        owner = modes._resolve_owner(
            Path.cwd(), getattr(args, "owner", None), source_required=False,
        )
        with output.stdout_to_stderr():
            result = modes.run_owned(
                root, lifecycle.SCRIPTS, payload=payload,
                mode="dev-release", owner=owner,
                expected_governance=governance.signature(),
            )
        if getattr(args, "json", False):
            output._json_output(result)
        else:
            output.info(f"Dev released; current-version -> {result['runtime_version']}")
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
        code = 2 if lifecycle is not None and isinstance(
            error, lifecycle.runtime.DevClaimConflict,
        ) else 1
        return _error(args, error, code)
