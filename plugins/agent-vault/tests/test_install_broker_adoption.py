"""Vault's native installer retains completion without legacy broker adoption."""

from pathlib import Path
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_installer_completes_before_activation_and_preserves_client_only_mode():
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert source.index("-Plugin 'agent-vault'") < source.index("# === install-contract:v4 self-stage")
    assert "-not ($Action -eq 'uninstall' -and $DryRun)" in source
    complete = source.split("function Invoke-VersionedMarkComplete {", 1)[1].split("\nfunction ", 1)[0]
    assert "-RetainHelpers" in complete
    install = source.split("function Install-Runtime {", 1)[1].split("\nfunction ", 1)[0]
    reused = install.split("if ($script:RuntimeConstructionReuse) {", 1)[1]
    assert reused.index("Invoke-VersionedMarkComplete") < reused.index("Invoke-VersionedActivate")
    fresh = install.split("$prevVersion = ''", 1)[1]
    assert fresh.index("Invoke-VersionedMarkComplete") < fresh.index("Invoke-VersionedActivate")
    assert "BrokerAdoption" not in source
    remove = source.split("function Invoke-Uninstall {", 1)[1].split("\nfunction ", 1)[0]
    assert remove.index("--remove-broker") < remove.index("Unregister-ScheduledTask")
    assert remove.index("--remove-broker") < remove.index("Remove-InstallRuntimeContent")
