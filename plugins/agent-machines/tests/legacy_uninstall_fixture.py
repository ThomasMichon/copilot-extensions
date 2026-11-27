"""Fixture-only bootstrap closure and closed-module process observation."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import shutil
import sys

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


def materialize_capture(payload: Path, *, source: Path = PLUGIN) -> None:
    """Use the selected consumer's declared Invocation capture assets."""
    manifest = source / "payload-invocation.json"
    owner = runpy.run_path(str(REPO / "libs/payload-invocation/bootstrap_python.py"))
    outputs = owner["expected_files"](
        manifest, json.loads(manifest.read_bytes()), canonical_root=REPO,
    )
    selected = {
        target: content for target, content in outputs.items()
        if target.parent.name == "_bootstrap_python"
        and target.name in {"bootstrap-capture.ps1", "bootstrap-capture.exe"}
    }
    assert len(selected) == 2
    for original, content in selected.items():
        target = payload / original.relative_to(source)
        assert isinstance(content, bytes)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def isolate_process_observation(payload: Path, *, source: Path = PLUGIN) -> None:
    """Keep POSIX filesystem tests out of the host's privileged process census."""
    helper = payload / "scripts/installer_engine_uninstall.py"
    assert not helper.samefile(source / "scripts/installer_engine_uninstall.py")
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


def materialize_bootstrap(payload: Path, *, source: Path = PLUGIN) -> None:
    """Prepare a copied payload without allowing explicit setup to acquire tools."""
    previous = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_installer_engine.py"))
        for original, target in owner["derived_file_pairs"](
            REPO, payload.parent / "materialized", copied_consumer=(source, payload),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
    finally:
        sys.path[:] = previous
    materialize_capture(payload, source=source)
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
    isolate_process_observation(payload, source=source)
