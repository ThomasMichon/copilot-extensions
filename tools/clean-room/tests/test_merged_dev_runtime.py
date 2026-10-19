"""Fresh source witnesses must publish the command they subsequently exercise."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "merged-dev-runtime" / "scenario.sh"


@pytest.mark.skipif(os.name == "nt", reason="native POSIX scenario")
@pytest.mark.parametrize(("plugin", "installer", "action"), [
    ("agent-bridge", "install.sh", "install"),
    ("agent-codespaces", "install.sh", "install"),
    ("agent-containers", "init.sh", "provision"),
    ("agent-mcp", "init.sh", "provision"),
    ("agent-machines", "init.sh", "provision"),
    ("agent-ssh", "install.sh", "provision"),
    ("agent-pull-requests", "install.sh", "provision"),
    ("budget-guidance", "install.sh", "provision"),
])
def test_fresh_installer_action_publishes_required_command(plugin, installer, action):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    source = SCENARIO.read_text(encoding="utf-8")
    selection = source[source.index('case "$PLUGIN" in'):source.index('\nphase 0 ')]
    result = subprocess.run(
        [bash, "-c", 'set -eu\nPLUGIN="$1"\n' + selection
         + '\nprintf "%s\\n%s\\n" "$INSTALLER" "$INSTALL_ACTION"', "scenario-selection", plugin],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [installer, action]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX scenario")
@pytest.mark.parametrize(("reported", "accepted"), [
    ("agent-codespaces {generation}", True),
    ("agent-codespaces {generation} (abcdef01)", True),
    ("agent-codespaces {generation}0 (abcdef01)", False),
    ("agent-codespaces 1.2.3 (abcdef01)", False),
])
def test_source_version_witness_matches_the_exact_command_field(reported, accepted):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    generation = "1.2.3+source." + "a" * 40 + "." + "0" * 16
    source = SCENARIO.read_text(encoding="utf-8")
    start = source.index("    _verify_codespaces_source_version() {")
    function = source[start:source.index("\n    }\n", start) + len("\n    }\n")]
    result = subprocess.run(
        [bash, "-c", 'set -eu\nGENERATION="$1"\nfixture_report="$2"\n'
         'bash() { printf "%s" "$fixture_report"; }\n' + function
         + "\n_verify_codespaces_source_version", "source-version", generation,
         reported.format(generation=generation)],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
