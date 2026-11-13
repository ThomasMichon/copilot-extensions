"""Disposable offline PR image inputs; no approval, acquisition or deployment."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import time

from packaging.utils import canonicalize_name

from runtime_profile import ProfileError, RuntimeProfile, validate_profile
from runtime_profile_wheels import _verified_profile_wheels
from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline
from installer_wheel_snapshot import WheelInstallInputs, WheelSnapshot, _digest


_DOCKERFILE = (
    Path(__file__).resolve().parents[1] / "services" / "pr" / "container" / "Dockerfile"
)
_REQUIRED = frozenset({
    "agent-pull-requests-service", "agent-pull-requests", "fleet-manager", "agent-worktrees",
})


@dataclass(frozen=True)
class ImageInputs:
    path: Path
    python_base: str
    lock_id: str
    profile_lock_ids: tuple[str, ...] = ()


def _inspect_profiles(selections, limits: WheelLimits, deadline: float):
    aggregate = {
        "archive": limits.max_archive_bytes,
        "expanded": limits.max_expanded_bytes,
        "seen": {},
    }
    inputs, identities, wheels, archives = [], [], {}, {}
    target = backend = None
    for profile, snapshot in selections:
        _check_deadline(deadline)
        if not isinstance(profile, RuntimeProfile) or not isinstance(snapshot, WheelSnapshot):
            raise ProfileError("PR image requires exact profiles and retained wheel custody")
        profile = validate_profile(profile.record(), max_tags=limits.max_tags, deadline=deadline)
        record = profile.record()
        environment = record["target"]["environment"]
        if (
            environment["sys_platform"] != "linux" or environment["os_name"] != "posix"
            or environment["platform_system"] != "Linux"
        ):
            raise ProfileError("PR image requires a Linux runtime profile")
        if target is not None and (record["target"] != target or record["backend"] != backend):
            raise ProfileError("PR image profiles select different targets or backends")
        target, backend = record["target"], record["backend"]
        distributions = _verified_profile_wheels(
            profile, snapshot.archives, max_archive_bytes=limits.max_archive_bytes,
            max_unpacked_bytes=limits.max_expanded_bytes,
            max_metadata_bytes=limits.max_metadata_bytes, limits=limits, deadline=deadline,
            aggregate=aggregate,
        )
        selected = WheelInstallInputs(
            snapshot, profile.canonical.decode("ascii"), tuple(distributions),
        )
        selected.verify()
        inputs.append(selected)
        identities.append(profile.lock_id)
        for wheel in record["wheels"]:
            name = canonicalize_name(wheel["name"])
            previous = wheels.get(name)
            if previous is not None:
                if (
                    previous | {"selected_extras": []}
                    != wheel | {"selected_extras": []}
                ):
                    raise ProfileError("PR image profiles contain conflicting shared distributions")
                wheel = wheel | {
                    "selected_extras": sorted(set(previous["selected_extras"]) | set(wheel["selected_extras"])),
                }
            wheels[name] = wheel
            archives.setdefault(wheel["filename"], snapshot.archives[wheel["filename"]])
    if not _REQUIRED <= wheels.keys():
        raise ProfileError("PR image closure omits a required service or native distribution")
    return inputs, tuple(identities), wheels, archives


@contextmanager
def prepare_image_inputs(
    profile: RuntimeProfile, snapshot: WheelSnapshot, *, python_base: str,
    limits: WheelLimits, deadline: float,
    additional_inputs: tuple[tuple[RuntimeProfile, WheelSnapshot], ...] = (),
):
    """Retain inspected bytes through an offline builder's context lifetime.

    The trusted caller must separately authorize the profile, first-party
    release, target/base and feed acquisition before construction. Neither a
    profile record nor this result grants that authority. Pass python_base as
    the Dockerfile's PYTHON_BASE build argument and keep this context open until
    the builder has consumed every input. This function never runs a builder.

    Additional independently admitted application profiles retain their original
    identities; composition creates no merged profile or approval record.
    """
    if (
        not isinstance(python_base, str)
        or re.fullmatch(r"[a-z0-9]+(?:[._:/-][a-z0-9]+)*@sha256:[0-9a-f]{64}", python_base) is None
    ):
        raise ProfileError("PR image requires an explicitly pinned base reference")
    if (
        type(deadline) not in (int, float) or not math.isfinite(deadline)
        or deadline <= time.monotonic()
    ):
        raise ProfileError("PR image requires a finite future deadline")
    if not isinstance(limits, WheelLimits):
        raise ProfileError("PR image requires explicit wheel limits")
    if (
        not isinstance(additional_inputs, tuple) or len(additional_inputs) > 63
        or any(not isinstance(item, tuple) or len(item) != 2 for item in additional_inputs)
    ):
        raise ProfileError("PR image requires a bounded tuple of additional profile inputs")
    inputs, identities, wheels, archives = _inspect_profiles(
        ((profile, snapshot), *additional_inputs), limits, deadline,
    )
    lock_id = identities[0] if not additional_inputs else "sha256:" + hashlib.sha256(
        json.dumps({"profiles": sorted(identities)}, sort_keys=True, separators=(",", ":")).encode("ascii"),
    ).hexdigest()
    with _temporary_inputs(None) as temporary:
        root = _private_retained_root(Path(temporary)).resolve()
        context = root / "context"
        wheelhouse = context / "wheels"
        wheelhouse.mkdir(parents=True)
        requirements = []
        for name, wheel in sorted(wheels.items()):
            _check_deadline(deadline)
            filename, digest = wheel["filename"], wheel["sha256"]
            stream = archives[filename]
            with (wheelhouse / filename).open("xb") as destination:
                copied_digest, _ = _digest(stream, limits.max_archive_bytes, deadline, destination)
            if copied_digest != digest:
                raise ProfileError("PR image wheel changed during context construction")
            extras = wheel["selected_extras"]
            suffix = "[" + ",".join(sorted(extras)) + "]" if extras else ""
            requirements.append(f"{name}{suffix}=={wheel['version']} --hash={digest}")
        (context / "requirements.lock").write_bytes(("\n".join(requirements) + "\n").encode("ascii"))
        (context / "Dockerfile").write_bytes(_DOCKERFILE.read_bytes())
        for selected in inputs:
            selected.verify()
        _check_deadline(deadline)
        yield ImageInputs(context, python_base, lock_id, identities)
        for selected in inputs:
            selected.verify()
        _check_deadline(deadline)
