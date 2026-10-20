"""Explicit native selection; cross-build advertisement never grants execution."""

from __future__ import annotations

from dataclasses import dataclass

from source import ProofError

ALIASES = {
    "amd64": ("amd64", "x86_64"),
    "arm64": ("arm64", "aarch64"),
}


@dataclass(frozen=True)
class NativeArchitecture:
    name: str = "amd64"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in ALIASES:
            raise ProofError("unsupported-native-architecture", "repo-config")

    @property
    def platform(self) -> str:
        return "linux/" + self.name

    def matches(self, observed: object) -> bool:
        return isinstance(observed, str) and observed in ALIASES[self.name]

    def require_engine(self, facts: object) -> None:
        if (not isinstance(facts, dict) or facts.get("os") != "linux"
                or not self.matches(facts.get("arch"))):
            raise ProofError("native-" + self.name + "-engine-required", "repo-config")

    def execution(self, prepared: dict) -> str:
        if prepared.get("platform") != "linux" or not self.matches(prepared.get("machine")):
            raise ProofError("native-image-execution-not-observed")
        return "native-" + self.name
