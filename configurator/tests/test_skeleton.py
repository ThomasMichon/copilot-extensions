"""Phase 0 skeleton tests: the app runs, reports its version, and stays a
programmatic, non-agentic, out-of-plugin entry point.
"""

from __future__ import annotations

from configurator import __version__
from configurator.__main__ import main


def test_version_flag(capsys):
    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_help_flag(capsys):
    assert main(["--help"]) == 0
    assert "configurator" in capsys.readouterr().out.lower()


def test_bare_run_prints_intro_and_roadmap(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Configurator" in out
    # The roadmap is shown so a first run is self-explanatory.
    assert "one-line bootstrap" in out
    assert "you are here" in out


def test_is_not_a_plugin_payload():
    # Guard the boundary: the configurator must not be delivered as a plugin.
    # It has no plugin.json and lives outside plugins/ (checked at the repo
    # level by the effort's validation); here we assert it declares no Copilot
    # plugin entry surface in its own package metadata.
    import configurator

    assert not hasattr(configurator, "PLUGIN_MANIFEST")
