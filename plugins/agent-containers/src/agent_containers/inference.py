"""Explicit nonsecret native inference inputs for restricted ACP launches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_ENDPOINT = re.compile(
    r"https?://[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?::[0-9]{1,5})?/?"
)
_REFERENCE = re.compile(r"openshell:resolve:env:[A-Z][A-Z0-9_]{0,63}")


@dataclass(frozen=True)
class NativeInferenceConfig:
    """A broker endpoint and public reference, never an upstream credential."""

    endpoint: str
    reference: str

    @classmethod
    def from_mapping(cls, raw: object) -> NativeInferenceConfig:
        if not isinstance(raw, dict) or set(raw) != {"endpoint", "reference"}:
            raise RuntimeError(
                "native_inference must contain exactly endpoint and reference"
            )
        profile = cls(endpoint=raw["endpoint"], reference=raw["reference"])
        profile.validate()
        return profile

    def validate(self) -> None:
        if not isinstance(self.endpoint, str) or not _ENDPOINT.fullmatch(self.endpoint):
            raise RuntimeError(
                "native_inference.endpoint must be an HTTP(S) origin using "
                "a single-label internal-network broker name"
            )
        parsed = urlsplit(self.endpoint)
        try:
            port = parsed.port
        except ValueError:
            raise RuntimeError("native_inference.endpoint port is invalid") from None
        if (
            parsed.hostname == "localhost"
            or len(parsed.hostname or "") > 63
            or port == 0
        ):
            raise RuntimeError("native_inference.endpoint is not a broker origin")
        if not isinstance(self.reference, str) or not _REFERENCE.fullmatch(self.reference):
            raise RuntimeError(
                "native_inference.reference must be a nonsecret "
                "OpenShell environment reference"
            )

    def environment(self) -> dict[str, str]:
        self.validate()
        return {
            "COPILOT_API_URL": self.endpoint,
            "COPILOT_GITHUB_TOKEN": self.reference,
        }
