from pathlib import Path
import zipfile

import pytest


@pytest.mark.parametrize("legacy", [False, True])
def test_host_exports_each_required_procutil_member_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool,
) -> None:
    root = Path(__file__).resolve().parents[3]
    for library in ("agent-procutil", "copilot-driver-core", "repository-policy"):
        monkeypatch.syspath_prepend(str(root / "libs" / library / "src"))
    from agent_remote_driver.session_host import bundle

    assert Path(bundle.__file__).resolve().is_relative_to(root / "libs" / "copilot-driver-core")
    monkeypatch.setattr(bundle, "_policy_sources", lambda: ())
    archive_path, _ = bundle.build_session_host_bundle(tmp_path, legacy=legacy)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.namelist()
        assert len(members) == len(set(members))
        assert members.count("agent_procutil/_windows_owned_output.py") == 1
