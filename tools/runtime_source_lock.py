"""Bind candidate source-lock output to an independently selected source.

This repository-only adapter freezes inputs, not origin approval or feed
admission. Its candidate is not a complete inspected-wheel RuntimeProfile.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from build_toolchain_lock import _MARKER_ENV_QUERY_SCRIPT, sanitize_subprocess_env
from runtime_profile import ProfileError, _tag, _target, _wheel_filename
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
    native_lock: bytes | None = None
    transaction: object | None = field(default=None, repr=False, compare=False)
    projections: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _verification: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    @property
    def lock_id(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical).hexdigest()

    def record(self) -> dict:
        return json.loads(self.canonical)

    def verify(self, *, deadline: float) -> None:
        """Recheck the selected retained inputs before exposing constructed health."""
        _check_deadline(deadline)
        if self._verification is not None:
            self._verification()
        record = self.record()
        if "sha256:" + hashlib.sha256(self.requirements).hexdigest() != record["requirements_sha256"]:
            raise ProfileError("source candidate requirements changed")
        observed = runtime.fingerprint_source(_source_files(_package_roots(self.payload)))
        if "sha256:" + observed != record["retained_source_sha256"]:
            raise ProfileError("retained source differs from the selected source lock")
        _check_deadline(deadline)


@dataclass(frozen=True)
class SourceConfiguration:
    """Held native settings and source facts; no resolution or lock approval."""

    canonical: bytes
    payload: Path
    transaction: object = field(repr=False, compare=False)
    projections: tuple[str, ...] = field(repr=False, compare=False)
    output: Path = field(repr=False, compare=False)
    _verification: Callable[[], None] = field(repr=False, compare=False)

    def record(self) -> dict:
        return json.loads(self.canonical)

    def verify(self) -> None:
        self._verification()


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


def _wheel_pins(
    content: bytes, target: dict, deadline: float, *, prefer_target_tags: bool = False,
) -> list[dict]:
    """Keep exact-file defaults; opt in to unique recorded-target tag preference."""
    _check_deadline(deadline)
    if type(prefer_target_tags) is not bool:
        raise ProfileError("native source wheel matching needs an explicit boolean")
    try:
        document = dependencies._toml_parser().loads(content.decode("utf-8"))
        if (
            set(document) - {"lock-version", "created-by", "requires-python", "packages"}
            or document.get("lock-version") != "1.0" or document.get("created-by") != "uv"
            or not isinstance(document.get("requires-python"), str)
            or not SpecifierSet(document["requires-python"]).contains(
                target["environment"]["python_full_version"], prereleases=True,
            )
            or not isinstance(document.get("packages", []), list)
        ):
            raise ProfileError("unsupported native source wheel-lock document")
        priority = {}
        for index, text in enumerate(target["tags"]):
            for tag in _tag(text, 10_000, deadline):
                priority.setdefault(tag, index)
        hash_lengths = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64,
                        "sha384": 96, "sha512": 128}
        result = []
        names = set()
        for package in document.get("packages", []):
            _check_deadline(deadline)
            if (
                not isinstance(package, dict)
                or not {"name", "version", "wheels"} <= package.keys()
                or set(package) - (
                    {"name", "version", "wheels", "marker"} if prefer_target_tags
                    else {"name", "version", "wheels"}
                )
                or not isinstance(package["name"], str) or not isinstance(package["version"], str)
                or not isinstance(package["wheels"], list) or not package["wheels"]
                or (not prefer_target_tags and len(package["wheels"]) != 1)
            ):
                raise ProfileError("native source wheel-lock needs one exact wheel per package")
            if "marker" in package and (
                not isinstance(package["marker"], str)
                or not Marker(package["marker"]).evaluate({**target["environment"], "extra": ""})
            ):
                raise ProfileError("native source wheel-lock package does not apply to the selected target")
            name = canonicalize_name(package["name"], validate=True)
            version = Version(package["version"])
            if name in names:
                raise ProfileError("unsupported native source wheel witness")
            candidates = []
            for wheel in package["wheels"]:
                _check_deadline(deadline)
                if (
                    not isinstance(wheel, dict)
                    or set(wheel) - {"url", "hashes", "size", "upload-time"}
                    or not isinstance(wheel.get("url"), str)
                    or not isinstance(wheel.get("hashes"), dict)
                    or "sha256" not in wheel["hashes"]
                    or (not prefer_target_tags and wheel["hashes"].keys() != {"sha256"})
                    or any(
                        algorithm not in hash_lengths or not isinstance(digest, str)
                        or re.fullmatch(r"[0-9a-f]{" + str(hash_lengths[algorithm]) + "}", digest) is None
                        for algorithm, digest in wheel["hashes"].items()
                    )
                ):
                    raise ProfileError("unsupported native source wheel witness")
                url = urlsplit(wheel["url"])
                filename = unquote(url.path.rsplit("/", 1)[-1])
                if (
                    url.scheme not in ("http", "https") or not url.hostname
                    or not filename.endswith(".whl") or "/" in filename or "\\" in filename
                ):
                    raise ProfileError("native source wheel witness has no exact wheel filename")
                wheel_name, wheel_version, _, tags = _wheel_filename(filename, 10_000, deadline)
                if wheel_name != name or wheel_version != version:
                    raise ProfileError("native source wheel witness differs from the selected target")
                ranks = [priority[tag] for tag in tags if tag in priority]
                if ranks:
                    candidates.append((min(ranks), filename, wheel["hashes"]["sha256"]))
            if not candidates:
                raise ProfileError("native source wheel witness differs from the selected target")
            best = min(item[0] for item in candidates)
            selected = [item for item in candidates if item[0] == best]
            if len(selected) != 1:
                raise ProfileError("native source wheel-lock has tied target file candidates")
            _, filename, digest = selected[0]
            names.add(name)
            result.append({
                "name": name, "version": str(version), "filename": filename,
                "sha256": "sha256:" + digest,
            })
        _check_deadline(deadline)
        return sorted(result, key=lambda item: item["name"])
    except (ValueError, TypeError, KeyError, InvalidMarker) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError("invalid native source wheel-lock output") from exc


def _source(payload: Path, expected: SourceIdentity) -> dict:
    if read_source_identity(payload, expected.package_version, plugin=expected.plugin) != expected:
        raise ProfileError("source candidate differs from the independently selected source")
    project = dependencies._toml(payload / "pyproject.toml")["project"]
    if ((expected.plugin != "worktree-manager"
         and canonicalize_name(project["name"]) != canonicalize_name(expected.plugin))
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


def _build_roots(payload: Path, deadline: float) -> list[str]:
    roots = {"packaging"}
    for package in _package_roots(payload):
        _check_deadline(deadline)
        build = dependencies._toml(package / "pyproject.toml").get("build-system")
        if not isinstance(build, dict) or not isinstance(build.get("requires"), list):
            raise ProfileError("source build needs explicit build-system requirements")
        for text in build["requires"]:
            if not isinstance(text, str):
                raise ProfileError("source build requirements must be registry requirements")
            requirement = Requirement(text)
            if requirement.url:
                raise ProfileError("source build requirements must be registry requirements")
            roots.add(str(requirement))
    return sorted(roots)


@contextmanager
def retain_source_configuration(
    plan: InstallationProfilePlan, *, payload: Path, expected_source: SourceIdentity,
    target: dict, component: str, uv: str, python: Path,
    deadline: float, extras: tuple[str, ...] = (),
    env: dict[str, str] | None = None, wheel_witness: bool = False,
    include_build_tools: bool = False,
    installation: dict | None = None,
):
    """Capture source/compiler settings without executing dependency resolution.

    The existing authority supplies the source selection and target facts.
    No installation, source discovery, alternate release or artifact is used.
    """
    _check_deadline(deadline)
    if type(include_build_tools) is not bool or (include_build_tools and not wheel_witness):
        raise ProfileError("build tools require an exact native source wheel lock")
    if include_build_tools and installation is None:
        raise ProfileError("source build resolution requires its actual installation owner")
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
        temporary = dependencies._private_retained_root(Path(temporary))
        build_roots = _build_roots(payload, deadline) if include_build_tools else None
        if build_roots is not None:
            registry = temporary / "build-requirements.in"
            registry.write_text("\n".join(build_roots) + "\n", encoding="utf-8")
            arguments.append(str(registry))
        output = temporary / (
            "pylock.toml" if wheel_witness else "source-lock.txt"
        )
        projection = []
        with dependencies.retain_uv_source_compile(
            uv, [*arguments, "--output-file", str(output),
                 *(["--format", "pylock.toml"] if wheel_witness else [])],
            cwd=payload, env=environment, trusted_source_roots=(payload,),
            deadline=deadline, native_target=True, _projection=projection,
        ) as transaction:
            _source(payload, expected_source)
            _retained_source(transaction, payload, deadline)
            retained_roots = tuple(transaction.sources.values())
            retained_hash = runtime.fingerprint_source(_source_files(retained_roots))
            configuration_policy = None
            if installation is not None:
                from runtime_feed_configuration import validate_source_resolution_inputs

                configuration_policy = validate_source_resolution_inputs(
                    plan, installation=installation, transaction=transaction,
                    projections=projection, deadline=deadline,
                )
            record = {
                "schema": "copilot-extensions.source-configuration", "version": 1,
                "mode": "source", "source": expected_source.document(),
                "trigger": plan.source_trigger, "policy": policy, "target": frozen_target,
                "component": component, "extras": list(extras),
                "python": str(python.resolve(strict=True)),
                "retained_source_sha256": "sha256:" + retained_hash,
            }
            if configuration_policy is not None:
                record["configuration_policy"] = configuration_policy
            if build_roots is not None:
                record["build_roots"] = build_roots
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

            def verify():
                _check_deadline(deadline)
                _source(payload, expected_source)
                if runtime.fingerprint_source(_source_files(retained_roots)) != retained_hash:
                    raise ProfileError("retained source changed during candidate consumption")
                if installation is not None:
                    observed = validate_source_resolution_inputs(
                        plan, installation=installation, transaction=transaction,
                        projections=projection, deadline=deadline,
                    )
                    if observed != configuration_policy:
                        raise ProfileError("source configuration changed during consumption")
                _check_deadline(deadline)

            verify()
            yield SourceConfiguration(
                canonical, transaction.sources[payload], transaction, tuple(projection),
                output, verify,
            )
            verify()


@contextmanager
def _retain_source_lock(
    plan: InstallationProfilePlan, *, max_lock_bytes: int, deadline: float,
    wheel_witness: bool = False, **options,
):
    """Resolve only after the actual source/settings owner has been captured."""
    _check_deadline(deadline)
    if type(max_lock_bytes) is not int or max_lock_bytes <= 0:
        raise ProfileError("source candidate needs a positive lock byte budget")
    with retain_source_configuration(
        plan, deadline=deadline, wheel_witness=wheel_witness, **options,
    ) as configuration:
        transaction = configuration.transaction
        result = subprocess.run(
            list(transaction.argv), env=transaction.environment, cwd=transaction.cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=deadline - time.monotonic(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        _check_deadline(deadline)
        if result.returncode:
            raise ProfileError("retained source candidate resolution failed")
        with configuration.output.open("rb") as stream:
            content = stream.read(max_lock_bytes + 1)
        if len(content) > max_lock_bytes:
            raise ProfileError("source candidate exceeds its lock byte budget")
        configuration.verify()
        record = configuration.record()
        native_lock = content if wheel_witness else None
        files = _wheel_pins(content, record["target"], deadline) if wheel_witness else None
        if files is not None:
            content = "".join(
                f'{item["name"]}=={item["version"]} --hash={item["sha256"]}\n' for item in files
            ).encode("ascii")
        record.update({
            "schema": "copilot-extensions.source-lock-candidate",
            "pins": _pins(content, record["target"]["environment"], deadline),
            "requirements_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        })
        if files is not None:
            record["files"] = files
            record["native_lock_sha256"] = "sha256:" + hashlib.sha256(native_lock).hexdigest()
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        _check_deadline(deadline)
        yield SourceLockCandidate(
            canonical, content, configuration.payload, native_lock,
            transaction if wheel_witness else None,
            configuration.projections if wheel_witness else (),
        )
        _check_deadline(deadline)


def retain_source_lock(plan: InstallationProfilePlan, **options):
    """Retain the existing version/hash-set candidate without file admission."""
    return _retain_source_lock(plan, **options)


def retain_source_wheel_lock(plan: InstallationProfilePlan, **options):
    """Retain exact native wheel witnesses, not origin authentication or admission."""
    return _retain_source_lock(plan, **options, wheel_witness=True)
