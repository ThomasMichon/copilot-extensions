"""Explicit installer adapter; snapshot production and activation have separate owners."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
PAYLOAD = Path(__file__).resolve().parent.parent
PLUGIN = "agent-pull-requests"
METADATA = {
    "snapshot-stamp": "stamp_snapshot_provenance",
    "snapshot-validate": "validate_snapshot_provenance",
    "slot-provision": "provision_runtime_slot",
    "slot-validate": "validate_runtime_slot_ownership",
    "slot-complete": "complete_runtime_slot",
    "slot-completion-validate": "validate_runtime_slot_completion",
}
LIBRARIES = (
    "agent-procutil", "single-instance-lease", "work-coalescing-singleton", "zdd",
)


def foundation():
    path = PAYLOAD / "src" / "agent_pull_requests" / "_installation_context.py"
    spec = importlib.util.spec_from_file_location("apr_installer_context", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def process_helpers():
    library = PAYLOAD / "libs" / "agent-procutil"
    if not library.is_dir():
        library = PAYLOAD.parent.parent / "libs" / "agent-procutil"
    path = library / "src" / "agent_procutil" / "__init__.py"
    spec = importlib.util.spec_from_file_location("apr_installer_procutil", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare_snapshot(api, common, version):
    spec = importlib.util.spec_from_file_location(
        "apr_snapshot_producer", PAYLOAD / "scripts" / "cell_snapshot.py",
    )
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    return producer.prepare(payload=PAYLOAD, version=version, api=api, common=common)


def run(command, *, environment=None, cwd=None):
    helpers = process_helpers()
    process, job = helpers.spawn_sync_in_kill_on_close_job(
        command, require_job=os.name == "nt", env=environment, cwd=cwd,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **helpers.no_window_kwargs(),
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    try:
        output, error = process.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        if job is not None:
            job.terminate_and_wait(5)
        elif process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        raise
    finally:
        if job is not None:
            job.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    if process.returncode:
        raise ValueError(
            f"Cell installer subprocess failed ({process.returncode}): "
            f"{error.strip() or output.strip()}"
        )
    if output:
        print(output.rstrip(), file=sys.stderr)
    return output


def build(slot: Path, snapshot: Path, payload_version: str):
    """Build a disposable copy, never run a backend against immutable snapshot bytes."""
    for library in LIBRARIES:
        if not (snapshot / "libs" / library / "pyproject.toml").is_file():
            raise ValueError(
                f"Snapshot lacks materialized libs/{library}; prepare a self-contained "
                "snapshot with the canonical release materializer before provisioning"
            )
    engine = snapshot / "scripts" / (
        "installer-engine.ps1" if os.name == "nt" else "installer-engine.sh"
    )
    if not engine.is_file():
        raise ValueError("Snapshot lacks its materialized installer-engine helper")
    if not (snapshot / "pyproject.toml").is_file():
        raise ValueError("Snapshot lacks pyproject.toml")

    # Atomic mkdir is the build claim. Failed/partial builds remain attributable and
    # blocked, never deleted or rebuilt by a second invocation or a force flag.
    workspace = slot / ".installer-build"
    workspace.mkdir()
    source = workspace / "payload"
    shutil.copytree(snapshot, source)
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.update({
        # Dependency policy attributes local sources to this helper's payload.
        "APR_CELL_ENGINE": str(source / "scripts" / engine.name),
        "APR_CELL_SLOT": str(slot),
        "APR_CELL_SOURCE": str(source),
        "APR_CELL_PACKAGES": json.dumps([
            *(str(source / "libs" / name) for name in LIBRARIES), str(source),
        ]),
        "UV_PYTHON_INSTALL_DIR": str(workspace / "python"),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_LINK_MODE": "copy",
    })
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell")
        if not host:
            raise ValueError("PowerShell is required for signed-venv construction")
        script = r"""
$ErrorActionPreference = 'Stop'
function Write-Step($Message) { [Console]::Error.WriteLine($Message) }
function Write-Ok($Message) { [Console]::Error.WriteLine($Message) }
function Write-Warn($Message) { [Console]::Error.WriteLine($Message) }
function Write-Fail($Message) { [Console]::Error.WriteLine($Message) }
. $env:APR_CELL_ENGINE
$uv = Ensure-Uv -InstallRoot $env:APR_CELL_SLOT -AcquireIfMissing $false
if (-not $uv) { throw 'Cell provisioning requires an existing working uv' }
$python = Join-Path $env:APR_CELL_SLOT 'Scripts\python.exe'
if (-not (New-SignedVenv -VenvDir $env:APR_CELL_SLOT -VenvPython $python `
    -UvCommand $uv -RequireSignedBase $true -AllowExisting $true)) {
    throw 'Signed-venv construction failed'
}
"""
        run([host, "-NoProfile", "-NonInteractive", "-Command", script],
            environment=environment, cwd=source)
        script = r"""
$ErrorActionPreference = 'Stop'
function Write-Warn($Message) { [Console]::Error.WriteLine($Message) }
. $env:APR_CELL_ENGINE
$uv = Ensure-Uv -InstallRoot $env:APR_CELL_SLOT -AcquireIfMissing $false
if (-not $uv) { throw 'Cell provisioning requires an existing working uv' }
$python = Join-Path $env:APR_CELL_SLOT 'Scripts\python.exe'
$packages = @($env:APR_CELL_PACKAGES | ConvertFrom-Json)
$result = Invoke-UvPipInstallResilient -UvCommand $uv `
    -Arguments (@('--python', $python, '--no-sources', '--quiet') + $packages)
if ($result.ExitCode -ne 0) { throw "Package construction failed: $($result.Output)" }
"""
        run([host, "-NoProfile", "-NonInteractive", "-Command", script],
            environment=environment, cwd=source)
    else:
        script = r"""
set -euo pipefail
_step() { printf '%s\n' "$*" >&2; }
_ok() { _step "$@"; }
_warn() { _step "$@"; }
_fail() { _step "$@"; }
. "$APR_CELL_ENGINE"
uv="$(ensure_uv "$APR_CELL_SLOT" tool 0)" ||
    { _fail 'Cell provisioning requires an existing working uv'; exit 1; }
new_signed_venv "$uv" "$APR_CELL_SLOT" 3.10
packages=()
for name in agent-procutil single-instance-lease work-coalescing-singleton zdd; do
    packages+=("$APR_CELL_SOURCE/libs/$name")
done
invoke_uv_pip_install_resilient "$uv" --python "$APR_CELL_SLOT/bin/python" \
    --no-sources --quiet "${packages[@]}" "$APR_CELL_SOURCE"
"""
        run(["bash", "--noprofile", "--norc", "-c", script],
            environment=environment, cwd=source)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([
        str(python), "-I", "-c",
        "import agent_pull_requests, agent_procutil, single_instance_lease, "
        "work_coalescing_singleton, zdd; "
        f"assert agent_pull_requests.__version__ == {payload_version!r}",
    ], environment=environment)
    # Retain the empty claim directory: removing it would let a delayed builder
    # reuse an earlier slotEmpty observation before completion is published.
    for child in workspace.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def activate(command):
    # This controller owns its bounded, journaled cutover and graceful child
    # custody. A construction Job would kill its committed successor in
    # contained tests. Keep the caller's outer containment, not a second Job.
    # Windows execv is not a status-preserving exec, so wait for the real target.
    completed = subprocess.run(  # noqa: S603 -- own canonically completed activation target
        command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        check=False, **process_helpers().no_window_kwargs(),
    )
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    if completed.returncode:
        raise ValueError(f"Completed target activation failed ({completed.returncode})")
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or (
        result.get("status"), result.get("activated")
    ) != ("ready", True):
        raise ValueError("Target activation did not return status=ready and activated=true")
    return result


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        *METADATA, "snapshot-prepare", "install", "update", "provision", "status",
    ))
    parser.add_argument("--context", required=True)
    parser.add_argument("--expected-marketplace-id", required=True)
    parser.add_argument("--durable-home")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--runtime-version")
    parser.add_argument("--expected-namespace-generation", type=int)
    parser.add_argument("--expected-install-generation", type=int)
    args = parser.parse_args(argv)
    if not args.context.strip():
        parser.error("explicit --context is required; ambient context is not authorization")
    if args.action in METADATA and not args.snapshot_id:
        parser.error("--snapshot-id is required (snapshot preparation is a separate operation)")
    if args.action.startswith("slot-"):
        if not args.runtime_version:
            parser.error("--runtime-version is required")
    if args.action == "snapshot-stamp" and (
        args.expected_namespace_generation is None or args.expected_install_generation is None
    ):
        parser.error("snapshot-stamp requires both expected receipt generations")
    return args


def management_version():
    version_match = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"', (PAYLOAD / "pyproject.toml").read_text("utf-8"),
    )
    if not version_match:
        raise ValueError("Management payload version is unavailable")
    version = version_match[1]
    if json.loads((PAYLOAD / "plugin.json").read_text("utf-8")).get("version") != version:
        raise ValueError("Management package and plugin versions differ")
    return version


def execute(args, api):
    version = management_version()
    common = {
        "context": args.context,
        "expected_marketplace_id": args.expected_marketplace_id,
        "expected_plugin_id": PLUGIN,
        "durable_home": args.durable_home,
    }
    bound = {
        **common, "snapshot_id": args.snapshot_id,
        "expected_payload_root": str(PAYLOAD), "expected_payload_version": version,
    }
    if args.action == "snapshot-prepare":
        return prepare_snapshot(api, common, version)
    if args.action == "snapshot-stamp":
        # Validate payload identity before publishing metadata, not after.
        api.resolve_context(**common, expected_payload_root=str(PAYLOAD), environment={})
        return api.stamp_snapshot_provenance(
            **common, snapshot_id=args.snapshot_id,
            expected_namespace_generation=args.expected_namespace_generation,
            expected_install_generation=args.expected_install_generation,
        )
    if args.action in METADATA:
        keywords = bound.copy()
        if args.action != "snapshot-validate":
            keywords["runtime_version"] = args.runtime_version
        return getattr(api, METADATA[args.action])(**keywords)

    status = api.resolve_installation_mode(
        **common, payload_root=str(PAYLOAD), expected_payload_root=str(PAYLOAD),
        legacy_root=Path.home() / ".agent-pull-requests",
    )
    if args.action == "status":
        return status
    if (status["status"], status["reason"], status["actualMode"]) != (
        "ready", "namespaced-active", "namespaced",
    ):
        raise ValueError(
            "Cell build requires governed namespaced activation; "
            f"status={status['status']} reason={status['reason']}. "
            "Legacy attribution/migration is a separate operation."
        )
    if args.runtime_version is None:
        args.runtime_version = version
    if args.runtime_version != version:
        raise ValueError("Built runtime version must match the management payload package version")
    if not args.snapshot_id:
        snapshot = prepare_snapshot(api, common, version)
        args.snapshot_id = snapshot["snapshotId"]
        bound["snapshot_id"] = args.snapshot_id
    slot_args = {**bound, "runtime_version": args.runtime_version}
    ownership = api.provision_runtime_slot(**slot_args)
    slot = Path(ownership["slotRoot"])
    if os.path.lexists(slot / ".runtime-slot-completion.json"):
        completed = api.validate_runtime_slot_completion(**slot_args)
    else:
        if not ownership["slotEmpty"]:
            raise ValueError(
                "Incomplete non-empty cell slot requires explicit repair; never overwrite"
            )
        snapshot = api.validate_snapshot_provenance(**bound)
        root = Path(snapshot["snapshotRoot"])
        digest = api._snapshot_content_sha256(root)
        build(slot, root, version)
        api.validate_runtime_slot_ownership(**slot_args)
        if api._snapshot_content_sha256(root) != digest:
            raise ValueError("Snapshot changed during package construction; completion refused")
        with (slot / ".install-complete.json").open("x", encoding="utf-8") as stream:
            json.dump({
                "version": args.runtime_version,
                "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "pid": os.getpid(), "payload_hash": digest,
            }, stream)
        completed = api.complete_runtime_slot(**slot_args)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise ValueError("Completed cell slot has no target Python; explicit repair required")
    if args.action == "provision":
        return completed
    return activate([
        str(python), "-I", "-m", "agent_pull_requests.watch_install",
        "--context", args.context, "--payload-root", str(PAYLOAD),
        "--snapshot-id", args.snapshot_id, "--runtime-version", args.runtime_version,
    ])


def main(argv=None):
    args = parse(argv)
    os.chdir(Path.home())
    try:
        result = execute(args, foundation())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Cell installer refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
