"""Immutable supplied release intent; no selector, authorization or persistence."""

from dataclasses import dataclass
import json

from .planning import PlanError, canonical, decode, digest, identifier, integer, record
from .release import ROLES, SHA256, validate_release


def _validate(value):
    canonical(value)
    record(value, "fleet-manager.release-selection",
           {"owner", "decision", "revision", "authority", "installation", "role",
            "release_digest", "sequence", "components"})
    for field in ("owner", "decision", "authority", "installation"):
        identifier(value[field], f"selection {field}")
    integer(value["revision"], "selection revision", minimum=1)
    integer(value["sequence"], "selected release sequence", minimum=1)
    if value["role"] not in ROLES:
        raise PlanError("unsupported selected role")
    if not isinstance(value["release_digest"], str) or not SHA256.fullmatch(value["release_digest"]):
        raise PlanError("selected release digest must be lowercase SHA-256")
    components = value["components"]
    if not isinstance(components, list) or not components:
        raise PlanError("selected components must be a nonempty bounded list")
    for component in components:
        identifier(component, "selected component")
    if components != sorted(set(components)):
        raise PlanError("selected components must be sorted and unique")


@dataclass(frozen=True)
class ReleaseSelection:
    """Canonical immutable decision data, not proof of its owner's authority."""

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise PlanError("selection must retain immutable canonical bytes")
        value = decode(self.encoded)
        _validate(value)
        if canonical(value) != self.encoded:
            raise PlanError("selection encoding must be canonical")

    @classmethod
    def from_dict(cls, value):
        _validate(value)
        return cls(canonical(value))

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def selection_digest(self):
        return digest(self.to_dict())

    def check_selected(self, current, manifest_bytes, *, authority, installation, role, sequence):
        """Correlate against independently trusted current intent and release inputs.

        The caller establishes current intent provenance and freshly authenticates
        the manifest/sequence. This check grants no execution or activation authority.
        """
        if not isinstance(current, ReleaseSelection) or self.encoded != current.encoded:
            raise PlanError("selection differs from current selected intent")
        identifier(authority, "expected selection authority")
        identifier(installation, "expected selection installation")
        integer(sequence, "expected release sequence", minimum=1)
        if role not in ROLES:
            raise PlanError("unsupported expected selection role")
        value = self.to_dict()
        if (value["authority"], value["installation"], value["role"]) != (
            authority, installation, role,
        ):
            raise PlanError("selection scope mismatch")
        if value["sequence"] != sequence:
            raise PlanError("selection release sequence mismatch")
        if type(manifest_bytes) is not bytes:
            raise PlanError("selected manifest must be immutable canonical bytes")
        manifest = decode(manifest_bytes)
        validate_release(manifest)
        if canonical(manifest) != manifest_bytes:
            raise PlanError("selected manifest encoding must be canonical")
        if value["release_digest"] != digest(manifest):
            raise PlanError("selection manifest content mismatch")
        if value["components"] != sorted(manifest["roles"][role]):
            raise PlanError("selection component closure mismatch")
