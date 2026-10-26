"""Read-only admission of an existing source runtime under its current owner."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
import time

_ROOT = Path(__file__).resolve().parents[1]
for _library in ("installer-engine", "versioned-runtime"):
    sys.path.insert(0, str(_ROOT / "libs" / _library))

import installer_engine as engine
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_wheel_snapshot import WheelReuseInputs, _open, _unchanged
from runtime_profile import ProfileError, RuntimeProfile, validate_profile
from runtime_source_authentication import AuthenticatedSource
from runtime_source_lock import SourceConfiguration, _source
from runtime_source_wheels import _selected_projects
from versioned_runtime_source import SourceIdentity


def _canonical(record) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)


def selected_generation(root: Path) -> str | None:
    generation = engine.runtime._read_current_marker(root) or engine.runtime.current_version(root)
    try:
        engine.runtime._validate_dev_previous_version(generation)
    except ValueError as error:
        raise ProfileError("existing source runtime selector evidence is corrupt") from error
    return generation


@dataclass(frozen=True)
class SourceReuseInputs:
    payload: Path
    identity: SourceIdentity
    profile: RuntimeProfile
    wheels: WheelReuseInputs
    local_packages: tuple[str, ...]
    _verification: Callable[[], None] = field(repr=False, compare=False)

    def verify(self) -> None:
        self._verification()


def verify_not_revoked(receipt: dict, revoked_inputs: Callable[[], frozenset[str]] | None) -> None:
    """Consume current deny-only evidence; it cannot approve an installation."""
    if revoked_inputs is None:
        return
    denied = revoked_inputs()
    if not isinstance(denied, frozenset) or any(
        not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in denied
    ):
        raise ProfileError("runtime revocation evidence must be an exact immutable digest set")
    binding = receipt["binding"]
    selected = {
        receipt["lock_id"], "sha256:" + hashlib.sha256(_canonical(binding["profile"]).encode()).hexdigest(),
        *[wheel["sha256"] for wheel in receipt["distributions"].values()],
    }
    if "build_profile" in binding:
        selected.add("sha256:" + hashlib.sha256(_canonical(binding["build_profile"]).encode()).hexdigest())
        selected.update(wheel["sha256"] for wheel in binding["build_profile"]["wheels"])
    locked = binding.get("source_lock", {})
    if locked:
        selected.add("sha256:" + hashlib.sha256(_canonical(locked).encode()).hexdigest())
    selected.update(
        value for key, value in locked.items()
        if key.endswith("_sha256") and isinstance(value, str)
    )
    selected.update(wheel["sha256"] for wheel in locked.get("files", ()))
    if isinstance(locked.get("source"), dict):
        selected.add("sha256:" + locked["source"]["assembly_hash"])
    if selected & denied:
        raise ProfileError("selected source runtime inputs have been revoked")


def _admit(receipt, configuration, authenticated, *, max_tags, deadline):
    binding = receipt["binding"]
    if set(binding) != {"profile", "source_lock", "build_toolchain", "build_profile"}:
        raise ProfileError("existing source runtime has incomplete construction evidence")
    locked = binding["source_lock"]
    profile = validate_profile(binding["profile"], max_tags=max_tags, deadline=deadline)
    build = validate_profile(binding["build_profile"], max_tags=max_tags, deadline=deadline).record()
    record = profile.record()
    if (
        not isinstance(locked, dict)
        or locked.get("schema") != "copilot-extensions.source-lock-candidate"
        or type(locked.get("version")) is not int or locked["version"] != 1
        or not isinstance(locked.get("source_authentication"), dict)
        or not isinstance(locked.get("acquisition"), dict)
    ):
        raise ProfileError("existing source runtime has invalid source-lock evidence")
    current = configuration.record()
    # A formerly admitted selection can be obsolete without being corrupt.
    if any(
        locked.get(key) != value for key, value in current.items()
        if key not in ("schema", "version")
    ) or locked["source_authentication"] != authenticated.record():
        return None
    source = authenticated.identity
    release = {"identity": "source:" + source.source_commit, "sha256": "sha256:" + source.assembly_hash}
    project = _source(configuration.payload, source)
    name = engine._package_name(configuration.payload)
    extras = "[" + ",".join(current["extras"]) + "]" if current["extras"] else ""
    if (
        any(record[key] != value for key, value in {
            "mode": "source", "artifact": None, "release": release,
            "target": current["target"], "component": current["component"],
            "backend": current["policy"]["selection"]["backend"],
            "approval_source": current["policy"]["policyId"],
            "roots": [f"{name}{extras}=={project['version']}"],
            "acquisition_policy": "sha256:" + hashlib.sha256(_canonical(locked).encode()).hexdigest(),
        }.items())
        or receipt["distributions"] != {
            wheel["name"]: {key: wheel[key] for key in ("version", "filename", "sha256")}
            for wheel in record["wheels"]
        }
        or any(build[key] != value for key, value in {
            "mode": "source", "artifact": None, "release": release, "target": current["target"],
            "component": current["component"], "backend": record["backend"],
            "roots": current["build_roots"],
            "approval_source": "configured-source:" + hashlib.sha256(authenticated.canonical).hexdigest(),
            "acquisition_policy": locked["acquisition"].get("policy_id"),
        }.items())
        or binding["build_toolchain"] != {
            "packages": {wheel["name"]: wheel["version"] for wheel in build["wheels"]},
            "admitted_profile": build,
        }
    ):
        raise ProfileError("existing source runtime differs from its current source/profile owner")
    return profile


@contextmanager
def retain_source_runtime_reuse(
    authenticated: AuthenticatedSource, configuration: SourceConfiguration, *,
    max_receipt_bytes: int, max_tags: int, deadline: float,
    revoked_inputs: Callable[[], frozenset[str]] | None = None,
):
    """Borrow a completed selected generation, never create one from a receipt."""
    _check_deadline(deadline)
    if (
        not isinstance(authenticated, AuthenticatedSource) or not isinstance(configuration, SourceConfiguration)
        or type(max_receipt_bytes) is not int or max_receipt_bytes <= 0
    ):
        raise ProfileError("source reuse needs its live authentication/settings owners and byte budget")
    authenticated.verify()
    configuration.verify()
    root = Path(configuration.record()["policy"]["owner"]["runtimeRoot"])
    generation = selected_generation(root)
    if not generation or generation == "dev":
        yield None
        return
    with ExitStack() as custody:
        try:
            slot, _descriptor = custody.enter_context(engine.runtime.retention_custody(
                root, generation, timeout=max(0, deadline - time.monotonic()),
            ))
        except FileNotFoundError as error:
            if engine.runtime.check_admission(
                root, generation, payload_hash=None,
            ) == engine.runtime.ADMIT_HEALTH_REPAIR_REQUIRED:
                raise ProfileError("existing source runtime slot evidence is corrupt") from error
            yield None
            return
        admission = engine.runtime.check_admission(root, generation, payload_hash=None)
        if admission == engine.runtime.ADMIT_HEALTH_REPAIR_REQUIRED:
            raise ProfileError("existing source runtime completion evidence is corrupt")
        if admission == engine.runtime.ADMIT_CONSTRUCT:
            yield None
            return
        proof = slot / ".runtime-input-origins.json"
        try:
            before = _ordinary(proof)
        except FileNotFoundError as error:
            raise ProfileError("completed source runtime evidence is missing its receipt") from error
        if before.st_size > max_receipt_bytes:
            raise ProfileError("existing source runtime receipt exceeds its byte budget")
        stream = custody.enter_context(_open(proof))
        opened = _unchanged(proof, before, stream)
        content = stream.read(max_receipt_bytes + 1)
        if len(content) > max_receipt_bytes:
            raise ProfileError("existing source runtime receipt exceeds its byte budget")
        try:
            evidence = json.loads(content, object_pairs_hook=engine.runtime._unique_object_pairs_hook)
            if not isinstance(evidence, dict) or set(evidence) - {"extras"} != {
                "schema", "version", "packages", "wheel_inputs", "local_packages",
            } or evidence["schema"] != "installer-engine.frozen-input-origins" or (
                type(evidence["version"]) is not int or evidence["version"] != 2
            ):
                raise ValueError
            wheels = WheelReuseInputs(generation, _canonical(evidence["wheel_inputs"]), deadline)
            receipt = wheels.receipt()
            if not isinstance(evidence["packages"], dict) or set(evidence["packages"]) != set(receipt["distributions"]):
                raise ProfileError("existing source origin evidence does not cover its complete closure")
            for name, origin in evidence["packages"].items():
                if not isinstance(origin, str) or not origin.startswith("file:") or not origin.endswith(
                    "#sha256=" + receipt["distributions"][name]["sha256"][7:]
                ):
                    raise ProfileError("existing source origin evidence differs from its wheel identity")
            installed_payload = slot / "payload"
            packages = engine._package_roots(installed_payload)
            extras = engine._selected_extras(installed_payload, tuple(evidence.get("extras", [])))
            local_packages = engine._selected_local_packages(
                installed_payload, packages, tuple(evidence["local_packages"]),
            )
            installed_hash = engine._wheel_construction_hash(
                engine._construction_hash(engine._source_files(packages), extras, local_packages), wheels,
            )
            if engine.runtime.check_admission(root, generation, payload_hash=installed_hash) != engine.runtime.ADMIT_REUSE:
                raise ProfileError("existing source evidence differs from its completed constructor identity")
            profile = _admit(receipt, configuration, authenticated, max_tags=max_tags, deadline=deadline)
        except (KeyError, TypeError, ValueError, UnicodeError) as error:
            raise ProfileError("existing source runtime evidence refused: " + str(error)) from error
        if profile is None:
            yield None
            return
        expected = authenticated.identity.runtime_version + ".profile." + receipt["lock_id"][7:23]
        if re.fullmatch(re.escape(expected) + r"(?:[.+]repair\.[0-9a-f]{32})?", generation) is None:
            raise ProfileError("existing source runtime has a foreign generation identity")
        packages = engine._package_roots(configuration.payload)
        selected = _selected_projects(
            packages, engine._package_name(configuration.payload), configuration.record()["extras"],
            configuration.record()["target"]["environment"], deadline,
        )
        local_packages = tuple(sorted(engine._package_name(package) for package in selected))
        if evidence.get("local_packages") != list(local_packages) or evidence.get("extras", []) != (
            configuration.record()["extras"]
        ):
            raise ProfileError("existing source runtime differs from its current local-package selection")

        def verify():
            _check_deadline(deadline)
            authenticated.verify()
            configuration.verify()
            wheels.verify()
            if engine.runtime.check_admission(
                root, generation, payload_hash=installed_hash,
            ) != engine.runtime.ADMIT_REUSE:
                raise ProfileError("existing source runtime completion evidence changed")
            _unchanged(proof, before, stream, opened=opened)
            verify_not_revoked(receipt, revoked_inputs)

        verify()
        yield SourceReuseInputs(configuration.payload, authenticated.identity, profile, wheels, local_packages, verify)
        verify()
