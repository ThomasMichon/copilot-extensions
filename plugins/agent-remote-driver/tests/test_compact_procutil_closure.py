"""Copied exports follow both supported process-output module layouts."""

from pathlib import Path

import agent_procutil
import pytest

from agent_remote_driver.session_host import bundle


@pytest.mark.parametrize(("separate_output", "declared"), [
    (False, False), (True, False), (True, True),
])
def test_compact_export_retains_optional_native_output_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, separate_output: bool, declared: bool,
) -> None:
    root = tmp_path / "process-helper"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "owned_process.py").write_text("", encoding="utf-8")
    helper = root / "_windows_owned_output.py"
    if separate_output:
        helper.write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_procutil, "__file__", str(root / "__init__.py"))
    monkeypatch.setattr(bundle, "_DRIVER_MODULES", ())
    monkeypatch.setattr(
        bundle, "_PROCUTIL_MODULES",
        ("owned_process.py", "_windows_owned_output.py") if declared else ("owned_process.py",),
    )

    members = bundle._bundle_sources()
    assert len(members) == len({name for name, _ in members})
    sources = dict(members)
    assert sources["agent_procutil/owned_process.py"] == root / "owned_process.py"
    assert ("agent_procutil/_windows_owned_output.py" in sources) is separate_output
    assert all(path.is_file() for path in sources.values())
