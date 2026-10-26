"""Legacy Logger uninstall ownership and runtime-use admission."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from contextlib import ExitStack

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
RUNTIME_ENTRIES = (
    "versions", ".venv", "snapshots", "bin", "current-version", "last-known-good",
    "payload-dir", "stamped-version", "deploy-manifest.json", "running-version.json",
)


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Uninstall dependency unavailable: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _plain(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            value = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Indirect Logger uninstall ownership")


def validate(root: Path):
    _plain(root)
    if root == Path(root.anchor) or root == Path.home() or root in Path.home().parents:
        raise RuntimeError("Logger uninstall refuses a filesystem root or home ancestor")
    stage = root / ".install-stage"
    if SCRIPTS.is_relative_to(root) and not SCRIPTS.is_relative_to(stage):
        raise RuntimeError("Logger uninstall helpers are inside removable runtime content")
    interpreter = Path(sys.executable).absolute()
    if interpreter.is_relative_to(root) and not interpreter.is_relative_to(stage):
        raise RuntimeError("Logger uninstall requires an interpreter outside removable runtime content")
    for name in ("install.json", "runtime-deployment.json"):
        if os.path.lexists(root / name):
            raise RuntimeError("Legacy Logger uninstall refuses cell or managed deployment metadata")
    snapshots = root / "snapshots"
    _plain(snapshots)
    snapshot_children = []
    if snapshots.exists():
        for child in snapshots.iterdir():
            _plain(child)
            if child.name != ".construction-leases":
                snapshot_children.append(child)
    if not snapshot_children and not any(
        os.path.lexists(root / name) for name in RUNTIME_ENTRIES if name != "snapshots"
    ):
        return
    binding = _load("_logger_uninstall_binding", "command_binding.py")
    primitive = binding._load_primitive(SCRIPTS.parent)
    payload = Path(os.environ.get("COPILOT_PLUGIN_STAGED_FROM") or SCRIPTS.parent)
    arguments = dict(
        payload_root=payload, plugin_id="agent-logger", legacy_root=root,
        project_root=Path.cwd(),
    )
    decision = primitive.probe_legacy_entrypoint(**arguments)
    if decision["allowMutation"] is not True:
        raise RuntimeError(f"Logger uninstall ownership refused: {decision['probeReason']}")
    manifest = root / "deploy-manifest.json"
    if os.path.lexists(manifest):
        _plain(manifest)
        if not manifest.is_file():
            raise RuntimeError("Logger deployment manifest must be a plain file")
    else:
        marker = root / "payload-dir"
        _plain(marker)
        snapshot = Path(marker.read_text(encoding="utf-8-sig").strip())
        if not snapshot.is_absolute():
            raise RuntimeError("Logger stamped ownership must be absolute")
        _plain(snapshot)
        snapshot = snapshot.resolve(strict=True)
        if not snapshot.is_relative_to(root / "snapshots"):
            raise RuntimeError("Logger stamped ownership is outside its snapshots")
        project = snapshot / "pyproject.toml"
        _plain(project)
        section = re.search(
            r"(?ms)^\[project\][ \t]*$\s*(.*?)(?=^\[|\Z)",
            project.read_text(encoding="utf-8-sig"),
        )
        if section is None or re.search(
            r'''(?m)^name[ \t]*=[ \t]*(["'])agent-logger\1[ \t]*(?:\#.*)?$''',
            section[1],
        ) is None:
            raise RuntimeError("Logger stamped ownership belongs to another product")
    receipt = None
    try:
        receipt = binding.publish_legacy_binding(primitive, **arguments)
    except binding.SourceProvenanceUnavailable:
        if manifest.exists() and primitive.read_json(manifest).get("commandLifecycle") is not None:
            raise RuntimeError("Logger lifecycle ownership cannot be revalidated") from None
    return primitive, receipt


def refuse_live_runtime(root: Path) -> None:
    runtime = _load("_logger_uninstall_runtime", "versioned_runtime.py")
    pids = runtime._iter_all_pids()
    if os.getpid() not in pids:
        raise RuntimeError("Logger process inventory is unavailable or missing the inspector; uninstall refused")
    if runtime._versions_with_live_process(root):
        raise RuntimeError("Active Logger runtime; uninstall refused")
    prefix = str(root).replace("\\", "/").casefold() + "/"
    for pid in pids:
        if pid == os.getpid():
            continue
        for path in (runtime._pid_image_path(pid), runtime._pid_cmdline_argv0(pid)):
            if path and path.replace("\\", "/").casefold().startswith(prefix):
                raise RuntimeError(f"Active Logger runtime process {pid}; uninstall refused")
    if os.name != "nt":
        if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
            raise RuntimeError("Logger uninstall requires supported native process inspection")
        for pid in pids:
            if pid == os.getpid():
                continue
            directory = Path("/proc") / str(pid)
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                command = (directory / "cmdline").read_bytes().replace(b"\0", b" ")
            except FileNotFoundError:
                continue
            except PermissionError as error:
                raise RuntimeError("Cannot inspect a current-user Logger runtime consumer") from error
            if b"agent_logger" in command:
                raise RuntimeError(f"Unattributed Logger runtime process {pid}; uninstall refused")


def remove_runtime(root: Path, shell: str) -> None:
    directory = root / "snapshots"
    names = sorted(
        child.name for child in directory.iterdir() if child.name != ".construction-leases"
    ) if directory.exists() else []
    leases = _load("_logger_uninstall_leases", "versioned_runtime_leases.py")
    runtime = _load("_logger_removal_runtime", "versioned_runtime.py")
    versions = runtime.list_versions(root)
    with ExitStack() as custody:
        for name in names:
            custody.enter_context(leases.construction_lease(directory, name, timeout=0))
        if versions:
            custody.enter_context(leases.batch_removal_custody(root, versions))
        custody.enter_context(leases._kernel_lease(
            root, "redirects", kind="publication", shared=False, timeout=0,
        ))
        current_names = sorted(
            child.name for child in directory.iterdir() if child.name != ".construction-leases"
        ) if directory.exists() else []
        if current_names != names or runtime.list_versions(root) != versions:
            raise RuntimeError("Logger snapshots changed during uninstall admission")
        if validate(root) is None:
            return
        refuse_live_runtime(root)
        filename = "installer-engine.ps1" if os.name == "nt" else "installer-engine.sh"
        engine = SCRIPTS / filename
        if not engine.is_file():
            engine = SCRIPTS.parents[2] / "libs" / "installer-engine" / filename

        def remove(target: Path, entries) -> None:
            if not entries:
                return
            if os.name == "nt":
                def quote(value):
                    return "'" + str(value).replace("'", "''") + "'"
                command = [
                    shell, "-NoProfile", "-NonInteractive", "-Command",
                    f"$ErrorActionPreference='Stop'; . {quote(engine)}; "
                    f"Remove-InstallRuntimeContent -InstallRoot {quote(target)} "
                    f"-EntryNames @({','.join(quote(name) for name in entries)})",
                ]
            else:
                command = [
                    shell, "-c", '. "$1"; shift; remove_install_runtime_content "$@"',
                    "logger-uninstall", str(engine), str(target), *entries,
                ]
                spec = importlib.util.spec_from_file_location(
                    "_logger_uninstall_process", engine.with_name("installer_engine_process.py"),
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError("Logger cleanup process supervisor is unavailable")
                supervisor = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(supervisor)
                try:
                    supervisor.run_owned(
                        command, cwd=Path.cwd(), env=dict(os.environ), timeout=90,
                        procutil=None, construction_fd=None,
                    )
                except subprocess.CalledProcessError as error:
                    raise RuntimeError(f"Logger runtime removal failed: {error.stderr.strip()}") from error
                return
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=90, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise RuntimeError(f"Logger runtime removal failed: {result.stderr.strip()}")

        remove(directory, names)
        remove(root, ("versions", ".venv", "bin", "current-version", "last-known-good", "running-version.json"))
        for name in ("stamped-version", "payload-dir", "deploy-manifest.json"):
            remove(root, (name,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--idle", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--remove-runtime", action="store_true")
    parser.add_argument("--shell")
    args = parser.parse_args()
    try:
        if ".." in Path(args.root).parts:
            raise RuntimeError("Logger uninstall requires a root without parent-traversal components")
        root = Path(os.path.abspath(args.root))
        owner = validate(root)
        if args.idle:
            refuse_live_runtime(root)
        if args.prepare:
            if not args.idle:
                raise RuntimeError("Logger uninstall preparation requires runtime-use admission")
            manifest = root / "deploy-manifest.json"
            if owner is not None and not manifest.exists():
                primitive, receipt = owner
                record = {"schema_version": 3, "service": "agent-logger"}
                if receipt is not None:
                    record["commandBinding"] = receipt
                # Preserve authenticated stamped ownership even if snapshot
                # deletion fails after removing its project metadata.
                primitive._atomic_write_json(manifest, record)
        if args.remove_runtime:
            if not args.prepare or not args.shell:
                raise RuntimeError("Runtime cleanup requires prepared ownership and a native shell")
            remove_runtime(root, args.shell)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Logger uninstall refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
