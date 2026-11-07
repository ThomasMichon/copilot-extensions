"""Run the native registration reader without selecting or changing a runtime."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine as engine
import versioned_runtime_source as sources


def inspect_registration(
    payload: Path, anchor: Path, default_name: str, *, uv: Path,
    install_source: Path | None = None,
) -> subprocess.CompletedProcess:
    if not anchor.is_absolute() or not anchor.is_dir():
        raise ValueError("Registration inspection requires an available absolute anchor")
    if not uv.is_absolute() or not uv.is_file():
        raise ValueError("Registration inspection requires an absolute UV executable")
    base_python = Path(sys.executable).resolve(strict=True)
    with ExitStack() as custody:
        options = {}
        if install_source is not None:
            from install_source import _member, native_selection, project_inputs

            selected, inputs = custody.enter_context(project_inputs(
                install_source, "agent-worktrees", deadline=time.monotonic() + 600,
            ))
            if sources.read_source_identity(
                payload, selected.identity.package_version, plugin="agent-worktrees",
            ) != selected.identity:
                raise ValueError("Inspection payload differs from the selected installation source")
            selection = native_selection(selected, inputs)
            payload = selected.payload
            options = {
                "wheel_inputs": inputs, "extras": tuple(selection["extras"]),
                "local_packages": tuple(selection["local_packages"]),
                "expected_source_identity": selection["identity"],
                "application": _member(install_source, selected.project["application"])
                if "application" in selected.project else None,
            }
        else:
            package_version = tomllib.loads(
                (payload / "pyproject.toml").read_text(encoding="utf-8"),
            )["project"]["version"]
            options["expected_source_identity"] = sources.manifest_fields(
                sources.read_source_identity(payload, package_version, plugin="agent-worktrees"),
            )
        temporary = custody.enter_context(tempfile.TemporaryDirectory(
            prefix="worktrees-registration-inspection-",
        ))
        root = Path(temporary).resolve(strict=True)
        candidate = custody.enter_context(engine.numbered_candidate(
            root, "control", plugin=payload, uv=uv, base_python=base_python,
            health_module="agent_worktrees.repos_registration_inspect",
            **options,
        ))
        python = engine.runtime.slot_python(root, candidate.slot.name)
        if python is None:
            raise RuntimeError("Registration inspection construction has no interpreter")
        environment = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            environment.pop(key, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        arguments = [
            "inspect", "--anchor", str(anchor), "--default-name", default_name,
            "--contract-version", "1",
        ]
        frozen = engine.FrozenInputs(
            candidate.frozen_plugin, engine._package_roots(candidate.frozen_plugin),
            candidate.payload_hash,
        )
        try:
            return engine.run_owned(
                [
                    str(python), "-I", "-B", "-X", "utf8", "-c",
                    "import sys; from agent_worktrees.repos_registration_inspect import command; "
                    "raise SystemExit(command(sys.argv[1:]))",
                    *arguments,
                ],
                cwd=Path(root.anchor), env=environment, timeout=30,
                procutil=engine._procutil(frozen), construction_fd=candidate.descriptor,
                runtime_root=root, runtime_version=candidate.slot.name,
                runtime_path=Path(engine.runtime.__file__),
                **({"deadline": inputs.deadline} if install_source is not None else {}),
            )
        except subprocess.CalledProcessError as error:
            return subprocess.CompletedProcess(error.cmd, error.returncode, error.stdout, error.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--default-name", required=True)
    parser.add_argument("--contract-version", type=int, choices=(1,), default=1)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--install-source", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = inspect_registration(
            Path(__file__).resolve().parent.parent, arguments.anchor, arguments.default_name,
            uv=arguments.uv, install_source=arguments.install_source,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Registration inspection preparation failed: {error}", file=sys.stderr)
        return 1
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
