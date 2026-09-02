from pathlib import Path


def test_init_completion_color_is_attached_to_write_host():
    script = (
        Path(__file__).parents[1] / "scripts" / "init.ps1"
    ).read_text(encoding="utf-8")

    assert "} -ForegroundColor DarkGray" not in script
    assert (
        "Write-Host '  Try: agent-machines version' -ForegroundColor DarkGray"
        in script
    )
