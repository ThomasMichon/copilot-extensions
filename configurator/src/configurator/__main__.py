"""Entry point for the copilot-extensions Configurator.

Phase 0 skeleton: proves the out-of-plugin app runs end-to-end from the one-line
bootstrap. Later phases fill in the real work — prerequisite provisioning, core
install via the harness's own flow, first-repo adoption + discovery, the
non-agentic visual configurator, and Git-referenced presets — see the effort
``installer-configurator`` and umbrella issue #352.

Everything here is **programmatic and non-agentic**: no AI agent is in the loop.
"""

from __future__ import annotations

import sys

from . import __version__

_BANNER = "copilot-extensions Configurator"
_TAGLINE = "the standalone, out-of-plugin installer & configurator"

# The build-out roadmap, shown so a first run is self-explanatory. Kept in step
# with the vision's Features and the umbrella's phase issues (#353-#358).
_ROADMAP = [
    ("0", "out-of-plugin app + one-line bootstrap", "you are here"),
    ("1", "know each plugin's prerequisites & config (dependency-free)", ""),
    ("2", "install prerequisites (restart-aware) + the agent-worktrees core", ""),
    ("3", "adopt a first harness repo + discover/register others", ""),
    ("4", "a non-agentic visual configurator (doctor / config / validate)", ""),
    ("5", "Git-referenced presets", ""),
]


def _print_intro() -> None:
    print()
    print(f"  {_BANNER}")
    print(f"  {_TAGLINE}")
    print(f"  version {__version__}  ·  Phase 0 skeleton")
    print()
    print("  Build-out roadmap (issue #352):")
    for num, desc, here in _ROADMAP:
        marker = f"  <- {here}" if here else ""
        print(f"    {num}. {desc}{marker}")
    print()
    print("  Nothing is installed yet — this is the Phase 0 skeleton. It exists")
    print("  to prove the app is delivered and runs OUTSIDE the plugin pipe.")
    print()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"configurator {__version__}")
        return 0
    if args and args[0] in ("--help", "-h"):
        print("usage: configurator [--version] [--help]")
        print()
        print("The standalone copilot-extensions installer & configurator.")
        print("Phase 0 skeleton — later phases add prerequisites, core install,")
        print("repo adoption/discovery, the visual configurator, and presets.")
        return 0
    _print_intro()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
