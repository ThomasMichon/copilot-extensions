"""Deterministic aggregate-policy compiler for agent-logger.

The compiler is deliberately independent of machine-global discovery. Callers
inject admitted repositories and discovered declarations, which keeps the trust
boundary explicit and makes the pure resolution core portable and testable.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import yaml

UNCLASSIFIED = "unclassified"
WILDCARD_WITNESS = "classified:*"


class ExecutionMode(str, Enum):
    """How the aggregate plan may affect execution."""

    OBSERVE = "observe"
    ENFORCE = "enforce"


class OwnershipDimension(str, Enum):
    """The independently resolved ownership dimensions."""

    COLLECTION = "collection"
    RENDERING = "rendering"


@dataclass(frozen=True, order=True)
class Provenance:
    """Source location for a declaration or claim."""

    path: str
    repository: str
    claim_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "path": _diagnostic_path(self.path, self.repository),
            "repository": self.repository,
        }
        if self.claim_id is not None:
            result["claim_id"] = self.claim_id
        return result


@dataclass(frozen=True)
class MachineIdentity:
    """Bounded machine identity used by exact selectors."""

    name: str
    platform: str
    role: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (("name", self.name), ("platform", self.platform)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"machine {field_name} must be a non-empty string")
        if self.role is not None and (
            not isinstance(self.role, str) or not self.role.strip()
        ):
            raise ValueError("machine role must be null or a non-empty string")

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "platform": self.platform, "role": self.role}


@dataclass(frozen=True)
class MachineSelector:
    """An exact conjunction over the bounded machine identity."""

    name: str | None = None
    platform: str | None = None
    role: str | None = None

    @property
    def specificity(self) -> int:
        return sum(value is not None for value in (self.name, self.platform, self.role))

    def matches(self, machine: MachineIdentity) -> bool:
        return (
            (self.name is None or self.name == machine.name)
            and (self.platform is None or self.platform == machine.platform)
            and (self.role is None or self.role == machine.role)
        )

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("name", self.name),
                ("platform", self.platform),
                ("role", self.role),
            )
            if value is not None
        }


@dataclass(frozen=True)
class SourceSet:
    """A decidable source population: exact identities or wildcard-minus-exclusions."""

    repositories: frozenset[str] = frozenset()
    wildcard: bool = False
    exclusions: frozenset[str] = frozenset()
    include_unclassified: bool = False

    def __post_init__(self) -> None:
        repositories = frozenset(canonical_repository_identity(item) for item in self.repositories)
        exclusions = frozenset(canonical_repository_identity(item) for item in self.exclusions)
        if self.wildcard and repositories:
            raise ValueError("a source set cannot combine wildcard and exact repositories")
        if not self.wildcard and exclusions:
            raise ValueError("source exclusions require wildcard")
        if not self.wildcard and not repositories and not self.include_unclassified:
            raise ValueError("a source set must include at least one population")
        object.__setattr__(self, "repositories", repositories)
        object.__setattr__(self, "exclusions", exclusions)

    def overlap_witness(self, other: SourceSet) -> str | None:
        if self.include_unclassified and other.include_unclassified:
            return UNCLASSIFIED

        if not self.wildcard and not other.wildcard:
            overlap = self.repositories & other.repositories
            return min(overlap) if overlap else None

        if self.wildcard and not other.wildcard:
            candidates = other.repositories - self.exclusions
            return min(candidates) if candidates else None

        if other.wildcard and not self.wildcard:
            candidates = self.repositories - other.exclusions
            return min(candidates) if candidates else None

        return WILDCARD_WITNESS

    def as_dict(self) -> dict[str, object]:
        return {
            "wildcard": self.wildcard,
            "repositories": sorted(self.repositories),
            "exclusions": sorted(self.exclusions),
            "include_unclassified": self.include_unclassified,
        }


@dataclass(frozen=True)
class Claim:
    """One declaration-local claim over collection and/or rendering."""

    claim_id: str
    sources: SourceSet
    collection_target: str | None = None
    rendered_sink: str | None = None
    profile: str | None = None
    landing: str | None = None
    retention: str | None = None
    mutation: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise ValueError("claim_id must be non-empty")
        if self.collection_target is None and self.rendered_sink is None:
            raise ValueError(f"claim {self.claim_id!r} owns no dimension")


@dataclass(frozen=True)
class RepositoryPolicy:
    """A default policy, machine overlay, or local override."""

    policy_id: str
    claims: tuple[Claim, ...] = ()
    selector: MachineSelector | None = None
    disabled_claims: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RepositoryDeclaration:
    """Typed portable declaration from one repository."""

    schema_version: int
    repository: str
    provenance: Provenance
    default_policy: RepositoryPolicy
    machine_policies: tuple[RepositoryPolicy, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", canonical_repository_identity(self.repository))


@dataclass(frozen=True)
class ResourceBinding:
    """Secret-free canonical machine binding for a logical target or sink."""

    kind: str
    identity: str
    ready: bool = True
    detail: str | None = None
    _canonical_identity: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        kind = self.kind.strip().lower()
        identity = self.identity.strip()
        if not kind or not identity:
            raise ValueError("resource bindings require kind and identity")
        if kind not in {"filesystem", "repository", "remote"}:
            raise ValueError(f"unsupported resource binding kind: {kind}")
        if "@" in identity or "?" in identity or "#" in identity:
            raise ValueError("resource identity must not contain credentials")
        if kind == "remote":
            parsed = urlparse(identity)
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("remote resource identity must not contain credentials")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "_canonical_identity",
            _canonical_resource_identity(kind, identity),
        )

    @property
    def canonical_identity(self) -> str:
        return self._canonical_identity

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "kind": self.kind,
            "identity": self.canonical_identity,
            "ready": self.ready,
        }
        return result


@dataclass(frozen=True)
class Admission:
    """Machine-local authorization for one authoritative repository checkout."""

    repository: str
    authoritative_checkout: Path
    enabled: bool = True
    quarantine_reason: str | None = None
    collection_targets: dict[str, ResourceBinding] = field(default_factory=dict)
    rendered_sinks: dict[str, ResourceBinding] = field(default_factory=dict)
    override: RepositoryPolicy | None = None
    override_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", canonical_repository_identity(self.repository))
        if not self.authoritative_checkout.is_absolute():
            raise ValueError("authoritative checkout paths must be absolute")


@dataclass(frozen=True)
class DiscoveredDeclaration:
    """A declaration found by an injected discovery adapter."""

    checkout: Path
    checkout_repository: str
    declaration: RepositoryDeclaration

    def __post_init__(self) -> None:
        if not self.checkout.is_absolute():
            raise ValueError("discovered checkout paths must be absolute")
        object.__setattr__(
            self,
            "checkout_repository",
            canonical_repository_identity(self.checkout_repository),
        )


@dataclass(frozen=True)
class NormalizedClaim:
    """One ownership dimension after repository-local selection."""

    repository: str
    claim_id: str
    dimension: OwnershipDimension
    sources: SourceSet
    resource: ResourceBinding
    provenance: Provenance
    profile: str | None = None
    landing: str | None = None
    retention: str | None = None
    mutation: str | None = None

    @property
    def policy_key(
        self,
    ) -> tuple[str, str | None, str | None, str | None, str | None]:
        return (
            self.resource.canonical_identity,
            self.profile,
            self.landing,
            self.retention,
            self.mutation,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "claim_id": self.claim_id,
            "dimension": self.dimension.value,
            "sources": self.sources.as_dict(),
            "resource": self.resource.as_dict(),
            "profile": self.profile,
            "landing": self.landing,
            "retention": self.retention,
            "mutation": self.mutation,
            "provenance": self.provenance.as_dict(),
        }


@dataclass(frozen=True)
class Finding:
    """A deterministic validation or collision finding."""

    code: str
    message: str
    repositories: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    dimension: OwnershipDimension | None = None
    witness: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "repositories": list(self.repositories),
            "claims": list(self.claims),
            "dimension": self.dimension.value if self.dimension else None,
            "witness": self.witness,
        }


@dataclass(frozen=True)
class AggregateInputs:
    """Complete injected input set for one compile."""

    machine: MachineIdentity
    mode: ExecutionMode
    admissions: tuple[Admission, ...]
    discoveries: tuple[DiscoveredDeclaration, ...]
    discovery_failures: tuple[DiscoveryFailure, ...] = ()


@dataclass(frozen=True)
class DiscoveryFailure:
    """A declaration that was found but could not be loaded or validated."""

    repository: str
    path: str
    reason: str
    kind: str = "declaration"
    checkout: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repository",
            canonical_repository_identity(self.repository),
        )
        if self.kind not in {"admission", "declaration", "override"}:
            raise ValueError(f"unsupported discovery failure kind: {self.kind}")
        if self.checkout is not None and not self.checkout.is_absolute():
            raise ValueError("failure checkout paths must be absolute")


class AggregateInputProvider(Protocol):
    """Discovery seam implemented by machine-specific adapters."""

    def load(self) -> AggregateInputs:
        """Load admitted repositories and discovered declarations."""


@dataclass(frozen=True)
class CheckoutDescriptor:
    """Verified checkout supplied by a discovery adapter."""

    checkout: Path
    repository: str
    declaration_path: Path
    read_from_head: bool = False

    def __post_init__(self) -> None:
        if not self.checkout.is_absolute() or not self.declaration_path.is_absolute():
            raise ValueError("discovery paths must be absolute")
        object.__setattr__(
            self,
            "repository",
            canonical_repository_identity(self.repository),
        )


class FileSystemAggregateInputProvider:
    """Load the machine registry, declarations, and explicit local overrides.

    Checkout enumeration and remote verification stay outside this adapter:
    callers inject verified descriptors rather than letting the compiler scan
    arbitrary clones or infer authority from directory names.
    """

    def __init__(
        self,
        *,
        machine: MachineIdentity,
        home: Path,
        checkouts: Sequence[CheckoutDescriptor] | None = None,
    ) -> None:
        self.machine = machine
        self.home = home
        self.checkouts = tuple(checkouts) if checkouts is not None else None

    def load(self) -> AggregateInputs:
        config_path = self.home / "config.yaml"
        machine_config = _load_yaml_mapping(config_path) if config_path.is_file() else {}
        aggregate = machine_config.get("aggregate", {})
        if aggregate is None:
            aggregate = {}
        if not isinstance(aggregate, dict):
            raise ValueError("aggregate machine configuration must be a mapping")
        _reject_unknown(aggregate, {"mode", "admissions"}, "aggregate")

        try:
            mode = ExecutionMode(str(aggregate.get("mode", ExecutionMode.OBSERVE.value)))
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ValueError("aggregate.mode must be observe or enforce") from exc

        raw_admissions = aggregate.get("admissions", [])
        if not isinstance(raw_admissions, list):
            raise ValueError("aggregate.admissions must be a list")

        admissions: list[Admission] = []
        failures: list[DiscoveryFailure] = []
        for index, raw_admission in enumerate(raw_admissions):
            if not isinstance(raw_admission, dict):
                raise ValueError(f"aggregate.admissions[{index}] must be a mapping")
            admission_location = f"aggregate.admissions[{index}]"
            _reject_unknown(
                raw_admission,
                {
                    "repository",
                    "checkout",
                    "enabled",
                    "quarantine_reason",
                    "override",
                    "collection_targets",
                    "rendered_sinks",
                },
                admission_location,
            )
            repository = canonical_repository_identity(
                _required_text(raw_admission, "repository", admission_location)
            )
            checkout = _absolute_path(
                raw_admission.get("checkout"),
                f"{admission_location}.checkout",
            )
            configured_enabled = _optional_bool(
                raw_admission.get("enabled"),
                f"{admission_location}.enabled",
                default=True,
            )
            configured_quarantine = _optional_text(raw_admission.get("quarantine_reason"))
            override_path = raw_admission.get("override")
            override: RepositoryPolicy | None = None
            resolved_override: Path | None = None
            if override_path is not None:
                resolved_override = _absolute_path(
                    override_path,
                    f"{admission_location}.override",
                )
                try:
                    override = _parse_policy(
                        _load_yaml_mapping(resolved_override),
                        location=str(resolved_override),
                        default_id="machine-override",
                    )
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    failures.append(
                        DiscoveryFailure(
                            repository=repository,
                            path=str(resolved_override),
                            reason=f"invalid override: {exc}",
                            kind="override",
                        )
                    )
            try:
                admission = Admission(
                    repository=repository,
                    authoritative_checkout=checkout,
                    enabled=configured_enabled,
                    quarantine_reason=configured_quarantine,
                    collection_targets=_parse_bindings(
                        raw_admission.get("collection_targets", {}),
                        f"{admission_location}.collection_targets",
                    ),
                    rendered_sinks=_parse_bindings(
                        raw_admission.get("rendered_sinks", {}),
                        f"{admission_location}.rendered_sinks",
                    ),
                    override=override,
                    override_path=str(resolved_override) if resolved_override else None,
                )
            except ValueError as exc:
                failures.append(
                    DiscoveryFailure(
                        repository=repository,
                        path=str(config_path),
                        reason=f"invalid admission: {exc}",
                        kind="admission",
                    )
                )
                admission = Admission(
                    repository=repository,
                    authoritative_checkout=checkout,
                    enabled=configured_enabled,
                    quarantine_reason=configured_quarantine,
                    override_path=str(resolved_override) if resolved_override else None,
                )
            admissions.append(admission)

        descriptors = self.checkouts
        if descriptors is None:
            descriptors, descriptor_failures = _discover_admitted_checkouts(admissions)
            failures.extend(descriptor_failures)

        discoveries: list[DiscoveredDeclaration] = []
        for descriptor in descriptors:
            try:
                data = (
                    _load_head_declaration(descriptor)
                    if descriptor.read_from_head
                    else _load_yaml_mapping(descriptor.declaration_path)
                )
                declaration = _parse_declaration(
                    data,
                    descriptor.declaration_path,
                )
            except (OSError, ValueError, yaml.YAMLError) as exc:
                failures.append(
                    DiscoveryFailure(
                        repository=descriptor.repository,
                        path=str(descriptor.declaration_path),
                        reason=str(exc),
                        kind="declaration",
                        checkout=descriptor.checkout,
                    )
                )
                continue
            discoveries.append(
                DiscoveredDeclaration(
                    checkout=descriptor.checkout,
                    checkout_repository=descriptor.repository,
                    declaration=declaration,
                )
            )

        return AggregateInputs(
            machine=self.machine,
            mode=mode,
            admissions=tuple(admissions),
            discoveries=tuple(discoveries),
            discovery_failures=tuple(failures),
        )


@dataclass
class ResolvedPlan:
    """Versioned, secret-free result of aggregate resolution."""

    machine: MachineIdentity
    mode: ExecutionMode
    admissions: list[dict[str, object]] = field(default_factory=list)
    discoveries: list[dict[str, object]] = field(default_factory=list)
    selected_policies: list[dict[str, object]] = field(default_factory=list)
    shadowed_policies: list[dict[str, object]] = field(default_factory=list)
    rejected_repositories: list[dict[str, object]] = field(default_factory=list)
    claims: list[NormalizedClaim] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    authorized: bool = True
    passive: bool = True

    def as_dict(self) -> dict[str, object]:
        claims = sorted(
            self.claims,
            key=lambda item: (
                item.dimension.value,
                item.repository,
                item.claim_id,
                item.resource.canonical_identity,
            ),
        )
        findings = sorted(
            self.findings,
            key=lambda item: (
                item.code,
                item.dimension.value if item.dimension else "",
                item.repositories,
                item.claims,
                item.witness or "",
            ),
        )
        return {
            "schema_version": 1,
            "machine": self.machine.as_dict(),
            "mode": self.mode.value,
            "admissions": sorted(self.admissions, key=lambda item: str(item["repository"])),
            "discoveries": sorted(
                self.discoveries,
                key=lambda item: (
                    str(item["repository"]),
                    str(item["path"]),
                    str(item["status"]),
                ),
            ),
            "selected_policies": sorted(
                self.selected_policies,
                key=lambda item: (str(item["repository"]), str(item["policy_id"])),
            ),
            "shadowed_policies": sorted(
                self.shadowed_policies,
                key=lambda item: (str(item["repository"]), str(item["policy_id"])),
            ),
            "rejected_repositories": sorted(
                self.rejected_repositories,
                key=lambda item: str(item["repository"]),
            ),
            "claims": [item.as_dict() for item in claims],
            "findings": [item.as_dict() for item in findings],
            "authorized": self.authorized,
            "passive": self.passive,
        }

    def canonical_json(self) -> str:
        """Return byte-stable JSON for equivalent resolved inputs."""
        return json.dumps(
            self.as_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def canonical_repository_identity(value: str) -> str:
    """Normalize an exact source-host repository identity.

    Accepted forms are canonical ``host/owner/repo`` identities, HTTP(S) URLs,
    SSH URLs, and SCP-like Git remotes. Matching is exact after normalization;
    basenames and substrings are never identities.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("repository identity must be non-empty")
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        raise ValueError("filesystem paths are not repository identities")

    host: str
    path: str
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path
    else:
        scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", raw)
        if scp:
            host, path = scp.group(1).lower(), scp.group(2)
        else:
            parts = raw.replace("\\", "/").strip("/").split("/")
            if len(parts) != 3:
                raise ValueError(
                    "repository identity must be host/owner/repository or a Git remote"
                )
            host, path = parts[0].lower(), "/".join(parts[1:])

    path_parts = path.replace("\\", "/").strip("/").split("/")
    if len(path_parts) != 2 or not host or any(not item for item in path_parts):
        raise ValueError("repository identity must contain exactly owner and repository")
    owner, repository = path_parts
    if repository.lower().endswith(".git"):
        repository = repository[:-4]
    if not repository:
        raise ValueError("repository identity has an empty repository name")
    return f"{host}/{owner.lower()}/{repository.lower()}"


def compile_from_provider(provider: AggregateInputProvider) -> ResolvedPlan:
    """Load and compile one complete input snapshot."""
    inputs = provider.load()
    return compile_aggregate(inputs)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    return _load_yaml_text_mapping(path.read_text(encoding="utf-8"), str(path))


def _load_yaml_text_mapping(text: str, location: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{location} must contain a mapping")
    return data


def _load_head_declaration(descriptor: CheckoutDescriptor) -> dict[str, Any]:
    relative = descriptor.declaration_path.relative_to(descriptor.checkout)
    result = _run_git(
        descriptor.checkout,
        "show",
        f"HEAD:{relative.as_posix()}",
    )
    return _load_yaml_text_mapping(result.stdout, str(descriptor.declaration_path))


def _discover_admitted_checkouts(
    admissions: Sequence[Admission],
) -> tuple[tuple[CheckoutDescriptor, ...], tuple[DiscoveryFailure, ...]]:
    descriptors: list[CheckoutDescriptor] = []
    failures: list[DiscoveryFailure] = []
    for admission in admissions:
        if not admission.enabled or admission.quarantine_reason:
            continue
        declaration_path = (
            admission.authoritative_checkout
            / ".copilot-extensions"
            / "agent-logger"
            / "config.yaml"
        )
        try:
            repository = repository_identity_from_checkout(
                admission.authoritative_checkout
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            failures.append(
                DiscoveryFailure(
                    repository=admission.repository,
                    path=str(admission.authoritative_checkout),
                    reason=str(exc),
                    kind="admission",
                    checkout=admission.authoritative_checkout,
                )
            )
            continue
        descriptors.append(
            CheckoutDescriptor(
                checkout=admission.authoritative_checkout,
                repository=repository,
                declaration_path=declaration_path,
                read_from_head=True,
            )
        )
    return tuple(descriptors), tuple(failures)


def repository_identity_from_checkout(checkout: Path) -> str:
    """Read and normalize the authoritative checkout's ``origin`` remote."""
    if not checkout.is_absolute():
        raise ValueError("checkout path must be absolute")
    _verify_authoritative_checkout(checkout)
    result = _run_git(checkout, "remote", "get-url", "origin")
    if not result.stdout.strip():
        raise ValueError("authoritative checkout has no readable origin remote")
    return canonical_repository_identity(result.stdout.strip())


def _verify_authoritative_checkout(checkout: Path) -> None:
    result = _run_git(
        checkout,
        "rev-parse",
        "--show-toplevel",
        "--git-dir",
        "--git-common-dir",
        "HEAD",
        "refs/remotes/origin/HEAD",
    )
    lines = [line.strip() for line in result.stdout.splitlines()]
    if len(lines) != 5:
        raise ValueError("authoritative checkout metadata is incomplete")
    top_level, git_dir, common_dir, head, remote_head = lines
    if _canonical_checkout(Path(top_level)) != _canonical_checkout(checkout):
        raise ValueError("admitted path is not the checkout root")

    resolved_git_dir = Path(git_dir)
    if not resolved_git_dir.is_absolute():
        resolved_git_dir = checkout / resolved_git_dir
    resolved_common_dir = Path(common_dir)
    if not resolved_common_dir.is_absolute():
        resolved_common_dir = checkout / resolved_common_dir
    if _canonical_checkout(resolved_git_dir) != _canonical_checkout(
        resolved_common_dir
    ):
        raise ValueError("secondary worktrees cannot be authoritative checkouts")
    if head != remote_head:
        raise ValueError("authoritative checkout is not at the remote default branch")

def _run_git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_PREFIX",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(key, None)
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=10,
    )
    if result.returncode != 0:
        raise ValueError("authoritative checkout metadata is unavailable")
    return result


def _parse_declaration(
    data: dict[str, Any],
    path: Path,
) -> RepositoryDeclaration:
    repository = canonical_repository_identity(_required_text(data, "repository", str(path)))
    _reject_unknown(
        data,
        {"schema_version", "repository", "default", "machines"},
        str(path),
    )
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int):
        raise ValueError(f"{path}: schema_version must be an integer")
    default = data.get("default", {})
    if not isinstance(default, dict):
        raise ValueError(f"{path}: default must be a mapping")
    raw_machines = data.get("machines", [])
    if not isinstance(raw_machines, list):
        raise ValueError(f"{path}: machines must be a list")
    machine_policies = tuple(
        _parse_policy(
            raw,
            location=f"{path}: machines[{index}]",
            default_id=f"machine-{index}",
            require_selector=True,
        )
        for index, raw in enumerate(raw_machines)
    )
    return RepositoryDeclaration(
        schema_version=schema_version,
        repository=repository,
        provenance=Provenance(path=str(path), repository=repository),
        default_policy=_parse_policy(
            default,
            location=f"{path}: default",
            default_id="default",
        ),
        machine_policies=machine_policies,
    )


def _parse_policy(
    data: dict[str, Any],
    *,
    location: str,
    default_id: str,
    require_selector: bool = False,
) -> RepositoryPolicy:
    if not isinstance(data, dict):
        raise ValueError(f"{location} must be a mapping")
    _reject_unknown(
        data,
        {"id", "selector", "claims", "disabled_claims"},
        location,
    )
    policy_id = _optional_text(data.get("id")) or default_id
    raw_claims = data.get("claims", [])
    if not isinstance(raw_claims, list):
        raise ValueError(f"{location}.claims must be a list")
    raw_disabled = data.get("disabled_claims", [])
    if not isinstance(raw_disabled, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_disabled
    ):
        raise ValueError(f"{location}.disabled_claims must be a list of claim IDs")

    selector: MachineSelector | None = None
    raw_selector = data.get("selector")
    if raw_selector is not None:
        if not isinstance(raw_selector, dict):
            raise ValueError(f"{location}.selector must be a mapping")
        unknown = set(raw_selector) - {"name", "platform", "role"}
        if unknown:
            raise ValueError(
                f"{location}.selector contains unsupported fields: {', '.join(sorted(unknown))}"
            )
        selector = MachineSelector(
            name=_optional_text(raw_selector.get("name")),
            platform=_optional_text(raw_selector.get("platform")),
            role=_optional_text(raw_selector.get("role")),
        )
        if selector.specificity == 0:
            raise ValueError(f"{location}.selector must constrain at least one field")
    elif require_selector:
        raise ValueError(f"{location}.selector is required")

    return RepositoryPolicy(
        policy_id=policy_id,
        claims=tuple(
            _parse_claim(raw, f"{location}.claims[{index}]")
            for index, raw in enumerate(raw_claims)
        ),
        selector=selector,
        disabled_claims=frozenset(item.strip() for item in raw_disabled),
    )


def _parse_claim(data: Any, location: str) -> Claim:
    if not isinstance(data, dict):
        raise ValueError(f"{location} must be a mapping")
    _reject_unknown(
        data,
        {
            "id",
            "sources",
            "collection_target",
            "rendered_sink",
            "profile",
            "landing",
            "retention",
            "mutation",
        },
        location,
    )
    sources = data.get("sources")
    if not isinstance(sources, dict):
        raise ValueError(f"{location}.sources must be a mapping")
    _reject_unknown(
        sources,
        {"repositories", "wildcard", "exclude", "unclassified"},
        f"{location}.sources",
    )
    raw_repositories = sources.get("repositories", [])
    raw_exclusions = sources.get("exclude", [])
    if not isinstance(raw_repositories, list) or not all(
        isinstance(item, str) for item in raw_repositories
    ):
        raise ValueError(f"{location}.sources.repositories must be a string list")
    if not isinstance(raw_exclusions, list) or not all(
        isinstance(item, str) for item in raw_exclusions
    ):
        raise ValueError(f"{location}.sources.exclude must be a string list")
    return Claim(
        claim_id=_required_text(data, "id", location),
        sources=SourceSet(
            repositories=frozenset(raw_repositories),
            wildcard=_optional_bool(
                sources.get("wildcard"),
                f"{location}.sources.wildcard",
                default=False,
            ),
            exclusions=frozenset(raw_exclusions),
            include_unclassified=_optional_bool(
                sources.get("unclassified"),
                f"{location}.sources.unclassified",
                default=False,
            ),
        ),
        collection_target=_optional_text(data.get("collection_target")),
        rendered_sink=_optional_text(data.get("rendered_sink")),
        profile=_optional_text(data.get("profile")),
        landing=_optional_text(data.get("landing")),
        retention=_optional_text(data.get("retention")),
        mutation=_optional_text(data.get("mutation")),
    )


def _parse_bindings(data: Any, location: str) -> dict[str, ResourceBinding]:
    if not isinstance(data, dict):
        raise ValueError(f"{location} must be a mapping")
    result: dict[str, ResourceBinding] = {}
    for name, raw in data.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
            raise ValueError(f"{location} entries must be named mappings")
        _reject_unknown(
            raw,
            {"kind", "identity", "ready", "detail"},
            f"{location}.{name}",
        )
        result[name] = ResourceBinding(
            kind=_required_text(raw, "kind", f"{location}.{name}"),
            identity=_required_text(raw, "identity", f"{location}.{name}"),
            ready=_optional_bool(
                raw.get("ready"),
                f"{location}.{name}.ready",
                default=True,
            ),
            detail=_optional_text(raw.get("detail")),
        )
    return result


def _required_text(data: dict[str, Any], key: str, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional text values must be non-empty strings or null")
    return value.strip()


def _optional_bool(value: Any, location: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{location} must be true or false")
    return value


def _reject_unknown(
    data: dict[str, Any],
    allowed: set[str],
    location: str,
) -> None:
    unknown = sorted(
        repr(key) if not isinstance(key, str) else key
        for key in data
        if not isinstance(key, str) or key not in allowed
    )
    if unknown:
        raise ValueError(f"{location} contains unsupported fields: {', '.join(unknown)}")


def _absolute_path(value: Any, location: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{location} must be absolute")
    return path


def compile_aggregate(inputs: AggregateInputs) -> ResolvedPlan:
    """Compile admitted declarations into one deterministic machine plan."""
    plan = ResolvedPlan(machine=inputs.machine, mode=inputs.mode)
    admissions: dict[str, Admission] = {}

    grouped_admissions: dict[str, list[Admission]] = {}
    for admission in inputs.admissions:
        grouped_admissions.setdefault(admission.repository, []).append(admission)
    for repository, repository_admissions in sorted(grouped_admissions.items()):
        ordered = sorted(
            repository_admissions,
            key=lambda item: _canonical_checkout(item.authoritative_checkout),
        )
        if len(ordered) > 1:
            plan.findings.append(
                Finding(
                    code="duplicate-admission",
                    message=f"repository {repository} is admitted more than once",
                    repositories=(repository,),
                )
            )
            for admission in ordered:
                plan.admissions.append(
                    {
                        "repository": admission.repository,
                        "state": "duplicate",
                        "quarantine_reason": (
                            "configured" if admission.quarantine_reason else None
                        ),
                        "override_path": (
                            _diagnostic_path(
                                admission.override_path,
                                admission.repository,
                            )
                            if admission.override_path
                            else None
                        ),
                    }
                )
            continue
        admission = ordered[0]
        admissions[admission.repository] = admission
        state = (
            "quarantined"
            if admission.quarantine_reason
            else "enabled"
            if admission.enabled
            else "disabled"
        )
        plan.admissions.append(
            {
                "repository": admission.repository,
                "state": state,
                "quarantine_reason": ("configured" if admission.quarantine_reason else None),
                "override_path": (
                    _diagnostic_path(
                        admission.override_path,
                        admission.repository,
                    )
                    if admission.override_path
                    else None
                ),
            }
        )

    failed_repositories: set[str] = set()
    for failure in sorted(
        inputs.discovery_failures,
        key=lambda item: (
            item.repository,
            item.kind,
            _diagnostic_path(item.path, item.repository),
            item.reason,
        ),
    ):
        admission = admissions.get(failure.repository)
        secondary = (
            admission is not None
            and failure.kind == "declaration"
            and failure.checkout is not None
            and _canonical_checkout(failure.checkout)
            != _canonical_checkout(admission.authoritative_checkout)
        )
        ignored = (
            admission is None
            or not admission.enabled
            or admission.quarantine_reason is not None
            or secondary
        )
        if not ignored:
            failed_repositories.add(failure.repository)
        plan.discoveries.append(
            {
                "repository": failure.repository,
                "path": _diagnostic_path(failure.path, failure.repository),
                "status": (
                    "unadmitted-invalid"
                    if admission is None
                    else "secondary-invalid"
                    if secondary
                    else "disabled-invalid"
                    if not admission.enabled
                    else "quarantined-invalid"
                    if admission.quarantine_reason
                    else f"invalid-{failure.kind}"
                ),
            }
        )
        if not ignored:
            plan.findings.append(
                Finding(
                    code=f"invalid-{failure.kind}",
                    message=(
                        f"repository {failure.repository} {failure.kind} could not be loaded"
                    ),
                    repositories=(failure.repository,),
                )
            )

    authoritative: dict[str, DiscoveredDeclaration] = {}
    duplicate_authoritative: set[str] = set()
    for discovered in sorted(
        inputs.discoveries,
        key=lambda item: (
            item.declaration.repository,
            _canonical_checkout(item.checkout),
            item.declaration.provenance.path,
        ),
    ):
        repository = discovered.declaration.repository
        admission = admissions.get(repository)
        status = "unadmitted"
        if admission is not None:
            authoritative_path = _canonical_checkout(discovered.checkout) == _canonical_checkout(
                admission.authoritative_checkout
            )
            if not authoritative_path:
                status = "secondary-checkout"
            elif discovered.checkout_repository != repository:
                status = "identity-mismatch"
                plan.findings.append(
                    Finding(
                        code="checkout-identity-mismatch",
                        message=(
                            f"checkout identity {discovered.checkout_repository} does not "
                            f"match declaration identity {repository}"
                        ),
                        repositories=tuple(sorted((repository, discovered.checkout_repository))),
                    )
                )
            else:
                status = "authoritative"
                if repository in authoritative:
                    status = "duplicate-authoritative"
                    if repository not in duplicate_authoritative:
                        plan.findings.append(
                            Finding(
                                code="duplicate-authoritative-declaration",
                                message=(
                                    f"repository {repository} has more than one declaration "
                                    "for its authoritative checkout"
                                ),
                                repositories=(repository,),
                            )
                        )
                    duplicate_authoritative.add(repository)
                    authoritative.pop(repository, None)
                else:
                    if repository not in duplicate_authoritative:
                        authoritative[repository] = discovered
        plan.discoveries.append(
            {
                "repository": repository,
                "path": _diagnostic_path(
                    discovered.declaration.provenance.path,
                    repository,
                ),
                "status": status,
            }
        )

    for repository, admission in sorted(admissions.items()):
        if not admission.enabled or admission.quarantine_reason:
            continue
        if repository in failed_repositories:
            plan.rejected_repositories.append(
                {
                    "repository": repository,
                    "reason": "declaration or override loading failed",
                }
            )
            continue
        if repository in duplicate_authoritative:
            plan.rejected_repositories.append(
                {
                    "repository": repository,
                    "reason": "duplicate authoritative declarations",
                }
            )
            continue
        discovered = authoritative.get(repository)
        if discovered is None:
            plan.findings.append(
                Finding(
                    code="missing-authoritative-declaration",
                    message=f"repository {repository} has no authoritative declaration",
                    repositories=(repository,),
                )
            )
            plan.rejected_repositories.append(
                {
                    "repository": repository,
                    "reason": "missing authoritative declaration",
                }
            )
            continue
        declaration = discovered.declaration
        repository_finding_count = len(plan.findings)
        if declaration.schema_version != 1:
            plan.findings.append(
                Finding(
                    code="unsupported-schema",
                    message=(
                        f"repository {repository} uses unsupported aggregate schema "
                        f"{declaration.schema_version}"
                    ),
                    repositories=(repository,),
                )
            )
            plan.rejected_repositories.append(
                {
                    "repository": repository,
                    "reason": "unsupported declaration schema",
                }
            )
            continue

        policy = _select_policy(plan, declaration, inputs.machine)
        if policy is None:
            continue
        if admission.override is not None:
            policy = _overlay_policy(policy, admission.override)
            plan.selected_policies.append(
                {
                    "repository": repository,
                    "policy_id": admission.override.policy_id,
                    "kind": "override",
                    "path": (
                        _diagnostic_path(
                            admission.override_path,
                            admission.repository,
                        )
                        if admission.override_path
                        else None
                    ),
                }
            )

        normalized = _normalize_claims(plan, admission, declaration, policy)
        if len(plan.findings) != repository_finding_count:
            plan.rejected_repositories.append(
                {
                    "repository": repository,
                    "reason": "repository-scoped validation failed",
                }
            )
            continue
        plan.claims.extend(normalized)

    plan.claims = _deduplicate_or_reject_internal_claims(plan, plan.claims)
    _detect_claim_collisions(plan)
    _detect_destination_conflicts(plan)
    for claim in plan.claims:
        if not claim.resource.ready:
            plan.findings.append(
                Finding(
                    code="resource-unready",
                    message=(
                        f"{claim.dimension.value} resource "
                        f"{claim.resource.canonical_identity} is not ready"
                    ),
                    repositories=(claim.repository,),
                    claims=(claim.claim_id,),
                    dimension=claim.dimension,
                )
            )

    plan.passive = not plan.claims
    plan.authorized = not plan.findings
    return plan


def _select_policy(
    plan: ResolvedPlan,
    declaration: RepositoryDeclaration,
    machine: MachineIdentity,
) -> RepositoryPolicy | None:
    matches = [
        policy
        for policy in declaration.machine_policies
        if policy.selector is not None and policy.selector.matches(machine)
    ]
    if not matches:
        selected = declaration.default_policy
        plan.selected_policies.append(
            {
                "repository": declaration.repository,
                "policy_id": selected.policy_id,
                "kind": "default",
            }
        )
        for policy in declaration.machine_policies:
            plan.shadowed_policies.append(
                {
                    "repository": declaration.repository,
                    "policy_id": policy.policy_id,
                    "reason": "selector-mismatch",
                }
            )
        return selected

    specificity = max(policy.selector.specificity for policy in matches if policy.selector)
    best = [
        policy
        for policy in matches
        if policy.selector is not None and policy.selector.specificity == specificity
    ]
    if len(best) > 1:
        ids = tuple(sorted(policy.policy_id for policy in best))
        plan.findings.append(
            Finding(
                code="ambiguous-machine-policy",
                message=(
                    f"repository {declaration.repository} has equally specific matching "
                    f"machine policies: {', '.join(ids)}"
                ),
                repositories=(declaration.repository,),
                claims=ids,
            )
        )
        plan.rejected_repositories.append(
            {
                "repository": declaration.repository,
                "reason": "ambiguous machine policy",
            }
        )
        return None

    selected = best[0]
    plan.selected_policies.append(
        {
            "repository": declaration.repository,
            "policy_id": selected.policy_id,
            "kind": "machine",
            "selector": selected.selector.as_dict() if selected.selector else {},
        }
    )
    for policy in declaration.machine_policies:
        if policy is not selected:
            plan.shadowed_policies.append(
                {
                    "repository": declaration.repository,
                    "policy_id": policy.policy_id,
                    "reason": "less-specific" if policy in matches else "selector-mismatch",
                }
            )
    return _overlay_policy(declaration.default_policy, selected)


def _overlay_policy(base: RepositoryPolicy, overlay: RepositoryPolicy) -> RepositoryPolicy:
    claims = {claim.claim_id: claim for claim in base.claims}
    for claim_id in overlay.disabled_claims:
        claims.pop(claim_id, None)
    claims.update({claim.claim_id: claim for claim in overlay.claims})
    return RepositoryPolicy(
        policy_id=overlay.policy_id,
        claims=tuple(claims[key] for key in sorted(claims)),
    )


def _normalize_claims(
    plan: ResolvedPlan,
    admission: Admission,
    declaration: RepositoryDeclaration,
    policy: RepositoryPolicy,
) -> list[NormalizedClaim]:
    normalized: list[NormalizedClaim] = []
    for claim in sorted(policy.claims, key=lambda item: item.claim_id):
        provenance = Provenance(
            path=declaration.provenance.path,
            repository=declaration.repository,
            claim_id=claim.claim_id,
        )
        if claim.collection_target is not None:
            target = admission.collection_targets.get(claim.collection_target)
            if target is None:
                plan.findings.append(
                    Finding(
                        code="unbound-resource",
                        message=(
                            f"collection target {claim.collection_target!r} is not bound "
                            f"for {declaration.repository}"
                        ),
                        repositories=(declaration.repository,),
                        claims=(claim.claim_id,),
                        dimension=OwnershipDimension.COLLECTION,
                    )
                )
            else:
                normalized.append(
                    NormalizedClaim(
                        repository=declaration.repository,
                        claim_id=claim.claim_id,
                        dimension=OwnershipDimension.COLLECTION,
                        sources=claim.sources,
                        resource=target,
                        provenance=provenance,
                        profile=claim.profile,
                        landing=claim.landing,
                        retention=claim.retention,
                        mutation=claim.mutation,
                    )
                )
        if claim.rendered_sink is not None:
            sink = admission.rendered_sinks.get(claim.rendered_sink)
            if sink is None:
                plan.findings.append(
                    Finding(
                        code="unbound-resource",
                        message=(
                            f"rendered sink {claim.rendered_sink!r} is not bound "
                            f"for {declaration.repository}"
                        ),
                        repositories=(declaration.repository,),
                        claims=(claim.claim_id,),
                        dimension=OwnershipDimension.RENDERING,
                    )
                )
            else:
                normalized.append(
                    NormalizedClaim(
                        repository=declaration.repository,
                        claim_id=claim.claim_id,
                        dimension=OwnershipDimension.RENDERING,
                        sources=claim.sources,
                        resource=sink,
                        provenance=provenance,
                        profile=claim.profile,
                        landing=claim.landing,
                        retention=claim.retention,
                        mutation=claim.mutation,
                    )
                )
    return normalized


def _deduplicate_or_reject_internal_claims(
    plan: ResolvedPlan, claims: list[NormalizedClaim]
) -> list[NormalizedClaim]:
    accepted: list[NormalizedClaim] = []
    rejected_repositories: set[str] = set()
    for claim in sorted(
        claims,
        key=lambda item: (
            item.dimension.value,
            item.repository,
            item.claim_id,
            item.resource.canonical_identity,
        ),
    ):
        duplicate = False
        for prior in accepted:
            if prior.repository != claim.repository or prior.dimension is not claim.dimension:
                continue
            witness = prior.sources.overlap_witness(claim.sources)
            if witness is None:
                continue
            if prior.sources == claim.sources and prior.policy_key == claim.policy_key:
                duplicate = True
                break
            plan.findings.append(
                Finding(
                    code="internal-claim-ambiguity",
                    message=(
                        f"repository {claim.repository} has overlapping "
                        f"{claim.dimension.value} claims"
                    ),
                    repositories=(claim.repository,),
                    claims=tuple(sorted((prior.claim_id, claim.claim_id))),
                    dimension=claim.dimension,
                    witness=witness,
                )
            )
            rejected_repositories.add(claim.repository)
        if not duplicate:
            accepted.append(claim)
    for repository in sorted(rejected_repositories):
        plan.rejected_repositories.append(
            {
                "repository": repository,
                "reason": "internal claim ambiguity",
            }
        )
    return [claim for claim in accepted if claim.repository not in rejected_repositories]


def _detect_claim_collisions(plan: ResolvedPlan) -> None:
    claims = sorted(
        plan.claims,
        key=lambda item: (item.dimension.value, item.repository, item.claim_id),
    )
    for index, left in enumerate(claims):
        for right in claims[index + 1 :]:
            if left.dimension is not right.dimension:
                continue
            if left.repository == right.repository:
                continue
            witness = left.sources.overlap_witness(right.sources)
            if witness is None:
                continue
            repositories = tuple(sorted((left.repository, right.repository)))
            claims_pair = tuple(sorted((left.claim_id, right.claim_id)))
            plan.findings.append(
                Finding(
                    code="cross-repository-ownership-collision",
                    message=(
                        f"{left.dimension.value} ownership overlaps between "
                        f"{repositories[0]} and {repositories[1]}"
                    ),
                    repositories=repositories,
                    claims=claims_pair,
                    dimension=left.dimension,
                    witness=witness,
                )
            )


def _detect_destination_conflicts(plan: ResolvedPlan) -> None:
    claims = sorted(
        plan.claims,
        key=lambda item: (
            item.dimension.value,
            item.resource.canonical_identity,
            item.repository,
            item.claim_id,
        ),
    )
    for index, left in enumerate(claims):
        for right in claims[index + 1 :]:
            if left.resource.canonical_identity != right.resource.canonical_identity:
                continue
            if (
                left.profile,
                left.landing,
                left.retention,
                left.mutation,
            ) == (
                right.profile,
                right.landing,
                right.retention,
                right.mutation,
            ):
                continue
            plan.findings.append(
                Finding(
                    code="destination-policy-conflict",
                    message=(
                        f"{left.dimension.value} destination "
                        f"{left.resource.canonical_identity} has incompatible policy"
                    ),
                    repositories=tuple(sorted((left.repository, right.repository))),
                    claims=tuple(sorted((left.claim_id, right.claim_id))),
                    dimension=(left.dimension if left.dimension is right.dimension else None),
                )
            )


def _canonical_checkout(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _canonical_resource_identity(kind: str, identity: str) -> str:
    if kind == "filesystem":
        if not _is_absolute_machine_path(identity):
            raise ValueError("filesystem resource identities must be absolute")
        # Machine-local bindings use conservative case folding so aliases cannot
        # evade collision checks on case-insensitive filesystems.
        normalized = posixpath.normpath(identity.replace("\\", "/")).casefold()
        digest = sha256(normalized.encode("utf-8")).hexdigest()
        return f"{kind}:sha256:{digest}"
    if kind == "repository":
        repository, separator, relative = identity.partition(":")
        canonical_repository = canonical_repository_identity(repository)
        normalized_relative = posixpath.normpath(relative.replace("\\", "/"))
        if (
            normalized_relative == ".."
            or normalized_relative.startswith("../")
            or normalized_relative.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized_relative)
        ):
            raise ValueError("repository resource roots must be repository-relative")
        normalized = (
            f"{canonical_repository}:{normalized_relative.casefold()}"
            if separator
            else canonical_repository
        )
        return f"{kind}:{normalized}"
    parsed = urlparse(identity)
    if parsed.scheme:
        if not parsed.hostname:
            raise ValueError("remote resource URL must include a host")
        host = parsed.hostname.lower()
        default_port = (parsed.scheme.lower() == "http" and parsed.port == 80) or (
            parsed.scheme.lower() == "https" and parsed.port == 443
        )
        port = f":{parsed.port}" if parsed.port and not default_port else ""
        normalized = f"{parsed.scheme.lower()}://{host}{port}{posixpath.normpath(parsed.path)}"
    else:
        normalized = identity.casefold()
    return f"{kind}:{normalized}"


def _is_absolute_machine_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:/", normalized) is not None
    )


def _diagnostic_path(path: str, repository: str | None = None) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    marker = "/.copilot-extensions/"
    marker_index = normalized.casefold().find(marker)
    if marker_index >= 0:
        relative = normalized[marker_index + 1 :]
        return f"{repository}/{relative}" if repository else relative
    if re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("/"):
        relative = posixpath.basename(normalized)
        return f"{repository}/{relative}" if repository else relative
    return normalized
