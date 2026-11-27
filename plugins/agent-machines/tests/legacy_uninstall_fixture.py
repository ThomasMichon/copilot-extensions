"""Fixture-only bootstrap closure and closed-module process observation."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import shutil
import sys

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


def isolate_process_observation(payload: Path) -> None:
    """Keep POSIX filesystem tests out of the host's privileged process census."""
    helper = payload / "scripts/installer_engine_uninstall.py"
    assert not helper.samefile(PLUGIN / "scripts/installer_engine_uninstall.py")
    profile = json.loads((payload / "scripts/uninstall-profile.json").read_text(encoding="utf-8"))
    assert str(profile["module"]).startswith("finite_fixture_")
    text = helper.read_text(encoding="utf-8")
    needle = '    """Observe current-user consumers; observation never grants stop authority."""\n'
    assert text.count(needle) == 1
    observation = (
        "    if os.environ.get('MACHINES_TEST_CENSUS_UNAVAILABLE') == '1':\n"
        "        raise UninstallRefused('Fixture-owned process census is unavailable')\n"
        "    if os.name != 'nt':\n"
        "        arguments = [sys.executable, *sys.argv]\n"
        "        return [{'pid': os.getpid(), 'ppid': os.getppid(),\n"
        "                 'image': sys.executable, 'argv': arguments,\n"
        "                 'command': ' '.join(arguments)}]\n"
    )
    helper.write_text(text.replace(needle, needle + observation), encoding="utf-8", newline="\n")


def materialize_bootstrap(payload: Path) -> None:
    """Prepare a copied payload without allowing explicit setup to acquire tools."""
    previous = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_installer_engine.py"))
        for source, target in owner["derived_file_pairs"](
            REPO, payload.parent / "materialized", copied_consumer=(PLUGIN, payload),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    finally:
        sys.path[:] = previous
    for suffix in ("sh", "ps1"):
        provider = payload / f"scripts/bootstrap-uv.{suffix}"
        text = provider.read_text(encoding="utf-8")
        if suffix == "sh":
            needle = '    exec "$@"\n'
            interception = (
                '    if [[ "${2:-}" == python && "${3:-}" == install ]]; then\n'
                '        return 0\n'
                '    fi\n'
            )
        else:
            needle = "    $result = & $InvokeCaptured @options\n"
            interception = (
                "    if ($Arguments[0] -eq 'python' -and $Arguments[1] -eq 'install') {\n"
                "        return [pscustomobject]@{ExitCode=0; Stdout=''; Stderr=''}\n"
                "    }\n"
            )
        assert text.count(needle) == 1
        provider.write_text(text.replace(needle, interception + needle), encoding="utf-8", newline="\n")
    isolate_process_observation(payload)
