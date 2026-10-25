#!/usr/bin/env python3
"""Provision immutable, installation-owned Containers dependency profiles."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

_runtime_spec = importlib.util.spec_from_file_location(
    "containers_profile_runtime", Path(__file__).with_name("versioned_runtime.py"),
)
if _runtime_spec is None or _runtime_spec.loader is None:
    raise RuntimeError("The owning Containers runtime helper is unavailable")
runtime = importlib.util.module_from_spec(_runtime_spec)
sys.modules[_runtime_spec.name] = runtime
_runtime_spec.loader.exec_module(runtime)

_source_spec = importlib.util.spec_from_file_location(
    "containers_profile_source", Path(__file__).with_name("versioned_runtime_source.py"),
)
if _source_spec is None or _source_spec.loader is None:
    raise RuntimeError("The owning Containers source helper is unavailable")
source = importlib.util.module_from_spec(_source_spec)
sys.modules[_source_spec.name] = source
_source_spec.loader.exec_module(source)

PROFILE_SCHEMA = "copilot-extensions.agent-containers.runtime-profile"
PROFILE_FILE = "runtime-profile.json"
EXECUTION_SCHEMA = "copilot-extensions.agent-containers.provider-execution"
PROFILES = ("base", "provider-exec")
LIBRARIES = (
    "agent-procutil",
    "ssh-manager",
    "credential-relay",
    "config-migrate",
    "zdd",
    "venue-copilot",
    "session-liveness-probe",
    "plugin-resolve",
    "repository-policy",
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeError(f"Duplicate runtime-profile key: {key}")
        value[key] = item
    return value


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Profile must be an ordinary JSON file: {path}")
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    if not isinstance(value, dict):
        raise RuntimeError(f"Profile must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: dict) -> None:
    if path.exists():
        if _read_json(path) == value:
            return
    elif path.is_symlink():
        raise RuntimeError(f"Profile may not be a symbolic link: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _execution_contract() -> dict:
    return {"schema": EXECUTION_SCHEMA, "version": 1, "retainedCreation": False}


def _validate_execution_contract(value: object) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "version", "retainedCreation"}
        or value["schema"] != EXECUTION_SCHEMA
        or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["retainedCreation"]) is not bool
    ):
        raise RuntimeError("Invalid Containers provider execution declaration")
    return value["retainedCreation"]


@dataclass(frozen=True)
class ExecutionSelection:
    """Read-only slot identity, not admission, retention or native custody."""

    python: Path
    runtime_version: str
    payload_hash: str
    profile: str
    contract_version: int
    retained_creation: bool


def select_execution(root: Path) -> ExecutionSelection:
    """Read the declaration of the canonical selected slot without provisioning.

    Keep this result with its exact interpreter; invoking a mutable payload shim
    later does not execute this selection. Callers still need the canonical
    retention owner and final provider/native admission before any launch.
    """
    root = Path(os.path.abspath(root.expanduser()))
    python = runtime.resolve_python(root)
    if python is None:
        raise RuntimeError("No completed Containers execution runtime is selected")
    slot = python.parent.parent
    if slot.parent != root / "versions" or slot.name.casefold() == runtime.DEV_VERSION:
        raise RuntimeError("Containers execution declaration requires an immutable slot")
    admission = runtime.check_admission(root, slot.name, payload_hash=None)
    if admission not in (runtime.ADMIT_REUSE, runtime.ADMIT_CONTENT_CONFLICT):
        raise RuntimeError(f"Selected Containers execution runtime is not complete: {admission}")
    value = _read_json(slot / PROFILE_FILE)
    required = {"packageVersion", "profile", "payloadHash", "executionContract"}
    if "executionContract" not in value:
        raise RuntimeError("Selected Containers runtime has no provider execution declaration")
    if (
        not required.issubset(value)
        or set(value) - required - {"runtimeSource"}
        or not isinstance(value["packageVersion"], str)
        or not value["packageVersion"]
        or value["profile"] not in PROFILES
        or not isinstance(value["payloadHash"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["payloadHash"]) is None
    ):
        raise RuntimeError("Invalid selected Containers slot profile")
    version = value["packageVersion"]
    profile_version = f"{version}{'.' if '+' in version else '+'}{value['profile']}"
    if source.package_version(slot.name) != profile_version:
        raise RuntimeError("Selected Containers slot identity conflicts with its profile")
    retained = _validate_execution_contract(value["executionContract"])
    admission = runtime.check_admission(
        root, slot.name, payload_hash=value["payloadHash"],
    )
    if admission != runtime.ADMIT_REUSE:
        raise RuntimeError(f"Selected Containers execution runtime is not reusable: {admission}")
    return ExecutionSelection(
        python=python, runtime_version=slot.name, payload_hash=value["payloadHash"],
        profile=value["profile"], contract_version=1, retained_creation=retained,
    )


def selected_profile(root: Path, requested: str | None = None) -> str:
    """Read durable intent, or preserve the selected legacy runtime's capability."""
    path = root / PROFILE_FILE
    if path.exists() or path.is_symlink():
        value = _read_json(path)
        if (
            set(value) != {"schema", "version", "profile"}
            or value["schema"] != PROFILE_SCHEMA
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["profile"] not in PROFILES
        ):
            raise RuntimeError(f"Invalid Containers runtime profile: {path}")
        profile = value["profile"]
    else:
        profile = "base"
        python = runtime.resolve_python(root)
        if python is not None:
            slot = python.parent.parent
            sites = [slot / "Lib" / "site-packages"]
            sites.extend((slot / "lib").glob("python*/site-packages"))
            distributions = importlib.metadata.distributions(path=[str(p) for p in sites])
            if any(
                distribution.metadata.get("Name", "").lower() == "paramiko"
                for distribution in distributions
            ):
                profile = "provider-exec"
    if requested is not None:
        if requested not in PROFILES:
            raise RuntimeError(f"Unknown Containers runtime profile: {requested}")
        profile = requested
    return profile


def _library_roots(payload: Path, *, prepared: bool = False) -> list[Path]:
    roots = []
    for name in LIBRARIES:
        library = payload / "libs" / name
        if not prepared and not (library / "pyproject.toml").is_file():
            library = payload.parent.parent / "libs" / name
        if not (library / "pyproject.toml").is_file():
            raise RuntimeError(f"{name} source not found in the owning payload")
        roots.append(library)
    return roots


def _fingerprint(payload: Path, libraries: list[Path]) -> str:
    paths = []
    for root in [payload, *libraries]:
        paths.append(root / "pyproject.toml")
        paths.extend(root.glob("README*"))
        for directory in ("src", "scripts"):
            for path in (root / directory).rglob("*"):
                if path.is_file() and not any(
                    part == "__pycache__" or part.endswith((".egg-info", ".pyc"))
                    for part in path.relative_to(root).parts
                ):
                    paths.append(path)
    return runtime.fingerprint_source(paths)


def _run(arguments: list[str], *, cwd: Path) -> None:
    with tempfile.TemporaryFile(mode="w+b") as output:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 16384))
            tail = output.read().decode("utf-8", errors="replace").splitlines()[-40:]
            for line in tail:
                line = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1***@", line)
                line = re.sub(r"(?i)((?:token|password|secret)=)[^&\s]+", r"\1***", line)
                print(f"package-manager: {line}", file=sys.stderr)
            raise RuntimeError(f"Runtime command failed (exit {result.returncode})")


def _health(python: Path, profile: str, *, cwd: Path) -> None:
    modules = ["agent_containers", *(name.replace("-", "_") for name in LIBRARIES)]
    code = (
        "import agent_containers, credential_relay, importlib.util, sys; from pathlib import Path"
        f"; assert all(Path(importlib.util.find_spec(n).origin).resolve().is_relative_to("
        f"Path(sys.prefix).resolve()) for n in {modules!r}),"
        " 'dependency escapes immutable runtime slot'"
    )
    if profile == "provider-exec":
        code += "; import paramiko"
    else:
        code += (
            "; import importlib.util"
            "; assert importlib.util.find_spec('paramiko') is None,"
            " 'base profile unexpectedly contains Paramiko'"
        )
    _run([str(python), "-I", "-c", code], cwd=cwd)


def _package_version(payload: Path) -> str:
    match = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"',
        (payload / "pyproject.toml").read_text(encoding="utf-8"),
    )
    if match is None:
        raise RuntimeError("Cannot determine Containers package version")
    return match.group(1)


def _check_source(payload: Path, version: str, identity) -> None:
    if source.read_source_identity(payload, version, plugin="agent-containers") != identity:
        raise RuntimeError("Containers source identity changed after runtime selection")


def _check_selected_source(identity, expected_identity: str | None) -> None:
    if (
        expected_identity is not None
        and json.dumps(source.manifest_fields(identity), sort_keys=True) != expected_identity
    ):
        raise RuntimeError("Containers source identity changed after runtime selection")


def stamp_source(
    root: Path, payload: Path, expected_version: str, *, expected_identity: str | None = None,
) -> Path:
    """Preserve the exact prepared closure for deferred first-use provisioning."""
    version = _package_version(payload)
    identity = source.read_source_identity(payload, version, plugin="agent-containers")
    if identity is None or identity.runtime_version != expected_version:
        raise RuntimeError("Containers stamped source changed after runtime selection")
    _check_selected_source(identity, expected_identity)
    snapshots = root / "snapshots"
    snapshot = snapshots / expected_version
    if snapshot.exists():
        _check_source(snapshot, version, identity)
        _check_source(payload, version, identity)
        return snapshot
    snapshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".source-", dir=snapshots) as temporary:
        candidate = Path(temporary) / "payload"
        shutil.copytree(payload, candidate)
        _check_source(candidate, version, identity)
        _check_source(payload, version, identity)
        candidate.rename(snapshot)
    return snapshot


def provision(
    root: Path,
    payload: Path,
    *,
    python: str,
    uv: str | None = None,
    requested: str | None = None,
    expected_source_version: str | None = None,
    expected_source_identity: str | None = None,
) -> dict:
    """Called under the installer's installation-wide transaction lock."""
    if not uv:
        raise RuntimeError("uv is required; unrestricted pip fallback is not supported")
    root = Path(os.path.abspath(root.expanduser()))
    payload = Path(os.path.abspath(payload.expanduser()))
    version = _package_version(payload)
    identity = source.read_source_identity(payload, version, plugin="agent-containers")
    selected_version = identity.runtime_version if identity else version
    if expected_source_version is not None and expected_source_version != selected_version:
        raise RuntimeError("Containers source identity changed after runtime selection")
    _check_selected_source(identity, expected_source_identity)
    profile = selected_profile(root, requested)
    profile_version = f"{version}{'.' if '+' in version else '+'}{profile}"
    runtime_version = (
        source.runtime_version(profile_version, identity.source_commit, identity.assembly_hash)
        if identity else profile_version
    )
    libraries = _library_roots(payload, prepared=identity is not None)
    fingerprint = _fingerprint(payload, libraries)
    root.mkdir(parents=True, exist_ok=True)
    # Desired state survives a failed build; the active selector remains unchanged.
    _write_json(
        root / PROFILE_FILE, {"schema": PROFILE_SCHEMA, "version": 1, "profile": profile}
    )
    expected = {"packageVersion": version, "profile": profile, "payloadHash": fingerprint}
    if identity is not None:
        expected["runtimeSource"] = identity.document()
    with ExitStack() as retention:
        with runtime.construction_lease(root, runtime_version):
            _check_source(payload, version, identity)
            admission = runtime.check_admission(
                root, runtime_version, payload_hash=fingerprint
            )
            slot = root / "versions" / runtime_version
            venv_python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            reused = admission == runtime.ADMIT_REUSE
            if reused:
                recorded = _read_json(slot / PROFILE_FILE)
                if "executionContract" in recorded:
                    declaration = recorded.pop("executionContract")
                    _validate_execution_contract(declaration)
                    if declaration != _execution_contract():
                        raise RuntimeError("Completed Containers slot has a conflicting declaration")
                if recorded != expected:
                    raise RuntimeError("Completed Containers slot has a conflicting profile")
            elif admission == runtime.ADMIT_CONSTRUCT:
                completed = False
                try:
                    slot = runtime.slot(
                        root, runtime_version, clean_incomplete=True, link_name=".venv"
                    )
                    if os.name == "nt":
                        _run(
                            [python, "-m", "venv", "--copies", "--without-pip", str(slot)],
                            cwd=payload,
                        )
                    else:
                        try:
                            _run([uv, "venv", "--python", python, str(slot)], cwd=payload)
                        except RuntimeError:
                            print(
                                "uv venv failed; retrying the incomplete slot with python -m venv",
                                file=sys.stderr,
                            )
                            runtime.slot(
                                root, runtime_version, clean_incomplete=True, link_name=".venv"
                            )
                            _run(
                                [python, "-m", "venv", "--copies", "--without-pip", str(slot)],
                                cwd=payload,
                            )
                    package = str(payload)
                    if profile == "provider-exec":
                        package += "[provider-exec]"
                    targets = [*(str(library) for library in libraries), package]
                    _run(
                        [
                            str(venv_python), "-I",
                            str(Path(__file__).with_name("installer_dependency_policy.py")),
                            "--uv", uv, "--", "--no-sources",
                            "--python", str(venv_python), "--quiet", *targets,
                        ],
                        cwd=payload,
                    )
                    if os.name == "nt":
                        for trampoline in (slot / "Scripts").glob("agent-*.exe"):
                            trampoline.unlink()
                    _health(venv_python, profile, cwd=payload)
                    _check_source(payload, version, identity)
                    if _fingerprint(payload, libraries) != fingerprint:
                        raise RuntimeError("Containers source changed during runtime construction")
                    _write_json(
                        slot / PROFILE_FILE,
                        {**expected, "executionContract": _execution_contract()},
                    )
                    runtime.mark_complete(root, runtime_version, payload_hash=fingerprint)
                    completed = True
                finally:
                    if not completed:
                        runtime.discard_failed_candidate(
                            root, runtime_version, link_name=".venv"
                        )
            else:
                raise RuntimeError(
                    f"Cannot replace immutable Containers slot {runtime_version}: {admission}; "
                    "use a new payload version or explicit runtime repair, not --force"
                )
            retention.enter_context(
                runtime.retention_lease(root, runtime_version, payload_hash=fingerprint)
            )
        if reused:
            _health(venv_python, profile, cwd=payload)
        _check_source(payload, version, identity)
        runtime.activate(
            root, runtime_version, link_name=".venv", replace_nonlink=True, link_free=True
        )
    return {
        "profile": profile,
        "runtimeVersion": runtime_version,
        "venv": str(slot),
        "python": str(venv_python),
        "reused": reused,
        "runtimeSource": identity.document() if identity else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--python")
    parser.add_argument("--uv")
    parser.add_argument("--profile", choices=PROFILES)
    parser.add_argument("--tsv", action="store_true")
    parser.add_argument("--expected-source-version")
    parser.add_argument("--expected-source-identity")
    parser.add_argument("--stamp-source", action="store_true")
    args = parser.parse_args()
    try:
        if args.stamp_source:
            if not args.expected_source_version:
                raise RuntimeError("Source stamping requires an exact selected source version")
            print(stamp_source(
                args.root, args.payload, args.expected_source_version,
                expected_identity=args.expected_source_identity,
            ))
            return 0
        if not args.python:
            raise RuntimeError("Runtime provisioning requires a bootstrap Python interpreter")
        result = provision(
            args.root, args.payload, python=args.python, uv=args.uv, requested=args.profile,
            expected_source_version=args.expected_source_version,
            expected_source_identity=args.expected_source_identity,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[agent-containers] {exc}", file=sys.stderr)
        return 1
    if args.tsv:
        print("\t".join(str(result[key]) for key in ("profile", "runtimeVersion", "venv", "python")))
    else:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
