"""Code-registered current owner resolution; these records are never wire authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from .komodo_declaration import KomodoDeclaration, fingerprint
from .komodo_native import KomodoTooling
from .komodo_profile import DATA_NAMES, SECRET_NAMES, KomodoMaterialization, ordinary_path, render_profile
from .planning import PlanError, digest, identifier, integer


@dataclass(frozen=True)
class KomodoSelection:
    """The producer authenticates release/config/host/storage/credential custody.

    A request cannot construct this result, choose its callback or supply paths.
    Its finite freshness can renew without changing the original deployment.
    """

    declaration: KomodoDeclaration
    fleet: str
    installation: str
    generation: int
    policy_revision: str
    valid_until: int
    release_digest: str
    host_runtime: Path
    tooling: KomodoTooling
    material: KomodoMaterialization
    compatible_with: tuple[str, ...] = ()
    adoption: tuple[tuple[str, str], ...] = ()
    adopted_public_identity: str | None = None
    adopted_network: str | None = None

    def __post_init__(self) -> None:
        if (type(self.declaration) is not KomodoDeclaration or type(self.tooling) is not KomodoTooling
                or type(self.material) is not KomodoMaterialization):
            raise PlanError("Komodo requires code-resolved selected owner records")
        for value in (self.fleet, self.installation, self.policy_revision):
            identifier(value, "Komodo current owner reference")
        integer(self.generation, "Komodo selected generation", minimum=1)
        integer(self.valid_until, "Komodo selected expiry", minimum=1)
        fingerprint(self.release_digest)
        ordinary_path(self.host_runtime)
        info = self.host_runtime.stat()
        if (not self.host_runtime.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise PlanError("Komodo host runtime must already have private host-owner custody")
        if type(self.compatible_with) is not tuple or len(self.compatible_with) > 16:
            raise PlanError("Komodo requires bounded explicit data compatibility assertions")
        for value in self.compatible_with:
            fingerprint(value)
        if len(set(self.compatible_with)) != len(self.compatible_with):
            raise PlanError("Komodo data compatibility assertions cannot repeat")
        object.__setattr__(self, "compatible_with", tuple(sorted(self.compatible_with)))
        if (type(self.adoption) is not tuple or len(self.adoption) not in (0, 3)
                or any(type(item) is not tuple or len(item) != 2 for item in self.adoption)):
            raise PlanError("Komodo adoption requires exact independently owned container fingerprints")
        if self.adoption and {item[0] for item in self.adoption} != {"core", "mongo", "periphery"}:
            raise PlanError("Komodo adoption requires exactly the three selected services")
        for _, value in self.adoption:
            fingerprint(value)
        if self.adoption:
            fingerprint(self.adopted_public_identity)
            fingerprint(self.adopted_network)
        elif self.adopted_public_identity is not None:
            raise PlanError("Komodo adopted public identity requires explicit incumbent custody")
        if self.adopted_network is not None:
            fingerprint(self.adopted_network)
        object.__setattr__(self, "adoption", tuple(sorted(self.adoption)))

    @property
    def resource(self) -> str:
        return digest({"daemon": self.tooling.daemon_id, "project": self.declaration.to_dict()["project"]})

    @property
    def owner(self) -> str:
        return digest({"fleet": self.fleet, "installation": self.installation, "resource": self.resource})

    def material_evidence(self) -> dict:
        credentials = self.material.credential_bytes()
        paths = {name: getattr(self.material, name) for name in (*DATA_NAMES, *SECRET_NAMES)}
        storage = {}
        for name, path in paths.items():
            ordinary_path(path)
            info = path.stat()
            storage[name] = {"path": str(path), "device": info.st_dev, "inode": info.st_ino}
        return {
            "paths": storage,
            "credentials": digest({
                name: hashlib.sha256(value).hexdigest() for name, value in credentials.items()
            }),
        }

    def receipt_context(self) -> dict:
        return {
            "declaration": self.declaration.to_dict(), "release": self.release_digest,
            "generation": self.generation, "policy_revision": self.policy_revision,
            "compatible_with": list(self.compatible_with),
            "adoption": [list(item) for item in self.adoption],
            "adopted_public_identity": self.adopted_public_identity,
            "adopted_network": self.adopted_network,
        }

    def profile(self) -> bytes:
        return render_profile(self.declaration, self.material, owner=self.owner,
                              network_external=self.adopted_network is not None)

    @property
    def deployment_digest(self) -> str:
        profile = self.profile()
        return digest({
            "declaration": self.declaration.deployment_digest,
            "fleet": self.fleet, "installation": self.installation, "resource": self.resource,
            "release": self.release_digest, "profile": hashlib.sha256(profile).hexdigest(),
            "tooling": {"docker": str(self.tooling.docker),
                        "client_config": str(self.tooling.client_config),
                        "client_identity": list(self.tooling.client_identity)},
            "material": self.material_evidence(), "compatible_with": list(self.compatible_with),
            "adoption": [list(item) for item in self.adoption],
            "adopted_public_identity": self.adopted_public_identity,
            "adopted_network": self.adopted_network,
        })
