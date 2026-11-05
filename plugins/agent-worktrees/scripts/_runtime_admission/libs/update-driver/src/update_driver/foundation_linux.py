"""Linux producer continuation under the native owner's inherited flock."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys

from single_instance_lease.operation import OperationLock

from . import foundation_state
from .foundation_inputs import FoundationInputError, check_health, inspect
from .participant import Witness


def _plain(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if path.resolve() != path or not expected(info.st_mode):
        raise FoundationInputError("foundation producer requires an ordinary unlinked path")


def _private_python(bootstrap: Path) -> Path:
    python = bootstrap / "venv" / "bin" / "python"
    _plain(bootstrap / "venv", directory=True)
    _plain(bootstrap / "venv" / "pyvenv.cfg")
    base = python.resolve(strict=True)
    managed = bootstrap / "python"
    _plain(managed, directory=True)
    _plain(base)
    if not base.is_relative_to(managed):
        raise FoundationInputError("foundation interpreter must use its private managed base")
    return python


def _uv(bootstrap: Path) -> Path:
    path = bootstrap / "uv-path"
    _plain(path)
    raw = path.read_bytes()
    if len(raw) > 4096 or not raw.endswith(b"\0") or b"\0" in raw[:-1]:
        raise FoundationInputError("malformed owned acquisition result")
    result = Path(os.fsdecode(raw[:-1]))
    if not result.is_absolute() or not result.is_file() or not os.access(result, os.X_OK):
        raise FoundationInputError("canonical acquisition did not return an executable uv")
    return result


def _run(argv: list[str], log: Path, timeout: int) -> int:
    from agent_procutil.owned_process import run_caller_owned

    result = run_caller_owned(
        argv, timeout=timeout, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    with log.open("ab") as stream:
        stream.write(((result.stdout or "") + (result.stderr or "")).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    return result.returncode


def produce(
    *, root: Path, source: Path, source_hash: str, child: int | None = None,
    child_code: int | None = None,
) -> int:
    if sys.platform != "linux":
        raise FoundationInputError("native foundation continuation requires Linux")
    os.environ.pop("UPDATE_DRIVER_WITNESS", None)
    native = None
    if child is not None:
        from agent_procutil.native_child import NativeChildCustody

        # Only a direct live unreaped session child can cross the Bash exec seam.
        native = NativeChildCustody(child)
        os.set_inheritable(8, False)
    operation = None
    custody = None
    try:
        custody = OperationLock.from_inherited(root / "execution.lock", 9)
        if native is not None:
            native.retire()
            os.close(8)
            native = None
        check_health()
        _plain(root, directory=True)
        selected = inspect(source)
        if selected.digest != source_hash or any(root.is_relative_to(path) for path in selected.packages):
            raise FoundationInputError("foundation source differs from native admission")
        if child is not None:
            operation = foundation_state.read(root)
            if operation is None or operation.state != "running" or operation.source_hash != source_hash:
                raise FoundationInputError("foundation continuation differs from native progress")
            bootstrap = root / "bootstrap" / operation.operation_id
            frozen = bootstrap / "inputs" / "update-driver"
            if inspect(frozen).digest != source_hash:
                raise FoundationInputError("native frozen source changed before continuation")
            if type(child_code) is not int or not 0 <= child_code <= 255:
                raise FoundationInputError("native acquisition returned no canonical exit status")
            if child_code:
                foundation_state.finish(
                    root, operation, custody, exit_code=child_code, error="prerequisite_failed",
                )
                return child_code
            if sys.version_info < (3, 11) or Path(sys._base_executable).resolve() != _private_python(bootstrap).resolve():
                raise FoundationInputError("native continuation did not use its acquired private Python")
        else:
            prior = foundation_state.read(root)
            target = root / "foundation"
            selector = target / "current-version"
            _plain(selector)
            selected_version = selector.read_text(encoding="utf-8").strip()
            if prior is not None and prior.state == "completed" and prior.source_hash == source_hash:
                if selected_version == "foundation-" + source_hash:
                    return 0
            # The retained module validates the canonical queue before replacing progress.
            operation = foundation_state.begin(root, source_hash, custody)
            bootstrap = root / "bootstrap" / operation.operation_id
            bootstrap.parent.mkdir(exist_ok=True, mode=0o700)
            _plain(bootstrap.parent, directory=True)
            bootstrap.mkdir(mode=0o700)
            # This is the same fixed closure and canonical freezer as construction.
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "_foundation_source_engine", source / "scripts" / "installer_engine.py",
            )
            engine = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = engine
            sys.path.insert(0, str(source / "scripts"))
            try:
                spec.loader.exec_module(engine)
                frozen = engine.freeze_inputs(source, bootstrap / "inputs").plugin
            finally:
                sys.path.pop(0)
                sys.modules.pop(spec.name, None)
            if inspect(frozen).digest != source_hash or inspect(source).digest != source_hash:
                raise FoundationInputError("source changed during foundation freeze")
            bash = shutil.which("bash")
            if bash is None:
                raise FoundationInputError("foundation acquisition requires Bash")
            code = _run(
                [bash, str(frozen / "scripts" / "foundation-prerequisites.sh"), str(bootstrap)],
                bootstrap / "prerequisites.log", 300,
            )
            if code:
                foundation_state.finish(
                    root, operation, custody, exit_code=code, error="prerequisite_failed",
                )
                return code
        python, uv = _private_python(bootstrap), _uv(bootstrap)
        custody.assert_owned()
        if inspect(source).digest != source_hash or inspect(frozen).digest != source_hash:
            raise FoundationInputError("foundation source changed before construction")
        os.environ["UV_CACHE_DIR"] = str(bootstrap / "cache")
        os.environ["UV_PYTHON_INSTALL_DIR"] = str(bootstrap / "python")
        witness = Witness(root, operation.operation_id)
        try:
            os.environ.update(witness.environment())
            try:
                code = _run([
                    str(python), "-I", "-B", "-X", "utf8",
                    str(frozen / "scripts" / "foundation-build.py"),
                    "--root", str(root), "--source-hash", source_hash, "--uv", str(uv),
                    "--base-python", str(python), "--admitted-source", str(source),
                ], root / f"foundation-{operation.operation_id}.log", 300)
            finally:
                os.environ.pop("UPDATE_DRIVER_WITNESS", None)
        finally:
            witness.close()
        custody.assert_owned()
        if inspect(source).digest != source_hash or inspect(frozen).digest != source_hash:
            raise FoundationInputError("foundation source changed after construction")
        foundation_state.finish(
            root, operation, custody, exit_code=code, error=None if code == 0 else "builder_failed",
        )
        return code
    except BaseException:
        if custody is not None and operation is not None:
            foundation_state.finish(
                root, operation, custody, exit_code=None, error="producer_interrupted",
            )
        raise
    finally:
        if native is not None:
            native.retire()
            os.close(8)
        if custody is not None:
            custody.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-hash", required=True)
    parser.add_argument("--child", type=int)
    parser.add_argument("--child-code", type=int)
    args = parser.parse_args()
    return produce(
        root=args.root, source=args.source, source_hash=args.source_hash,
        child=args.child, child_code=args.child_code,
    )


if __name__ == "__main__":
    raise SystemExit(main())
