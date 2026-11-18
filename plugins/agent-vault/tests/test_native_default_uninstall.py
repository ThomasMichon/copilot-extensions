"""Default-owner metadata does not make an owned daemon unrecognizable."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import venv
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows process identity")


def _observer() -> ModuleType:
    source = Path(__file__).parents[1] / "scripts" / "vault-uninstall-process.py"
    spec = importlib.util.spec_from_file_location("vault_default_uninstall_observer", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("native_default", [False, True])
def test_uninstall_recognizes_exact_default_owner_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrapped: bool,
    native_default: bool,
) -> None:
    import sys

    monkeypatch.setattr(sys, "path", list(sys.path))
    observer = _observer()
    root = tmp_path / "owned runtime"
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    package = slot / "Lib" / "site-packages"
    homes = [
        line.split("=", 1)[1].strip()
        for line in (slot / "pyvenv.cfg").read_text().splitlines()
        if line.startswith("home =")
    ]
    assert len(homes) == 1
    image = str(Path(homes[0]) / "python.exe")
    daemon, service = observer._contracts()
    invocation = [
        image,
        "-B",
        *(["-c", daemon, str(package)] if wrapped else []),
        "-c",
        service,
        str(package),
        "--foreground",
        "--persistent",
        "--configuration-root",
        str(tmp_path / "removed configuration"),
        *(["--native-default-owner"] if native_default else []),
    ]
    request = {
        "root": str(root),
        "image": image,
        "command": subprocess.list2cmdline(invocation),
    }
    assert observer.admits(request)
    for unexpected in ("--unknown-owner", "--native-default-owner"):
        invalid = [*invocation, unexpected, "--native-default-owner"]
        assert not observer.admits({**request, "command": subprocess.list2cmdline(invalid)})
