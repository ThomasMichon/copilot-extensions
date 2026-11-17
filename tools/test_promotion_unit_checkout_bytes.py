"""Real Git materialization must preserve the admitted literal payload bytes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from promotion_unit_contract import UnitEvidenceError, verify_sources

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_fresh_checkout_keeps_admitted_powershell_payload_bytes(
    tmp_path: Path, autocrlf: str,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", "--no-replace-objects", *arguments], cwd=root, env=environment,
            check=True, capture_output=True, timeout=30,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW")) if os.name == "nt" else 0,
        )

    manifest = json.loads((ROOT / "tools/promotion-unit-manifest.json").read_bytes())
    payloads: list[str] = [name for name in manifest["sources"] if name.endswith(".ps1")]
    assert payloads
    admitted_payloads = tuple(payloads)
    names = {Path(name).name for name in payloads}
    ignored = {
        "tests", "fixtures", "__pycache__", "node_modules", ".venv",
        ".test-venvs", ".test-runs", "build", "dist", "obj",
    }
    for owner in ("libs", "plugins", "worktree-manager", "fleet-manager", "services"):
        for directory, children, files in os.walk(ROOT / owner):
            children[:] = [name for name in children if name not in ignored]
            payloads.extend(
                (Path(directory) / name).relative_to(ROOT).as_posix()
                for name in files if name in names
            )
    payloads = sorted(set(payloads))
    content = b"Write-Output 'literal promotion fixture'\n"
    sources = dict.fromkeys(admitted_payloads, hashlib.sha256(content).hexdigest())
    attributes = root / ".gitattributes"
    attributes.write_bytes(b"*.ps1 text eol=crlf\n")
    for name in payloads:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    git("init", "--quiet")
    git("config", "core.autocrlf", autocrlf)
    git("config", "core.longpaths", "true")
    git("add", "--", ".gitattributes", *payloads)

    def materialize() -> None:
        for name in payloads:
            (root / name).unlink()
        git("checkout-index", "--all", "--force")

    materialize()
    with pytest.raises(UnitEvidenceError, match="source changed without admission"):
        verify_sources(root, sources)
    attributes.write_bytes((ROOT / ".gitattributes").read_bytes())
    git("add", "--", ".gitattributes")
    materialize()
    verify_sources(root, sources)
    for name in payloads:
        assert (root / name).read_bytes() == content, name
