import base64
import hashlib
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from fleet_manager import client_diagnostics
from fleet_manager.planning import PlanError


class Entry(str):
    def __new__(cls, name, data):
        value = super().__new__(cls, name)
        value.hash = SimpleNamespace(
            mode="sha256",
            value=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(),
        )
        value.size = len(data)
        return value


@pytest.fixture
def installed(tmp_path, monkeypatch):
    package = tmp_path / "fleet_manager"
    package.mkdir()
    files = []
    for name in (
        "__init__.py", "_versioned_runtime.py",
        "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py", "versioned_runtime_publication.py",
        "_installer_process.py", "_construction_procutil.py",
    ):
        data = b"resource bytes\n"
        (package / name).write_bytes(data)
        files.append(Entry(f"fleet_manager/{name}", data))
    distribution = SimpleNamespace(
        version=client_diagnostics.__version__.replace("-dev", ".dev"), files=files,
        locate_file=lambda entry: tmp_path / str(entry),
    )
    monkeypatch.setattr(client_diagnostics, "__file__", str(package / "client_diagnostics.py"))
    def discover(**arguments):
        assert arguments == {"name": "fleet-manager", "path": [str(tmp_path)]}
        return iter([distribution])

    monkeypatch.setattr(client_diagnostics.metadata.Distribution, "discover", discover)
    return distribution, package


def test_client_evidence_does_not_claim_release_or_controller_readiness(installed):
    report = client_diagnostics.diagnose_client()
    assert report["status"] == "installed"
    assert report["evidence"] == "local-installed-resource-record"
    assert not report["controller_readiness_verified"] and not report["mutations_performed"]
    assert "release_digest" not in report
    assert set(report["runtime_resource_sha256s"]) == {
        "__init__.py", "_versioned_runtime.py",
        "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py", "versioned_runtime_publication.py",
        "_installer_process.py", "_construction_procutil.py",
    }
    assert report["runtime_resource_sha256s"]["_versioned_runtime.py"] == report["runtime_resource_sha256"]


def installed_runtime(installed, monkeypatch):
    from fleet_manager import artifact_staging

    distribution, package = installed
    library = Path(__file__).resolve().parents[2] / "libs" / "versioned-runtime"
    for name in (
        "_versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py", "versioned_runtime_publication.py",
    ):
        source = "versioned_runtime.py" if name == "_versioned_runtime.py" else name
        content = (library / source).read_bytes()
        (package / name).write_bytes(content)
        distribution.files = [
            entry for entry in distribution.files if str(entry) != f"fleet_manager/{name}"
        ] + [Entry(f"fleet_manager/{name}", content)]
    monkeypatch.setattr(artifact_staging, "__file__", str(package / "artifact_staging.py"))
    return artifact_staging, package


@pytest.mark.parametrize("member", [
    "_versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_source.py", "versioned_runtime_publication.py",
])
def test_runtime_and_companion_replacement_after_admission_is_refused(installed, monkeypatch, member):
    staging, package = installed_runtime(installed, monkeypatch)
    original = staging.diagnose_client

    def replaced():
        evidence = original()
        (package / member).write_text("raise AssertionError('unadmitted code executed')\n")
        return evidence

    monkeypatch.setattr(staging, "diagnose_client", replaced)
    with pytest.raises(PlanError, match="changed after RECORD admission"):
        with staging._primitive():
            pytest.fail("replaced executable bytes were admitted")


@pytest.mark.parametrize("member", [
    "versioned_runtime_leases.py", "versioned_runtime_construction.py", "versioned_runtime_source.py",
    "versioned_runtime_publication.py",
])
def test_companions_execute_only_retained_bytes_across_late_replacement(installed, monkeypatch, member):
    import sys

    staging, package = installed_runtime(installed, monkeypatch)
    original = staging._bytes

    def replaced(path, limit):
        content = original(path, limit)
        if path == package / member:
            path.write_text("raise AssertionError('unadmitted code executed')\n")
        return content

    monkeypatch.setattr(staging, "_bytes", replaced)
    with staging._primitive() as runtime:
        prefix = runtime.__name__
        for name in (
            "versioned_runtime_leases.py", "versioned_runtime_construction.py",
            "versioned_runtime_source.py", "versioned_runtime_publication.py",
        ):
            (package / name).write_text("raise AssertionError('late code executed')\n")
        assert runtime.ConstructionTimeout.__module__ == prefix + "_versioned_runtime_leases"
        assert "_runtime_construction_channels_close" in runtime.construction_shell_helpers("sh")
        assert runtime._runtime_component("versioned_runtime_construction").__name__ in sys.modules
        assert runtime._runtime_component("versioned_runtime_source").__name__ in sys.modules
        publication = runtime._runtime_component("versioned_runtime_publication")
        assert publication.lease_component() is runtime._leases
    assert not any(name == prefix or name.startswith(prefix + "_") for name in sys.modules)


@pytest.mark.parametrize("failure", [
    "version", "missing-inventory", "missing-entry", "duplicate-entry",
    "missing-hash", "weak-hash", "bad-size", "bad-hash", "missing-resource",
    "oversized-resource", "foreign-package", "directory-resource",
])
def test_missing_conflicting_or_tampered_installed_evidence_fails(installed, failure):
    distribution, package = installed
    entry = distribution.files[-1]
    if failure == "version":
        distribution.version = "999.0"
    elif failure == "missing-inventory":
        distribution.files = None
    elif failure == "missing-entry":
        distribution.files.pop()
    elif failure == "duplicate-entry":
        distribution.files.append(entry)
    elif failure == "missing-hash":
        entry.hash = None
    elif failure == "weak-hash":
        entry.hash.mode = "md5"
    elif failure == "bad-size":
        entry.size += 1
    elif failure == "bad-hash":
        entry.hash.value = "invalid"
    elif failure == "missing-resource":
        (package / "_versioned_runtime.py").unlink()
    elif failure == "oversized-resource":
        (package / "_versioned_runtime.py").write_bytes(b"x" * (1024 * 1024 + 1))
    elif failure == "foreign-package":
        distribution.locate_file = lambda item: package.parent / "foreign" / str(item)
    elif failure == "directory-resource":
        (package / "_versioned_runtime.py").unlink()
        (package / "_versioned_runtime.py").mkdir()
    before = {path: path.read_bytes() for path in package.iterdir() if path.is_file()}
    with pytest.raises(PlanError):
        client_diagnostics.diagnose_client()
    assert {path: path.read_bytes() for path in package.iterdir() if path.is_file()} == before


def test_missing_distribution_is_not_silently_source_checkout_success(monkeypatch):
    monkeypatch.setattr(client_diagnostics.metadata.Distribution, "discover", lambda **_: iter(()))
    with pytest.raises(PlanError, match="distribution is not installed"):
        client_diagnostics.diagnose_client()


@pytest.mark.parametrize("error", [ValueError("private metadata"), PermissionError("private metadata")])
def test_malformed_or_unreadable_inventory_reports_bounded_cause(installed, monkeypatch, error):
    distribution, _ = installed

    class BrokenInventory:
        version = distribution.version

        @property
        def files(self):
            raise error

    monkeypatch.setattr(client_diagnostics.metadata.Distribution, "discover",
                        lambda **_: iter([BrokenInventory()]))
    with pytest.raises(PlanError, match="distribution metadata could not be read") as exc:
        client_diagnostics.diagnose_client()
    assert "private metadata" not in str(exc.value)


def test_ambient_metadata_discovery_is_never_a_fallback(installed, monkeypatch):
    def ambient(*args, **kwargs):
        raise AssertionError("ambient metadata search was attempted")

    monkeypatch.setattr(client_diagnostics.metadata, "distribution", ambient)
    assert client_diagnostics.diagnose_client()["status"] == "installed"


def test_duplicate_local_distributions_are_refused(installed, monkeypatch):
    distribution, _ = installed
    monkeypatch.setattr(client_diagnostics.metadata.Distribution, "discover",
                        lambda **_: iter([distribution, distribution]))
    with pytest.raises(PlanError, match="ambiguous local metadata"):
        client_diagnostics.diagnose_client()


def test_resource_read_error_is_nonsecret(installed, monkeypatch):
    distribution, package = installed
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == package / "_versioned_runtime.py":
            raise PermissionError("private input value")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(PlanError, match="resource could not be read") as exc:
        client_diagnostics.diagnose_client()
    assert "private input value" not in str(exc.value)


@pytest.mark.parametrize("level", [0, 1, 2])
@pytest.mark.parametrize("kind", ["symlink", "reparse-point", "file"])
def test_linked_or_invalid_resource_ancestors_fail_before_reading(installed, monkeypatch, level, kind):
    _, package = installed
    ancestor = (package, package.parent, package.parent.parent)[level]
    original = Path.lstat

    def altered(path, *args, **kwargs):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else (
                    stat.S_IFREG if kind == "file" else stat.S_IFDIR
                ),
                st_file_attributes=0x400 if kind == "reparse-point" else 0,
            )
        return original(path, *args, **kwargs)

    def unread(path, *args, **kwargs):
        pytest.fail("resource bytes must not be read through an invalid ancestor")

    monkeypatch.setattr(Path, "lstat", altered)
    monkeypatch.setattr(Path, "open", unread)
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    with pytest.raises(PlanError, match="must not be linked|ancestor must be a directory"):
        client_diagnostics.diagnose_client()
