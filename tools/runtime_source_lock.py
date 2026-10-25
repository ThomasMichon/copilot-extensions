"""Bind candidate source-lock output to an independently selected source.

This repository-only adapter freezes inputs, not origin approval or feed
admission. Its candidate is not a complete inspected-wheel RuntimeProfile.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from build_toolchain_lock import _MARKER_ENV_QUERY_SCRIPT, sanitize_subprocess_env
from runtime_profile import ProfileError, _target
from runtime_profile_policy import InstallationProfilePlan

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "libs" / "installer-engine"))
sys.path.insert(0, str(_ROOT / "libs" / "versioned-runtime"))
import installer_dependency_policy as dependencies
from installer_engine import _package_roots, _source_files, runtime
from installer_wheel_inputs import _check_deadline
from versioned_runtime_source import SourceIdentity, read_source_identity


@dataclass(frozen=True)
class SourceLockCandidate:
    canonical: bytes
    requirements: bytes
    payload: Path

    @property
    def lock_id(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical).hexdigest()

    def record(self) -> dict:
        return json.loads(self.canonical)

    def verify(self, *, deadline: float) -> None:
        """Recheck the selected retained inputs before exposing constructed health."""
        _check_deadline(deadline)
        record = self.record()
        if "sha256:" + hashlib.sha256(self.requirements).hexdigest() != record["requirements_sha256"]:
            raise ProfileError("source candidate requirements changed")
        observed = runtime.fingerprint_source(_source_files(_package_roots(self.payload)))
        if "sha256:" + observed != record["retained_source_sha256"]:
            raise ProfileError("retained source differs from the selected source lock")
        _check_deadline(deadline)


def _pins(content: bytes, environment: dict, deadline: float) -> list[dict]:
    try:
        text = re.sub(r"\\\r?\n", " ", content.decode("utf-8"))
        result = []
        names = set()
        for line in text.splitlines():
            _check_deadline(deadline)
            if not line.strip():
                continue
            parts = re.split(r"\s+--hash=", line.strip())
            requirement = Requirement(parts[0].strip())
            name = canonicalize_name(requirement.name)
            versions = list(requirement.specifier)
            if (
                requirement.url or requirement.extras or name in names
                or len(versions) != 1 or versions[0].operator != "=="
                or "*" in versions[0].version
                or (requirement.marker and not requirement.marker.evaluate({**environment, "extra": ""}))
                or len(parts) < 2
                or any(re.fullmatch(r"sha256:[0-9a-f]{64}", value.strip()) is None for value in parts[1:])
            ):
                raise ProfileError("source candidate output is not an exact applicable hashed lock")
            hashes = sorted(value.strip() for value in parts[1:])
            if len(set(hashes)) != len(hashes):
                raise ProfileError("source candidate output has duplicate hashes")
            names.add(name)
            result.append({"name": name, "version": str(Version(versions[0].version)), "hashes": hashes})
        return sorted(result, key=lambda item: item["name"])
    except (UnicodeError, InvalidRequirement) as exc:
        raise ProfileError("source candidate output is not an exact hashed lock") from exc


def _source(payload: Path, expected: SourceIdentity) -> dict:
    if read_source_identity(payload, expected.package_version, plugin=expected.plugin) != expected:
        raise ProfileError("source candidate differs from the independently selected source")
    project = dependencies._toml(payload / "pyproject.toml")["project"]
    if (canonicalize_name(project["name"]) != canonicalize_name(expected.plugin)
            or project["version"] != expected.package_version):
        raise ProfileError("source candidate project differs from its selected identity")
    return project


def _retained_source(transaction, payload: Path, deadline: float) -> None:
    files = set(_source_files((payload,)))
    if payload not in transaction.sources or not files <= transaction.source_hashes.keys():
        raise ProfileError("retained source does not cover the independently verified payload")
    for path, retained_digest in transaction.source_hashes.items():
        _check_deadline(deadline)
        if hashlib.sha256(path.read_bytes()).hexdigest() != retained_digest:
            raise ProfileError("retained source differs from the independently verified payload")


@contextmanager
def retain_source_lock(
    plan: InstallationProfilePlan, *, payload: Path, expected_source: SourceIdentity,
    target: dict, component: str, uv: str, python: Path,
    max_lock_bytes: int, deadline: float, extras: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
):
    """Keep source/configuration custody through candidate consumption.

    The existing authority supplies the source selection and target facts.
    No installation, source discovery, alternate release or artifact is used.
    """
    _check_deadline(deadline)
    if type(max_lock_bytes) is not int or max_lock_bytes <= 0:
        raise ProfileError("source candidate needs a positive lock byte budget")
    if not isinstance(expected_source, SourceIdentity):
        raise ProfileError("source candidate needs an independent source selection")
    policy = plan.policy()
    if (
        plan.release_selection is not None or plan.source_trigger not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        )
        or policy.get("status") != "ready" or not policy["selection"]["allowSourceFallback"]
    ):
        raise ProfileError("installation authority has not requested permitted source mode")
    _target(target, 10_000, deadline)
    frozen_target = json.loads(json.dumps(target, allow_nan=False))
    if (not isinstance(component, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", component) is None
            or policy["selection"]["backend"] not in ("cpu", frozen_target["device"])):
        raise ProfileError("source candidate capability differs from selected policy")
    if (not isinstance(extras, tuple)
            or any(not isinstance(extra, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", extra) is None
                   for extra in extras)
            or len({canonicalize_name(extra) for extra in extras}) != len(extras)):
        raise ProfileError("source candidate extras must be an immutable unique selection")
    if not python.is_absolute() or not python.is_file():
        raise ProfileError("source candidate needs an exact target interpreter")
    environment = dependencies._captured_environment(env)
    observed = subprocess.run(
        [str(python), "-I", "-S", "-c", _MARKER_ENV_QUERY_SCRIPT],
        capture_output=True, text=True, env=sanitize_subprocess_env(environment),
        timeout=max(0, deadline - time.monotonic()),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    _check_deadline(deadline)
    if observed.returncode or json.loads(observed.stdout) != frozen_target["environment"]:
        raise ProfileError("source candidate target differs from the selected interpreter")
    payload = payload.resolve(strict=True)
    project = _source(payload, expected_source)
    extras = tuple(sorted(canonicalize_name(extra) for extra in extras))
    provided = {canonicalize_name(extra) for extra in project.get("optional-dependencies", {})}
    if not set(extras) <= provided:
        raise ProfileError("source candidate requests an undeclared extra")
    arguments = [
        str(payload / "pyproject.toml"), "--python", str(python),
        "--python-version", frozen_target["environment"]["python_full_version"],
        "--only-binary", ":all:", "--no-python-downloads",
        *[argument for extra in extras for argument in ("--extra", extra)],
    ]
    with dependencies._temporary_inputs(None) as temporary:
        output = dependencies._private_retained_root(Path(temporary)) / "source-lock.txt"
        with dependencies.retain_uv_source_compile(
            uv, [*arguments, "--output-file", str(output)],
            cwd=payload, env=environment, trusted_source_roots=(payload,),
            deadline=deadline, native_target=True,
        ) as transaction:
            _source(payload, expected_source)
            _retained_source(transaction, payload, deadline)
            retained_roots = tuple(transaction.sources.values())
            retained_hash = runtime.fingerprint_source(_source_files(retained_roots))
            _check_deadline(deadline)
            result = subprocess.run(
                list(transaction.argv), env=transaction.environment, cwd=transaction.cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=deadline - time.monotonic(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            _check_deadline(deadline)
            if result.returncode:
                raise ProfileError("retained source candidate resolution failed")
            with output.open("rb") as stream:
                content = stream.read(max_lock_bytes + 1)
            if len(content) > max_lock_bytes:
                raise ProfileError("source candidate exceeds its lock byte budget")
            _source(payload, expected_source)
            if runtime.fingerprint_source(_source_files(retained_roots)) != retained_hash:
                raise ProfileError("retained source changed during candidate resolution")
            record = {
                "schema": "copilot-extensions.source-lock-candidate", "version": 1,
                "mode": "source", "source": expected_source.document(),
                "trigger": plan.source_trigger, "policy": policy, "target": frozen_target,
                "component": component, "extras": list(extras),
                "python": str(python.resolve(strict=True)),
                "retained_source_sha256": "sha256:" + retained_hash,
                "pins": _pins(content, frozen_target["environment"], deadline),
                "requirements_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            _check_deadline(deadline)
            yield SourceLockCandidate(canonical, content, transaction.sources[payload])
            _check_deadline(deadline)
            if runtime.fingerprint_source(_source_files(retained_roots)) != retained_hash:
                raise ProfileError("retained source changed during candidate consumption")
