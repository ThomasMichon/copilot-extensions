"""Static deployment admission never executes installed import hooks."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime


@pytest.fixture(params=["Lib/site-packages", "lib/python3.14/site-packages"])
def installed(tmp_path, request):
    slot = runtime.slot(tmp_path / "runtime", "2.0")
    site = slot.joinpath(*request.param.split("/"))
    site.mkdir(parents=True)
    (slot / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    package = site / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 'frozen'\n")
    metadata = site / "sample-1.0.dist-info"
    metadata.mkdir()
    (metadata / "direct_url.json").write_text(json.dumps({
        "url": (tmp_path / "removed-build-input").as_uri(), "dir_info": {},
    }))
    return slot.parent.parent, slot, site


def damage(slot, site, case, tmp_path):
    origin = site / "sample-1.0.dist-info" / "direct_url.json"
    if case in {"editable-origin", "editable-origin-case"}:
        if case == "editable-origin-case":
            upper = origin.with_name("DIRECT_URL.JSON")
            origin.rename(upper)
            origin = upper
        origin.write_text(json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}))
    elif case == "invalid-origin":
        origin.write_text('{"dir_info": {}, "dir_info": {"editable": false}}')
    elif case == "invalid-editable-type":
        origin.write_text('{"dir_info": {"editable": 0}}')
    elif case == "egg-link":
        (site / "sample.egg-link").write_text(str(tmp_path) + "\n.")
    elif case == "editable-finder":
        (site / "__editable___sample_finder.py").write_text("raise RuntimeError('must not execute')")
    elif case == "external-path":
        (site / "sample.pth").write_text(str(tmp_path / "checkout"))
    elif case == "relative-external-path":
        (site / "sample.pth").write_text("../../../../checkout")
    elif case == "executable-hook":
        (site / "sample.pth").write_text(
            f"import pathlib; pathlib.Path({str(tmp_path / 'executed')!r}).touch()\n"
        )
    elif case == "fake-virtualenv-hook":
        (site / "_virtualenv.pth").write_text("import _virtualenv\n")
    elif case == "modified-virtualenv-hook":
        (site / "_virtualenv.py").write_text("")
        (site / "_virtualenv.pth").write_text("import _virtualenv; import editable\n")
    elif case == "system-site-packages":
        (slot / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "editable-origin", "editable-origin-case", "invalid-origin", "invalid-editable-type", "egg-link",
    "editable-finder", "external-path", "relative-external-path", "executable-hook",
    "fake-virtualenv-hook", "modified-virtualenv-hook", "system-site-packages",
])
@pytest.mark.parametrize("published", [False, True])
def test_refuse_nonimmutable_environment_without_rewriting(installed, tmp_path, case, published):
    root, slot, site = installed
    if published:
        runtime.mark_complete(root, "2.0", payload_hash="same")
    damage(slot, site, case, tmp_path)
    before = {p.relative_to(slot): p.read_bytes() for p in slot.rglob("*") if p.is_file()}
    if published:
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_HEALTH_REPAIR_REQUIRED
    with pytest.raises(RuntimeError):
        runtime.mark_complete(root, "2.0", payload_hash="same")
    assert {p.relative_to(slot): p.read_bytes() for p in slot.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "executed").exists()
    assert runtime.current_version(root) is None


def test_standard_bootstrap_hooks_and_slot_local_paths(installed):
    root, slot, site = installed
    (site / "_virtualenv.py").write_text("")
    (site / "_virtualenv.pth").write_text("import _virtualenv\n")
    package = site / "_distutils_hack"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (site / "distutils-precedence.pth").write_text(
        "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; "
        "enabled = os.environ.get(var, 'local') == 'local'; "
        "enabled and __import__('_distutils_hack').add_shim();\n"
    )
    (site / "paths.pth").write_text("# comment\n\nsample\n" + str(slot / "resources") + "\n")
    marker = runtime.mark_complete(root, "2.0", payload_hash="same")
    before = marker.read_bytes()
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE
    assert runtime.mark_complete(root, "2.0", payload_hash="same") == marker
    assert marker.read_bytes() == before


@pytest.mark.parametrize("published", [False, True])
def test_linked_installed_package_is_refused(installed, tmp_path, published):
    root, slot, site = installed
    if published:
        runtime.mark_complete(root, "2.0", payload_hash="same")
    external = tmp_path / "checkout.py"
    external.write_text("VALUE = 'mutable'\n")
    link = site / "live.py"
    try:
        link.symlink_to(external)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows file symlinks require an unavailable privilege")
        raise
    with pytest.raises(RuntimeError):
        runtime.mark_complete(root, "2.0", payload_hash="same")
    if published:
        assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_HEALTH_REPAIR_REQUIRED
    assert link.is_symlink()
    assert external.read_text() == "VALUE = 'mutable'\n"


@pytest.mark.parametrize("published", [False, True])
def test_cached_wheel_hardlinks_survive_cache_retirement(installed, tmp_path, published):
    root, slot, site = installed
    if published:
        runtime.mark_complete(root, "2.0", payload_hash="same")
    cache = tmp_path / "wheel-cache"
    cache.mkdir()
    source = cache / "package.py"
    source.write_text("VALUE = 'immutable wheel content'\n")
    os.link(source, site / "live.py")
    runtime.mark_complete(root, "2.0", payload_hash="same")
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE
    assert (site / "live.py").stat().st_nlink == 2
    source.unlink()
    assert (site / "live.py").read_text() == "VALUE = 'immutable wheel content'\n"
    assert runtime.check_admission(root, "2.0", payload_hash="same") == runtime.ADMIT_REUSE


def test_metadata_io_failure_is_not_absence(installed, monkeypatch):
    root, _slot, site = installed
    runtime.mark_complete(root, "2.0", payload_hash="same")
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path == site / "sample-1.0.dist-info" / "direct_url.json":
            raise PermissionError("metadata denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError, match="metadata denied"):
        runtime.check_admission(root, "2.0", payload_hash="same")


@pytest.mark.parametrize("version", ["dev", "dev-" + "a" * 32])
def test_explicit_development_completion_also_refuses_editable_content(tmp_path, version):
    owner = "worktree:development"
    runtime.claim_dev(tmp_path, owner)
    slot = runtime.slot(tmp_path, version)
    site = slot / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "live.egg-link").write_text(str(tmp_path / "checkout"))
    with pytest.raises(RuntimeError, match="Editable installation"):
        runtime.mark_complete(tmp_path, version, payload_hash="same", dev_owner=owner)
    assert not runtime.marker_path(tmp_path, version).exists()
    assert (site / "live.egg-link").is_file()


def test_all_vendored_adopters_share_environment_admission():
    repository = LIBRARY.parents[1]
    destinations = [
        *repository.glob("plugins/*/scripts/versioned_runtime.py"),
        repository / "worktree-manager" / "scripts" / "versioned_runtime.py",
        repository / "libs" / "update-driver" / "scripts" / "versioned_runtime.py",
    ]
    assert len(destinations) >= 15
    for index, source in enumerate(destinations):
        spec = importlib.util.spec_from_file_location(f"admission_adopter_{index}", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert callable(module.require_immutable_environment), source
        assert source.read_bytes() == (LIBRARY / "versioned_runtime.py").read_bytes()
