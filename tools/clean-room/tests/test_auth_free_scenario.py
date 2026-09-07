"""Tier-P scenarios declaring no Copilot auth must not borrow credentials."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("auth", ["none", "required", "malformed"])
def test_powershell_uses_manifest_auth_contract(tmp_path, auth):
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    if auth == "malformed":
        (scenario / "manifest.json").write_text("{not valid json", encoding="utf-8")
    else:
        (scenario / "manifest.json").write_text(json.dumps({"tier": "P", "auth": {"copilot": auth}}), encoding="utf-8")
    source = (RIG / "run.ps1").read_text(encoding="utf-8")
    start = source.index("function Start-Container {")
    function = source[start:source.index("\nfunction Ensure-Container", start)]
    quote = lambda p: str(p).replace("'", "''")
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "function Resolve-CopilotToken { throw 'authentication-requested' }\n"
        "function Test-Image { return $true }\n"
        "function docker { if ($args[0] -eq 'run') { $script:selectedImage=$args[-2] } }\n"
        f"$ScenarioDir='{quote(scenario)}'; $Results='{quote(tmp_path / 'out')}'\n"
        f"$LibDir='{quote(RIG / 'lib')}'\n"
        "$BaseTag='base-image'; $AuthTag='authed-image'; $Container='fixture'\n"
        "$PassEnv=@(); $HarnessMount=''; $ScenarioSharedLib=$null\n"
        + function + "\nStart-Container\nWrite-Output \"selected=$script:selectedImage\"\n",
        encoding="utf-8",
    )
    result = subprocess.run([POWERSHELL, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=30)
    if auth == "none":
        assert result.returncode == 0, result.stderr
        assert "selected=base-image" in result.stdout
    else:
        # "required" and "malformed" both fail closed to requiring auth: a
        # scenario manifest that cannot be parsed must never be silently
        # treated as auth-free, and must not crash the rig either.
        assert result.returncode != 0
        assert "authentication-requested" in result.stderr


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX Bash required")
def test_bash_auth_free_branch_precedes_token_lookup():
    source = (RIG / "run.sh").read_text(encoding="utf-8")
    body = source.split("start_container() {", 1)[1]
    assert body.index('no_scenario_auth="$("$(_py)"') < body.index('token="$(resolve_token)"')
    assert 'if [ "$no_scenario_auth" = true ]; then' in body


BASH = shutil.which("bash")


@pytest.mark.skipif(os.name == "nt" or not BASH, reason="POSIX Bash required")
def test_bash_manifest_parsing_uses_host_python_fallback_not_hardcoded_python3(tmp_path):
    # A host with only `python` (no `python3` binary) must still resolve the
    # Tier-P auth-free contract instead of hard-failing on a missing binary.
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "manifest.json").write_text(json.dumps({"tier": "P", "auth": {"copilot": "none"}}), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_python = shutil.which("python3") or shutil.which("python")
    assert real_python, "a Python interpreter is required to run this probe"
    (fake_bin / "python").write_text(f'#!/bin/sh\nexec "{real_python}" "$@"\n', encoding="utf-8")
    os.chmod(fake_bin / "python", 0o755)
    source = (RIG / "run.sh").read_text(encoding="utf-8")
    body = source[source.index("start_container() {"):]
    # Slice up to (not including) the first line after the auth/image-selection
    # if/elif/else/fi block -- a stable anchor, unlike brace-matching against a
    # block that closes with `fi`, not `}`.
    body = body[: body.index('docker rm -f "$CONTAINER"')] + "\n}\n"
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        "_py() { command -v python3 || command -v python; }\n"
        "resolve_token() { :; }\n"
        "img_exists() { return 1; }\n"
        "do_build() { :; }\n"
        f'SCENARIO_DIR="{scenario}"\nBASE_TAG=base-image\n'
        + body
        + "\nstart_container\necho \"img=$img\"\n",
        encoding="utf-8",
    )
    # Restrict PATH to the fake bin only so `_py()` cannot find a real python3
    # anywhere else on this host; resolve bash by absolute path (not a PATH
    # search) so restricting the child's PATH cannot also hide bash itself.
    env = {**os.environ, "PATH": str(fake_bin)}
    result = subprocess.run([BASH, str(probe)], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    assert "img=base-image" in result.stdout


def test_version_witness_normalizes_only_dev_spelling():
    source = (RIG / "scenarios" / "agent-machines-installation-cells" / "scenario.py").read_text(encoding="utf-8")
    function = source.split("def reports_version(", 1)[1].split("\ndef next_dev_version", 1)[0]
    namespace = {}
    exec("def reports_version(" + function, namespace)
    witness = namespace["reports_version"]
    assert witness("agent-machines 1.0.0.dev3\n", "1.0.0-dev3")
    assert not witness("agent-machines 1.0.0.dev30\n", "1.0.0-dev3")
    assert not witness("not-agent-machines 1.0.0.dev3\n", "1.0.0-dev3")
