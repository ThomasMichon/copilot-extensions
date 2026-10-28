"""Fixed official-image profile, never caller-supplied Compose or environment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from .komodo_declaration import KomodoDeclaration, fingerprint
from .planning import PlanError, canonical, digest
from .private_state import read_private_file

SECRET_NAMES = ("admin_username", "admin_password", "database_username",
                "database_password", "jwt_secret", "webhook_secret")
DATA_NAMES = ("mongo_data", "mongo_config", "keys", "backups", "periphery")

# The official entrypoint drops to mongodb before its _FILE reads. Load only
# these fixed private mounts as the image's root, then reuse its normal init/drop.
MONGO_COMMAND = (
    "bash", "-ec",
    "MONGO_INITDB_ROOT_USERNAME=$(</run/secrets/database_username); "
    "MONGO_INITDB_ROOT_PASSWORD=$(</run/secrets/database_password); "
    "export MONGO_INITDB_ROOT_USERNAME MONGO_INITDB_ROOT_PASSWORD; "
    "unset MONGO_INITDB_ROOT_USERNAME_FILE MONGO_INITDB_ROOT_PASSWORD_FILE; "
    "exec docker-entrypoint.sh mongod --quiet --wiredTigerCacheSizeGB 0.25",
)


def ordinary_path(path: Path) -> None:
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or any(ord(char) < 32 or char in "$" for char in str(path))):
        raise PlanError("Komodo requires an ordinary absolute owner path")
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise PlanError("Komodo owner paths cannot traverse indirection")
        if (component / ".git").exists():
            raise PlanError("Komodo runtime cannot depend on a source checkout")


@dataclass(frozen=True)
class KomodoMaterialization:
    """Current storage/credential owner result, not a wire grant or producer."""

    docker_socket: Path
    mongo_data: Path
    mongo_config: Path
    keys: Path
    backups: Path
    periphery: Path
    admin_username: Path
    admin_password: Path
    database_username: Path
    database_password: Path
    jwt_secret: Path
    webhook_secret: Path

    def __post_init__(self) -> None:
        ordinary_path(self.docker_socket)
        if not stat.S_ISSOCK(self.docker_socket.stat().st_mode):
            raise PlanError("Komodo host owner has not supplied its native Docker socket")
        paths = tuple(getattr(self, name) for name in (*DATA_NAMES, *SECRET_NAMES))
        if len(set(paths)) != len(paths):
            raise PlanError("Komodo materialization paths cannot alias")
        data = tuple(getattr(self, name) for name in DATA_NAMES)
        if any(first in second.parents or second in first.parents
               for index, first in enumerate(data) for second in data[index + 1:]):
            raise PlanError("Komodo persistent storage must be disjoint")
        for path in paths:
            ordinary_path(path)
        for path in data:
            if not path.is_dir():
                raise PlanError("Komodo storage owner has not prepared its directory")
        if any(parent == secret or parent in secret.parents
               for parent in data for secret in paths[len(DATA_NAMES):]):
            raise PlanError("Komodo credentials cannot be placed inside service-writable storage")
        self.credential_bytes()

    def credential_bytes(self) -> dict[str, bytes]:
        values = {name: read_private_file(getattr(self, name), 4096) for name in SECRET_NAMES}
        for name, value in values.items():
            if (not value or b"\0" in value or b"\n" in value or b"\r" in value
                    or (name.endswith(("password", "secret")) and len(value) < 16)):
                raise PlanError("Komodo credential owner supplied unsupported material")
            try:
                value.decode("utf-8")
            except UnicodeError:
                raise PlanError("Komodo credential owner supplied unsupported material") from None
        return values


def render_profile(declaration: KomodoDeclaration, material: KomodoMaterialization,
                   *, owner: str, network_external: bool = False) -> bytes:
    """Render only the selected fixed profile; no Docker or credential production."""
    fingerprint(owner)
    desired = declaration.to_dict()
    platform = "linux/" + desired["architecture"]
    services = {}
    for name in ("core", "mongo", "periphery"):
        limits = desired["resources"][name]
        services[name] = {
            "image": desired["images"][name]["image"], "platform": platform,
            "restart": "unless-stopped",
            "cpus": f'{limits["cpu_millis"] // 1000}.{limits["cpu_millis"] % 1000:03d}',
            "mem_limit": limits["memory_bytes"],
            "labels": {
                "komodo.skip": "true", "io.fleet-manager.owner": owner,
                "io.fleet-manager.deployment": declaration.deployment_digest,
            },
        }

    def bind(path: Path, destination: str, *, readonly: bool = False) -> dict:
        return {"type": "bind", "source": str(path), "target": destination,
                "read_only": readonly, "bind": {"create_host_path": False}}

    def secrets(*names: str) -> list[str]:
        return list(names)

    services["mongo"].update(
        command=[argument.replace("$", "$$") for argument in MONGO_COMMAND],
        environment={
            "MONGO_INITDB_ROOT_USERNAME_FILE": "/run/secrets/database_username",
            "MONGO_INITDB_ROOT_PASSWORD_FILE": "/run/secrets/database_password",
        },
        secrets=secrets("database_username", "database_password"),
        volumes=[bind(material.mongo_data, "/data/db"),
                 bind(material.mongo_config, "/data/configdb")],
    )
    services["core"].update(
        init=True, depends_on={"mongo": {"condition": "service_started"}},
        ports=[{"target": 9120, "published": str(desired["core_port"]),
                "host_ip": "127.0.0.1", "protocol": "tcp"}],
        environment={
            "KOMODO_DATABASE_ADDRESS": "mongo:27017",
            "KOMODO_DATABASE_USERNAME_FILE": "/run/secrets/database_username",
            "KOMODO_DATABASE_PASSWORD_FILE": "/run/secrets/database_password",
            "KOMODO_INIT_ADMIN_USERNAME_FILE": "/run/secrets/admin_username",
            "KOMODO_INIT_ADMIN_PASSWORD_FILE": "/run/secrets/admin_password",
            "KOMODO_JWT_SECRET_FILE": "/run/secrets/jwt_secret",
            "KOMODO_WEBHOOK_SECRET_FILE": "/run/secrets/webhook_secret",
            "KOMODO_PERIPHERY_PUBLIC_KEY": "file:/config/keys/periphery.pub",
            "KOMODO_FIRST_SERVER_NAME": desired["server_name"],
            "KOMODO_HOST": "http://127.0.0.1:" + str(desired["core_port"]),
            "KOMODO_LOCAL_AUTH": "true", "KOMODO_DISABLE_USER_REGISTRATION": "true",
            "KOMODO_ENABLE_NEW_USERS": "false", "KOMODO_DISABLE_NON_ADMIN_CREATE": "true",
            "KOMODO_GITHUB_OAUTH_ENABLED": "false", "KOMODO_GOOGLE_OAUTH_ENABLED": "false",
            "KOMODO_OIDC_ENABLED": "false", "KOMODO_TRANSPARENT_MODE": "false",
            "KOMODO_PRETTY_STARTUP_CONFIG": "false",
        },
        secrets=secrets(*SECRET_NAMES),
        volumes=[bind(material.keys, "/config/keys"), bind(material.backups, "/backups")],
    )
    services["periphery"].update(
        init=True, depends_on={"core": {"condition": "service_started"}},
        environment={
            "PERIPHERY_PRIVATE_KEY": "file:/config/keys/periphery.key",
            "PERIPHERY_CORE_ADDRESS": "ws://core:9120",
            "PERIPHERY_CONNECT_AS": desired["server_name"],
            "PERIPHERY_CORE_PUBLIC_KEYS": "file:/config/keys/core.pub",
            "PERIPHERY_ROOT_DIRECTORY": str(material.periphery),
            "PERIPHERY_DISABLE_TERMINALS": "true",
            "PERIPHERY_DISABLE_CONTAINER_TERMINALS": "true",
            "PERIPHERY_PRETTY_STARTUP_CONFIG": "false",
        },
        volumes=[bind(material.keys, "/config/keys"), bind(material.periphery, str(material.periphery)),
                 bind(material.docker_socket, "/var/run/docker.sock"),
                 bind(Path("/proc"), "/proc", readonly=True)],
    )
    for service in services.values():
        service["labels"]["io.fleet-manager.deployment"] = digest({
            key: value for key, value in service.items() if key != "labels"
        })
    return canonical({
        "name": desired["project"], "services": services,
        "secrets": {name: {"file": str(getattr(material, name))} for name in SECRET_NAMES},
        "networks": {"default": {
            "name": desired["project"] + "_default",
            **({"external": True} if network_external else {
                "driver": "bridge", "internal": False, "attachable": False,
                "labels": {"io.fleet-manager.owner": owner},
            }),
        }},
    })
