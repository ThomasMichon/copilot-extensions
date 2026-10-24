import base64
import hashlib
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_chat_service import ClientError
import agent_chat_service.catalog as catalog


@pytest.fixture
def installation(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    package = site / "agent_chat_service"
    package.mkdir(parents=True)
    entries = []
    for name in catalog.PACKAGE_FILES:
        data = ("# synthetic installed " + name + "\n").encode("ascii")
        (package / name).write_bytes(data)
        entry = metadata.PackagePath("agent_chat_service/" + name)
        value = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        entry.hash = metadata.FileHash("sha256=" + value)
        entry.size = len(data)
        entries.append(entry)
    distribution = SimpleNamespace(
        version=catalog.__version__.replace("-dev", ".dev"),
        files=entries,
        entry_points=[SimpleNamespace(
            group="console_scripts", name="agent-chat", value="agent_chat_service.cli:main",
        )],
        locate_file=lambda entry: site / str(entry),
    )
    monkeypatch.setattr(catalog, "__file__", str(package / "catalog.py"))
    monkeypatch.setattr(catalog.sysconfig, "get_path", lambda key: str(site))
    monkeypatch.setattr(catalog.metadata, "distribution", lambda name: distribution)
    return SimpleNamespace(package=package, distribution=distribution, entries=entries)


def test_exact_current_interpreter_not_path_or_source_selection(installation, monkeypatch):
    monkeypatch.setenv("PATH", "")
    result = catalog.installed_catalog()
    assert result["commands"][0]["argv"] == [
        catalog.sys.executable, "-I", "-m", "agent_chat_service.cli",
    ]
    assert set(result["resource_sha256s"]) == set(catalog.PACKAGE_FILES)
    assert result["distribution"] == {
        "name": "agent-chat-service", "version": installation.distribution.version,
    }
    assert not any(result[key] for key in (
        "release_selection_verified", "remote_verified",
        "native_listening_verified", "mutations_performed",
    ))


@pytest.mark.parametrize("damage", [
    "version", "no-record", "missing-entry", "duplicate-entry", "unhashed",
    "wrong-hash", "wrong-size", "changed", "missing", "directory",
    "extra-source", "oversized", "foreign-location", "outside-isolated-site",
    "missing-entrypoint", "duplicate-entrypoint", "wrong-entrypoint",
])
def test_catalog_refuses_unusable_installed_evidence(installation, monkeypatch, damage):
    distribution = installation.distribution
    path = installation.package / "transport.py"
    entry = next(item for item in installation.entries if str(item).endswith("/transport.py"))
    if damage == "version":
        distribution.version = "99"
    elif damage == "no-record":
        distribution.files = None
    elif damage == "missing-entry":
        distribution.files.remove(entry)
    elif damage == "duplicate-entry":
        distribution.files.append(entry)
    elif damage == "unhashed":
        entry.hash = None
    elif damage == "wrong-hash":
        entry.hash = metadata.FileHash("md5=invalid")
    elif damage == "wrong-size":
        entry.size += 1
    elif damage == "changed":
        path.write_bytes(b"changed")
    elif damage == "missing":
        path.unlink()
    elif damage == "directory":
        path.unlink()
        path.mkdir()
    elif damage == "extra-source":
        (installation.package / "extra.py").write_bytes(b"extra")
    elif damage == "oversized":
        path.write_bytes(b"x" * (catalog.MAX_RESOURCE_BYTES + 1))
    elif damage == "foreign-location":
        distribution.locate_file = lambda entry: installation.package.parent / "foreign"
    elif damage == "outside-isolated-site":
        monkeypatch.setattr(catalog.sysconfig, "get_path", lambda key: str(path.parent / "other"))
    elif damage == "missing-entrypoint":
        distribution.entry_points = []
    elif damage == "duplicate-entrypoint":
        distribution.entry_points *= 2
    elif damage == "wrong-entrypoint":
        distribution.entry_points[0].value = "foreign:main"
    with pytest.raises(ClientError) as caught:
        catalog.installed_catalog()
    assert caught.value.code == "invalid_installation"
    assert str(installation.package) not in caught.value.message


def test_missing_distribution_is_an_explicit_offline_error(monkeypatch):
    def unavailable(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(catalog.metadata, "distribution", unavailable)
    with pytest.raises(ClientError) as caught:
        catalog.installed_catalog()
    assert caught.value.code == "client_not_installed"


def test_catalog_refuses_linked_resource_evidence(installation, monkeypatch):
    original = Path.lstat
    target = installation.package / "transport.py"

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(catalog.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    with pytest.raises(ClientError) as caught:
        catalog.installed_catalog()
    assert caught.value.code == "invalid_installation"


@pytest.mark.parametrize("damage", ["changed", "missing", "unhashed"])
def test_catalog_includes_the_host_module_in_its_verified_closure(installation, damage):
    resource = installation.package / "hosting.py"
    entry = next(item for item in installation.entries if str(item).endswith("/hosting.py"))
    if damage == "changed":
        resource.write_bytes(b"changed host resource")
    elif damage == "missing":
        resource.unlink()
    else:
        entry.hash = None
    with pytest.raises(ClientError) as caught:
        catalog.installed_catalog()
    assert caught.value.code == "invalid_installation"


@pytest.mark.parametrize("damage", ["changed", "missing", "unhashed"])
def test_catalog_includes_the_workspace_module_in_its_verified_closure(installation, damage):
    resource = installation.package / "workspace.py"
    entry = next(item for item in installation.entries if str(item).endswith("/workspace.py"))
    if damage == "changed":
        resource.write_bytes(b"changed workspace resource")
    elif damage == "missing":
        resource.unlink()
    else:
        entry.hash = None
    with pytest.raises(ClientError) as caught:
        catalog.installed_catalog()
    assert caught.value.code == "invalid_installation"


@pytest.mark.parametrize("damage", ["changed", "missing", "unhashed"])
def test_catalog_includes_collaboration_in_its_verified_closure(installation, damage):
    resource = installation.package / "collaboration.py"
    entry = next(item for item in installation.entries if str(item).endswith("/collaboration.py"))
    if damage == "changed":
        resource.write_bytes(b"changed collaboration resource")
    elif damage == "missing":
        resource.unlink()
    else:
        entry.hash = None
    with pytest.raises(ClientError) as caught:
        catalog.installed_catalog()
    assert caught.value.code == "invalid_installation"
