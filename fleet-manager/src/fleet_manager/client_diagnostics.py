"""Read-only evidence for the current installed thin client, never fleet readiness."""

import base64
import hashlib
from importlib import metadata
from pathlib import Path
import stat

from . import __version__
from .planning import PlanError


def diagnose_client():
    """Require wheel RECORD-bound resources from this exact imported package."""
    try:
        distribution = metadata.distribution("fleet-manager")
        version = distribution.version
        files = distribution.files
    except metadata.PackageNotFoundError:
        raise PlanError("fleet client distribution is not installed") from None
    except (OSError, ValueError):
        raise PlanError("fleet client distribution metadata could not be read") from None
    if version not in {__version__, __version__.replace("-dev", ".dev")}:
        raise PlanError("fleet client distribution and imported package versions differ")
    if files is None:
        raise PlanError("fleet client distribution has no installed file inventory")
    required = (
        "__init__.py", "_versioned_runtime.py",
        "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py",
    )
    resource_digests = {}
    package = Path(__file__).absolute().parent
    for name in required:
        member = f"fleet_manager/{name}"
        matches = [item for item in files if str(item) == member]
        if len(matches) != 1 or matches[0].hash is None or matches[0].hash.mode != "sha256":
            raise PlanError("fleet client resource has no unique SHA-256 RECORD entry")
        entry = matches[0]
        selected = Path(distribution.locate_file(entry)).absolute()
        if selected != package / name:
            raise PlanError("fleet client resource does not belong to the imported package")
        try:
            for path in (*reversed(selected.parents), selected):
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or (
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                ):
                    raise PlanError("fleet client resource must not be linked")
                if path != selected and not stat.S_ISDIR(info.st_mode):
                    raise PlanError("fleet client resource ancestor must be a directory")
            if not stat.S_ISREG(info.st_mode):
                raise PlanError("fleet client resource must be a regular file")
            with selected.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
        except OSError:
            raise PlanError("fleet client resource could not be read") from None
        if len(data) > 1024 * 1024:
            raise PlanError("fleet client resource exceeds the 1 MiB limit")
        value = hashlib.sha256(data).digest()
        encoded = base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
        if encoded != entry.hash.value or entry.size != len(data):
            raise PlanError("fleet client resource differs from its installed RECORD")
        resource_digests[name] = value.hex()
    return {
        "schema": "fleet-manager.client-diagnostics", "version": 1,
        "role": "client", "package_version": __version__,
        "status": "installed", "evidence": "local-installed-resource-record",
        "runtime_resource_sha256": resource_digests["_versioned_runtime.py"],
        "runtime_resource_sha256s": resource_digests,
        "controller_readiness_verified": False, "mutations_performed": False,
    }
