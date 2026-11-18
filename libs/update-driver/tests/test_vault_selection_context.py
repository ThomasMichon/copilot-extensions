"""Accepted Vault selection does not come from a later queue worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from update_driver import Request, drain, first_use, status

KEY = "_AGENT_VAULT_FIRST_USE_ROOT"
pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native first-use execution",
)


@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("materialized", [False, True])
def test_vault_first_use_root_survives_a_different_worker(
    owning_payload: tuple[Path, Path, Path],
    area: Path,
    monkeypatch: pytest.MonkeyPatch,
    present: bool,
    materialized: bool,
) -> None:
    payload, installation, project = owning_payload
    if materialized:
        canonical = (
            Path(__file__).resolve().parents[2] / "installer-engine" / "installer_driver.py"
        )
        (payload / "scripts" / "installer_driver.py").write_bytes(canonical.read_bytes())
    for name in ("install.ps1", "install.sh"):
        path = payload / "scripts" / name
        record = (
            "[IO.File]::WriteAllText((Join-Path $InstallDir 'selected-root'), "
            "[string]$env:_AGENT_VAULT_FIRST_USE_ROOT)\n"
            if name.endswith(".ps1")
            else 'printf "%s" "${_AGENT_VAULT_FIRST_USE_ROOT:-}" >"$3/selected-root"\n'
        )
        path.write_text(
            path.read_text().replace("exit 7", record + "exit 7"),
            encoding="utf-8",
        )
    if present:
        monkeypatch.setenv(KEY, str(project))
    else:
        monkeypatch.delenv(KEY, raising=False)
    root = area / "queue"
    request = first_use.enqueue(payload, installation, project, root=root)
    monkeypatch.setenv(KEY, str(area / "unrelated-worker"))
    assert drain(root=root) == 0
    result = status(request.id, root=root)
    assert isinstance(result, Request)
    assert result.exit_code == 7, result.log_path.read_text()
    assert (installation / "selected-root").read_text() == (str(project) if present else "")
