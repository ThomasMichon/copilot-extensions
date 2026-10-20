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
    ("agent-worktrees", "install.sh", "provision"),
    ("agent-dispatch", "install.sh", "install"),
    ("agent-index", "install.sh", "install"),
    ("agent-logger", "install.sh", "provision"),
    ("agent-vault", "install.sh", "provision"),
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


def _version_witness(plugin, version_command, reported):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    generation = "1.2.3+source." + "a" * 40 + "." + "0" * 16
    source = SCENARIO.read_text(encoding="utf-8")
    start = source.index("    _verify_source_version() {")
    function = source[start:source.index("\n    }\n", start) + len("\n    }\n")]
    return subprocess.run(
        [bash, "-c", 'set -eu\nGENERATION="$1"\nfixture_report="$2"\n'
         'PLUGIN="$3"\nversion_command="$4"\n'
         'bash() { [ "$1" = -lc ] && [ "$2" = "$version_command" ] || return 2; '
         'printf "%s" "$fixture_report"; }\n' + function
         + "\n_verify_source_version", "source-version", generation,
         reported.format(plugin=plugin, generation=generation), plugin, version_command],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.skipif(os.name == "nt", reason="native POSIX scenario")
@pytest.mark.parametrize(("plugin", "version_command"), [
    ("agent-codespaces", "agent-codespaces version"),
    ("agent-dispatch", "agent-dispatch --version"),
    ("agent-logger", "agent-logger version"),
    ("agent-vault", "agent-vault --version"),
    ("agent-worktrees", "agent-worktrees --version"),
])
@pytest.mark.parametrize(("reported", "accepted"), [
    ("{plugin} {generation}", True),
    ("{plugin} {generation} (abcdef01)", True),
    ("{plugin} {generation}0 (abcdef01)", False),
    ("{plugin} 1.2.3 (abcdef01)", False),
])
def test_source_version_witness_matches_the_exact_command_field(plugin, version_command, reported, accepted):
    result = _version_witness(plugin, version_command, reported)
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="native POSIX scenario")
@pytest.mark.parametrize(("reported", "accepted"), [
    ("{generation}", True),
    ("{generation} unexpected", False),
    ("agent-index {generation}", False),
    ("1.2.3", False),
])
def test_index_source_version_witness_requires_the_exact_bare_generation(reported, accepted):
    result = _version_witness("agent-index", "agent-index version", reported)
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
