"""Private copied SDK fixtures that retain the real adapter and lifetime worker."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

import test_installation_context
from test_installation_context import LIB, _run_ps


def _copy_sdk(destination: Path, *, owner_hook: str = "") -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for source in LIB.iterdir():
        if source.is_file() and source.suffix in {".py", ".sh", ".ps1", ".awk", ".pyz"}:
            shutil.copyfile(source, destination / source.name)
    if owner_hook:
        _inject_owner_hook(destination, owner_hook)
    return destination


def _inject_owner_hook(library: Path, hook: str) -> None:
    owner = library / "installation_context.py"
    source = owner.read_text(encoding="utf-8")
    marker = "\n_load_fragments()\n"
    assert source.count(marker) == 1
    owner.write_text(source.replace(marker, marker + "\n" + hook + "\n", 1), encoding="utf-8")


def _run_owner_probe(
    work_root: Path, body: str, *, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    hook = (
        "def _powershell_main(action, parameters, supplied):\n"
        "    assert action == 'source-id'\n"
        "    try:\n"
        + "".join("        " + line + "\n" for line in body.splitlines())
        + "        return 0\n"
        "    except InstallationContextError as error:\n"
        "        print(str(error), file=sys.stderr)\n"
        "        return 1\n"
    )
    library = _copy_sdk(work_root / "sdk", owner_hook=hook)
    # Reuse the public adapter runner, selecting only this fixture's copied pair.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(test_installation_context, "SCRIPT", library / "installation-context.ps1")
        return _run_ps(
            "source-id", "-SourceJson", '{"source":"github","repo":"example/marketplace"}',
            "-CopilotHome", work_root / "copilot", "-DurableHome", work_root / "durable",
            env=environment, check=False,
        )
