"""Fixed native installation and broker handoffs under the suite driver."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import stat
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from functools import wraps

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
NATIVE_ENV = "AGENT_WORKTREES_NATIVE_INSTALLATION"
CONTEXT_KEYS = (
    "AGENT_HOME", "AGENT_WORKTREES_UPDATE_CONTEXT", "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "WORKTREE_MANAGER_ROOT",
    "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_ENGINE_CMD", "CE_UPDATE_AUTH",
    "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", "COPILOT_PROJECT_DIR",
)
TRANSIENT_KEYS = (
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_PLUGIN_INSTALL_STAGED",
    "COPILOT_PLUGIN_STAGED_FROM", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", NATIVE_ENV,
    "AGENT_WORKTREES_FIRST_USE_NO_WAIT", "AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT",
)


def positive_input_timeout(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("input timeout must be positive and finite")
    return seconds


def bootstrap(payload: Path, *, stamp: bool = False):
    required = {"agent-update-driver", "agent-single-instance-lease", "agent-procutil", "agent-zdd"}
    if stamp:
        base = payload / "scripts" / "_runtime_admission" / "libs"
        paths = [base / name / "src" for name in (
            "update-driver", "single-instance-lease", "agent-procutil", "zdd",
        )]
        for path in paths:
            try:
                info = path.lstat()
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise RuntimeError("Worktrees stamp requires ordinary admission sources") from error
            if (
                not stat.S_ISDIR(info.st_mode) or resolved != path
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise RuntimeError("Worktrees stamp requires ordinary admission sources")
    else:
        import installer_engine as engine

        packages = {
            engine._package_name(package): package for package in engine._package_roots(payload)
            if engine._package_name(package) in required
        }
        if packages.keys() != required:
            raise RuntimeError("Worktrees installation requires its complete execution-driver source closure")
        paths = [packages[name] / "src" for name in sorted(required)]
    sys.path[:0] = [str(path) for path in paths]
    from update_driver import core

    return core


@contextmanager
def custody(*, required: bool = False):
    from update_driver import core, borrowed_custody, participant, participant_environment
    from update_driver.participant import ENVIRONMENT_KEY

    owner = borrowed_custody(root=core.default_root())
    if owner is None:
        inherited = participant_environment()
        if inherited:
            owner = participant(root=core.default_root(), witness=inherited[ENVIRONMENT_KEY])
    if owner is None:
        if required:
            raise RuntimeError("Worktrees installation requires live driver custody")
        yield None
    else:
        with owner, owner.execution_scope():
            yield owner


@contextmanager
def required_custody():
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        yield None
    else:
        if (SCRIPTS.parent / "pyproject.toml").is_file():
            bootstrap(SCRIPTS.parent)
        with custody(required=True) as owner:
            yield owner


def owned(function):
    @wraps(function)
    def execute(*args, **kwargs):
        with required_custody():
            return function(*args, **kwargs)

    return execute


def child_environment() -> dict[str, str]:
    driver = sys.modules.get("update_driver")
    inherited = driver.participant_environment() if driver is not None else {}
    return {**os.environ, **inherited}


def adopt_broker(root: Path, slot: Path) -> None:
    """Adopt before publication or callbacks, with the complete slot retained."""
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        return
    import versioned_runtime as runtime
    from update_driver import core

    with custody(required=True) as owner:
        owner.assert_owned()
        python = runtime.slot_python(root, slot.name)
        if python is None:
            raise RuntimeError("Constructed Worktrees has no broker adoption interpreter")
        command = [
            str(python), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(root), "--slot", str(slot),
            "--execution-root", str(core.default_root()),
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            env=child_environment(),
        )
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            _settle(process.communicate)
            raise
        if process.returncode:
            if stderr:
                print(stderr, end="", file=sys.stderr, flush=True)
            raise RuntimeError(f"Constructed Worktrees broker adoption failed ({process.returncode})")
        receipt = json.loads(stdout)
        if not isinstance(receipt, dict) or set(receipt) != {"adopted"} or receipt["adopted"] is not True:
            raise RuntimeError("Constructed Worktrees returned no broker adoption receipt")
        owner.assert_owned()


def _settle(wait):
    while True:
        try:
            return wait()
        except (KeyboardInterrupt, SystemExit):
            continue


def _source_digest(payload: Path) -> str:
    import installer_engine as engine

    return engine.runtime.fingerprint_source(engine._source_files(engine._package_roots(payload)))


def _admission_digest(args) -> str:
    if args.action != "stamp":
        return _source_digest(args.payload)
    import versioned_runtime_source as source

    for name in (source.SOURCE_FILE, "snapshot-provenance.json"):
        path = args.payload / name
        if not os.path.lexists(path):
            continue
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise RuntimeError("Worktrees stamp source metadata is not an ordinary file")
    qualified = os.path.lexists(args.payload / source.SOURCE_FILE)
    excluded = {".git", "__pycache__", ".venv", "node_modules", "build", "dist",
                ".pytest_cache", ".mypy_cache", "tests"}
    files = []

    def scan_error(error):
        raise error

    def require_ordinary(path, *, directory):
        info = path.lstat()
        if (
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise RuntimeError("Worktrees stamp source contains a nonordinary entry")

    require_ordinary(args.payload, directory=True)
    for current, directories, names in os.walk(args.payload, onerror=scan_error, followlinks=False):
        parent = Path(current)
        # Match the ordinary stamp copier; source-qualified snapshots copy the entire tree.
        if not qualified:
            directories[:] = [
                name for name in directories
                if name.casefold() != "__pycache__"
                and not (parent == args.payload and name.casefold() in excluded)
            ]
            if parent == args.payload:
                names = [name for name in names if name.casefold() not in excluded]
        for name in directories:
            require_ordinary(parent / name, directory=True)
        for name in names:
            path = parent / name
            require_ordinary(path, directory=False)
            files.append(path)
    if not files:
        raise RuntimeError("Worktrees stamp source is empty")
    return source._runtime().fingerprint_source(files)


def _native_arguments(args) -> list[str]:
    values = ["-Action", args.action, "-InstallDir", str(args.root.resolve())]
    for option in ("project_name", "bootstrap_python", "owner", "installation_payload", "install_source", "input_timeout"):
        value = getattr(args, option, None)
        if value is not None:
            values.extend(["-" + "".join(word.title() for word in option.split("_")), str(value)])
    for option in ("force", "zero_downtime", "provision_missing_runtime", "remove_config", "runtime_only"):
        if getattr(args, option, False):
            values.append("-" + "".join(word.title() for word in option.split("_")))
    if getattr(args, "source_descriptor", None) is not None:
        values.extend(["-RuntimeSourceDescriptor", json.dumps(args.source_descriptor, sort_keys=True)])
    return values


def _uninstall_policy():
    spec = importlib.util.spec_from_file_location("_worktrees_uninstall_policy", SCRIPTS / "uninstall-state.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Worktrees uninstall policy is unavailable")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    return policy


def _uninstall_runtime(args, owner) -> int:
    from update_driver.windows_broker import remove

    if args.action != "uninstall":
        raise RuntimeError("Runtime removal requires the owning uninstall action")
    policy = _uninstall_policy()
    arguments = dict(
        payload_root=args.payload, plugin_id="agent-worktrees", root=args.root,
        shell=str(args.shell), profile=args.payload / "scripts" / "uninstall-profile.json",
        controller_custody=owner, purge=args.remove_config,
        project_name=args.project_name if args.project_name != "agent-worktrees" else None,
    )
    owner.assert_owned()
    admitted = policy.cleanup(**arguments, check_consumers=True)
    if args.check_removal:
        print(json.dumps(admitted), flush=True)
        return 0
    owner.assert_owned()
    remove(args.root)
    owner.assert_owned()
    result = policy.cleanup(**arguments, remove_runtime=True)
    owner.assert_owned()
    print(json.dumps(result), flush=True)
    return 0


def _selected_snapshot_current(args) -> bool:
    selected = getattr(args, "selected_snapshot", None)
    absent = getattr(args, "expect_no_snapshot", False)
    if selected is None and not absent:
        return True
    pointer = args.root / "payload-dir"
    try:
        info = pointer.lstat()
    except FileNotFoundError:
        return absent
    except OSError:
        return False
    if absent:
        return False
    try:
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            return False
        current = Path(pointer.read_text(encoding="utf-8-sig").strip())
        if not current.is_absolute() or not selected.is_absolute():
            return False
        return current.resolve(strict=True) == selected.resolve(strict=True) == args.payload.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False


def _execute(args) -> int:
    from update_driver import ParticipantError, core

    with custody(required=True) as owner:
        request = core.status(owner.request_id, root=owner.root)
        arguments = _native_arguments(args)
        if request.state != "running":
            raise RuntimeError("Native installer has no running execution owner")
        if request.spec.backend == "engine-native-install":
            if (
                request.spec.argv != tuple(arguments)
                or request.spec.source != _admission_digest(args)
                or request.spec.project != str(args.root.resolve())
            ):
                raise RuntimeError("Native installation changed after driver admission")
        elif request.spec.backend not in {
            "manager", "engine", "engine-stage-update", "engine-reconcile-plugins",
            "engine-launch-update",
        }:
            raise RuntimeError("Execution owner does not authorize native Worktrees installation")
        owner.assert_owned()
        if not _selected_snapshot_current(args):
            print("::agent-worktrees-driver-reselect::", file=sys.stderr, flush=True)
            return 75
        if args.check_native:
            return 0
        if getattr(args, "check_removal", False) or getattr(args, "remove_runtime", False):
            return _uninstall_runtime(args, owner)
        environment = child_environment()
        environment.pop("AGENT_WORKTREES_FIRST_USE_NO_WAIT", None)
        environment.pop("AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT", None)
        environment[NATIVE_ENV] = "1"
        process = subprocess.Popen(
            [str(args.shell), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(args.payload / "scripts" / "install.ps1"),
             *arguments],
            env=environment, stdout=sys.stdout, stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            code = process.wait()
        except BaseException:
            _settle(process.wait)
            raise
        try:
            owner.assert_owned()
        except ParticipantError as error:
            error.add_note(
                f"Native Worktrees installer child exited {code}; transaction custody is unconfirmed",
            )
            raise
        if code:
            print(f"Native Worktrees installer failed (exit {code})", file=sys.stderr, flush=True)
        return code


def _foreground(args, driver) -> int:
    if any(os.environ.get(key) for key in (
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
    )):
        raise RuntimeError("Native driver admission must precede publication custody")
    arguments = _native_arguments(args)
    digest = _admission_digest(args)
    command = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(SCRIPTS / "runtime_install_driver.py"),
        "--payload", str(args.payload), "--root", str(args.root),
        "--shell", str(args.shell), "--action", args.action, "--participant",
    ]
    for option in ("project_name", "bootstrap_python", "owner", "installation_payload", "install_source", "input_timeout"):
        value = getattr(args, option, None)
        if value is not None:
            command.extend(["--" + option.replace("_", "-"), str(value)])
    if getattr(args, "selected_snapshot", None) is not None:
        command.extend(["--selected-snapshot", str(args.selected_snapshot)])
    if getattr(args, "expect_no_snapshot", False):
        command.append("--expect-no-snapshot")
    for option in ("force", "zero_downtime", "provision_missing_runtime", "remove_config", "runtime_only"):
        if getattr(args, option, False):
            command.append("--" + option.replace("_", "-"))
    if getattr(args, "source_descriptor", None) is not None:
        command.extend(["--source-descriptor", json.dumps(args.source_descriptor, sort_keys=True)])
    context = {key: os.environ[key] for key in CONTEXT_KEYS if key in os.environ}
    spec = driver.RequestSpec(
        "engine-native-install", tuple(arguments), project=str(args.root.resolve()),
        cwd=str(Path.cwd().resolve()), source=digest, context=tuple(context.items()),
    )
    runner = driver.TrustedRunner(
        tuple(command), str(Path.cwd().resolve()), context_keys=(*CONTEXT_KEYS, *TRANSIENT_KEYS),
    )
    if getattr(args, "fail_if_busy", False):
        try:
            request = driver.run_explicit(spec, runner=runner)
        except driver.StateError as error:
            reason = str(error)
            if reason not in {
                "request-local execution is busy; no request admitted",
                "request-local execution requires an idle queue; nothing replayed",
                "request-local execution requires an idle, unfenced queue; nothing replayed",
            }:
                raise
            if reason.startswith("request-local execution requires") and any(
                item.state in {"pending", "interrupted", "blocked"} and item.unresolved
                for item in driver.status()
            ):
                raise driver.InterruptedError(
                    "first-use installation is fenced by unsettled prior work",
                ) from error
            print("::agent-worktrees-driver-busy::", file=sys.stderr, flush=True)
            return 75
    else:
        request = driver.enqueue(spec, runner=runner)
        driver.drain()
    if hasattr(sys.stderr, "reconfigure"):
        # The durable log already contains native Windows line endings.
        sys.stderr.reconfigure(newline="")
    result = driver.wait(request.id, output=sys.stderr)
    if result.state != "completed" or result.exit_code is None:
        raise RuntimeError(f"Native Worktrees installation did not complete: {result.state}")
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shell", required=True, type=Path)
    parser.add_argument("--action", required=True, choices=(
        "install", "update", "provision", "stamp", "repair", "dev", "dev-release", "uninstall",
    ))
    parser.add_argument("--project-name")
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--bootstrap-python", type=Path)
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--installation-payload", type=Path)
    parser.add_argument("--install-source", type=Path)
    parser.add_argument("--input-timeout", type=positive_input_timeout)
    parser.add_argument("--source-descriptor", type=json.loads, help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--zero-downtime", action="store_true")
    parser.add_argument("--provision-missing-runtime", action="store_true")
    parser.add_argument("--remove-config", action="store_true")
    parser.add_argument("--participant", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fail-if-busy", action="store_true", help=argparse.SUPPRESS)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--selected-snapshot", type=Path, help=argparse.SUPPRESS)
    selection.add_argument("--expect-no-snapshot", action="store_true", help=argparse.SUPPRESS)
    inspection = parser.add_mutually_exclusive_group()
    inspection.add_argument("--check-native", action="store_true", help=argparse.SUPPRESS)
    inspection.add_argument("--check-removal", action="store_true", help=argparse.SUPPRESS)
    inspection.add_argument("--remove-runtime", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.installation_payload is not None and (
        args.action not in ("dev", "dev-release")
        or not args.installation_payload.is_absolute()
        or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
    ):
        parser.error("--installation-payload requires an absolute legacy dev/dev-release payload")
    if args.runtime_only and (
        args.action not in ("install", "update") or args.project_name is not None
    ):
        parser.error("--runtime-only requires install/update without --project-name")
    if args.fail_if_busy and args.action not in ("stamp", "provision", "install"):
        parser.error("--fail-if-busy requires a first-use installation action")
    if (args.expect_no_snapshot or args.selected_snapshot is not None) and (
        args.action != "provision"
        or (args.selected_snapshot is not None and not args.selected_snapshot.is_absolute())
    ):
        parser.error("--selected-snapshot requires an absolute first-use provision source")
    if args.input_timeout is not None and not args.install_source:
        parser.error("--input-timeout requires --install-source")
    if args.install_source and args.action not in ("install", "update", "provision", "repair"):
        parser.error("--install-source selects only installation, update, provisioning or independent repair")
    if args.install_source and not args.install_source.is_absolute():
        parser.error("--install-source requires an absolute snapshot")
    if args.source_descriptor is not None and not args.install_source:
        parser.error("--source-descriptor requires --install-source")
    if os.name != "nt":
        parser.error("Native Worktrees driver entry requires Windows")
    args.payload = args.payload.resolve(strict=True)
    args.root = args.root.resolve()
    if args.action == "uninstall":
        if sys.version_info < (3, 11):
            parser.error("Native Worktrees uninstall requires an external Python 3.11+ interpreter")
        _uninstall_policy().external_python(args.root)
        for value in (
            sys.executable, sys.prefix, sys.base_prefix,
            getattr(sys, "_base_executable", sys.executable), args.bootstrap_python,
        ):
            if value is not None and any(
                candidate.is_relative_to(args.root)
                for candidate in (Path(value).absolute(), Path(value).resolve(strict=True))
            ):
                parser.error("Native Worktrees uninstall interpreter must be outside the removable runtime")
    args.shell = args.shell.resolve(strict=True)
    if args.shell.name.lower() not in {"pwsh.exe", "powershell.exe"}:
        parser.error("Native Worktrees installation requires an exact PowerShell executable")
    if args.payload != SCRIPTS.parent:
        parser.error("Native installation uses only this executing payload")
    if args.install_source:
        from install_source import select_project
        import versioned_runtime_source as sources

        selected = select_project(args.install_source, "agent-worktrees")
        if selected.source_descriptor is None:
            parser.error("--install-source requires portable repository source provenance")
        if args.source_descriptor is not None and args.source_descriptor != selected.source_descriptor:
            parser.error("installation source repository changed after driver admission")
        if sources.read_source_identity(
            args.payload, selected.identity.package_version, plugin="agent-worktrees",
        ) != selected.identity:
            parser.error("invocation payload differs from selected installation source")
        args.source_descriptor = selected.source_descriptor
    driver = bootstrap(args.payload, stamp=args.action == "stamp")
    with custody() as owner:
        if owner is None:
            if args.participant or args.check_native or args.check_removal or args.remove_runtime:
                raise RuntimeError("Native installation participant requires live driver custody")
            return _foreground(args, driver)
        return _execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
