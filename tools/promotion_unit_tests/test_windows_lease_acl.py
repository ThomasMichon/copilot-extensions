"""Actor-free lease ACL and first-touch privacy controls."""

import importlib.util
import itertools
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "libs/versioned-runtime/versioned_runtime_leases.py"
SPEC = importlib.util.spec_from_file_location("unit_windows_lease_acl", SOURCE)
leases = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(leases)


@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_exact_generated_legacy_acl_is_recognized(directory):
    flags = "OICI" if directory else "ID"
    for principals in itertools.permutations(("OW", "SY", "BA")):
        value = ("O:BAD:P" if directory else "O:BAD:") + "".join(
            f"(A;{flags};FA;;;{principal})" for principal in principals
        )
        assert leases._legacy_windows_lease_acl(value, directory=directory)


CASES = {
    "foreign-owner": "O:SYD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "public-write": "O:BAD:P(A;OICI;FA;;;WD)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "missing-entry": "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)",
    "extra-entry": "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;WD)",
    "changed-rights": "O:BAD:P(A;OICI;FR;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "deny-entry": "O:BAD:P(D;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "unprotected-directory": "O:BAD:(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "wrong-inheritance": "O:BAD:P(A;ID;FA;;;OW)(A;ID;FA;;;SY)(A;ID;FA;;;BA)",
    "trailing-data": "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)garbage",
    "empty": "",
}


@pytest.mark.parametrize("name", CASES)
def test_foreign_public_partial_or_edited_acl_is_not_repair_authority(name):
    assert not leases._legacy_windows_lease_acl(CASES[name], directory=True)


@pytest.mark.parametrize(("kind", "flags", "mask", "sid", "expected"), [
    (0, 0, 0x1F01FF, "S-1-5-21-1001", True),
    (0, 0x10, 0x1F01FF, "S-1-5-21-1001", True),
    (0, 8, 0x1F01FF, "S-1-5-21-1001", False),
    (1, 0, 0x1F01FF, "S-1-5-21-1001", False),
    (0, 0, 0x120089, "S-1-5-21-1001", False),
    (0, 0, 0x1F01FF, "S-1-5-32-544", False),
    (0, 0, 0x1F01FF, "S-1-3-4", False),
    (0, 0, 0x1F01FF, "S-1-5-21-1002", False),
], ids=["direct", "inherited", "inherit-only", "deny", "read-only", "admin-only",
        "owner-rights", "other-account"])
def test_legacy_repair_requires_actual_account_full_control(kind, flags, mask, sid, expected):
    assert leases._windows_account_full_control(kind, flags, mask, sid, "S-1-5-21-1001") is expected


def test_posix_lease_requests_private_root_before_descendants(tmp_path, monkeypatch):
    """Portable call-boundary control without acquiring a kernel lease."""
    root = tmp_path / "fresh"
    requested = []
    original = Path.mkdir
    def mkdir(path, mode=0o777, parents=False, exist_ok=False):
        if path == root:
            requested.append(mode)
        return original(path, mode=mode, parents=parents, exist_ok=exist_ok)
    class OpenReached(Exception):
        pass
    def opened(*args, **kwargs):
        assert requested == [0o700]
        raise OpenReached()
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(leases, "os", SimpleNamespace(**{
        **vars(os), "name": "posix", "open": opened,
    }))
    with pytest.raises(OpenReached):
        with leases._kernel_lease(root, "fixture", kind="construction", shared=False):
            pytest.fail("probe reached kernel lock")


@pytest.mark.parametrize("existing", [False, True])
def test_candidate_requests_private_root_before_source_phase(tmp_path, monkeypatch, existing):
    repository = SOURCE.parents[2]
    monkeypatch.syspath_prepend(str(repository / "libs/installer-engine"))
    monkeypatch.syspath_prepend(str(repository / "libs/versioned-runtime"))
    path = repository / "libs/installer-engine/installer_engine.py"
    spec = importlib.util.spec_from_file_location("unit_private_first_touch_engine", path)
    engine = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, engine)
    spec.loader.exec_module(engine)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="private-root-fixture"\nversion="1.0"\n'
        '[tool.uv]\nexclude-newer="7 days"\n', encoding="utf-8",
    )
    root = tmp_path / "fresh"
    if existing:
        root.mkdir()
        if os.name != "nt":
            root.chmod(0o755)
    requested = []
    original = Path.mkdir
    def mkdir(path, mode=0o777, parents=False, exist_ok=False):
        if path == root:
            requested.append(mode)
        return original(path, mode=mode, parents=parents, exist_ok=exist_ok)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    original_hash = engine._construction_hash
    class SourceReached(Exception):
        pass
    def fingerprinted(*args, **kwargs):
        digest = original_hash(*args, **kwargs)
        assert len(digest) == 64
        assert requested == [0o700]
        assert (root / ".installer-inputs").is_dir()
        if os.name != "nt":
            monkeypatch.syspath_prepend(str(repository / "fleet-manager/src"))
            from fleet_manager.private_state import private_directory
            mode = stat.S_IMODE(root.stat().st_mode)
            if existing:
                assert mode == 0o755
                with pytest.raises(ValueError, match="not private"):
                    private_directory(root)
            else:
                assert mode == 0o700
                private_directory(root)
        raise SourceReached()
    monkeypatch.setattr(engine, "_construction_hash", fingerprinted)
    with pytest.raises(SourceReached):
        with engine.numbered_candidate(
            root, "1.0", plugin=plugin, uv=Path(sys.executable),
            base_python=Path(sys.executable), health_module="fixture",
        ):
            pytest.fail("probe reached construction")
