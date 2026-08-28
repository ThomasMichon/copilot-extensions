"""Typed values for installer/readiness discovery and planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Platform(str, Enum):
    """Portable platform identities used by module manifests."""

    WINDOWS = "windows"
    LINUX = "linux"
    WSL = "wsl"
    MACOS = "macos"


class Requirement(str, Enum):
    """Whether failure of a module is significant to a consumer."""

    REQUIRED = "required"
    OPTIONAL = "optional"


class Restart(str, Enum):
    """Environment boundary that must restart after installation."""

    NONE = "none"
    SHELL = "shell"
    SESSION = "session"
    MACHINE = "machine"


class ConfigurationEmpty(str, Enum):
    """Whether an empty configuration satisfies downstream dependencies."""

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"


class ReadinessState(str, Enum):
    """States emitted by a module readiness probe."""

    READY = "ready"
    CONFIGURATION_EMPTY = "configuration-empty"
    NOT_READY = "not-ready"
    FAILED = "failed"


class PlanState(str, Enum):
    """A planner classification; it does not imply execution."""

    READY = "ready"
    CONFIGURATION_EMPTY = "configuration-empty"
    PLANNED = "planned"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Finding:
    """One actionable contract or discovery failure."""

    code: str
    message: str
    source: str
    owner: str | None = None
    module_id: str | None = None
    remedy: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Return a stable JSON-friendly representation."""
        result = {
            "code": self.code,
            "message": self.message,
            "source": self.source,
        }
        for key in ("owner", "module_id", "remedy"):
            value = getattr(self, key)
            if value is not None:
                result["moduleId" if key == "module_id" else key] = value
        return result


@dataclass(frozen=True)
class MarketplaceProvenance:
    """Globally distinguishing marketplace installation identity."""

    marketplace_id: str
    source_fingerprint: str
    source_kind: str
    source_canonical: str
    source_ref: str = ""


@dataclass(frozen=True)
class PluginInstallation:
    """An enabled plugin payload selected through attributable provenance."""

    plugin_id: str
    payload_root: Path
    provenance: MarketplaceProvenance
    scopes: tuple[str, ...] = ()
    install_receipt: Path | None = None

    @property
    def owner_id(self) -> str:
        """Return the marketplace-qualified plugin installation identity."""
        return f"{self.provenance.marketplace_id}::{self.plugin_id}"


@dataclass(frozen=True)
class Invocation:
    """A bounded invocation rooted in the owning plugin payload."""

    kind: str
    target: Path
    arguments: tuple[str, ...]
    command_id: str | None = None


@dataclass(frozen=True)
class Module:
    """One validated installer/readiness module."""

    module_id: str
    owner: PluginInstallation
    platforms: tuple[Platform, ...]
    classification: Requirement
    installer: dict[Platform, Invocation]
    readiness: dict[Platform, Invocation]
    configuration_empty: ConfigurationEmpty
    dependencies: tuple[str, ...]
    restart: Restart
    source: Path

    @property
    def qualified_id(self) -> str:
        """Return the cell-qualified module identity."""
        return f"{self.owner.provenance.marketplace_id}::{self.module_id}"


@dataclass(frozen=True)
class Decline:
    """An intentional declaration that an enabled runtime has no modules."""

    owner: PluginInstallation
    reason: str
    source: Path


@dataclass(frozen=True)
class DiscoveryReport:
    """Validated modules, explicit declines, and actionable findings."""

    modules: tuple[Module, ...] = ()
    declines: tuple[Decline, ...] = ()
    findings: tuple[Finding, ...] = ()
    machine_gated_owners: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether planning may safely proceed."""
        return not self.findings

    @property
    def covered_owners(self) -> frozenset[str]:
        """Owners represented by modules or intentional declines."""
        return frozenset(
            [module.owner.owner_id for module in self.modules]
            + [decline.owner.owner_id for decline in self.declines]
        )


@dataclass(frozen=True)
class ReadinessResult:
    """Strictly parsed output from one module readiness probe."""

    module_id: str
    state: ReadinessState
    detail: str | None = None


@dataclass(frozen=True)
class PlanStep:
    """One deterministic graph classification."""

    module: Module
    state: PlanState
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """A deterministic plan for later consumer execution."""

    platform: Platform
    steps: tuple[PlanStep, ...]

    @property
    def required_failures(self) -> tuple[PlanStep, ...]:
        """Required failed or blocked nodes, without imposing exit policy."""
        return tuple(
            step
            for step in self.steps
            if step.module.classification is Requirement.REQUIRED
            and step.state in (PlanState.FAILED, PlanState.BLOCKED)
        )
