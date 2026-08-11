"""Entry point for the copilot-extensions Configurator.

Phase 1: on top of the Phase 0 out-of-plugin skeleton, this adds the
**dependency-free plugin-knowledge model** — the installer's own declarative
catalog of each plugin's prerequisites, managed config, and "what to do" to make
it ready (see ``catalog.py`` + ``data/plugins.toml``). Later phases fill in the
real work — prerequisite provisioning, core install via the harness's own flow,
first-repo adoption + discovery, the non-agentic visual configurator, and
Git-referenced presets — see the effort ``installer-configurator`` and umbrella
issue #352.

Everything here is **programmatic and non-agentic**: no AI agent is in the loop.
"""

from __future__ import annotations

import sys

from . import __version__
from .catalog import load_catalog
from .model import Model, build_model, coverage, effective_prereqs

_BANNER = "copilot-extensions Configurator"
_TAGLINE = "the standalone, out-of-plugin installer & configurator"

# Plugins are shown most-setup-first: the core install engine, then services with
# their own installers, then Python libraries, then skills-only knowledge plugins.
KIND_ORDER = {"core": 0, "service": 1, "library": 2, "knowledge": 3}

# The build-out roadmap, shown so a first run is self-explanatory. Kept in step
# with the vision's Features and the umbrella's phase issues (#353-#358).
_ROADMAP = [
    ("0", "out-of-plugin app + one-line bootstrap", "done"),
    ("1", "know each plugin's prerequisites & config (dependency-free)", "you are here"),
    ("2", "install prerequisites (restart-aware) + the agent-worktrees core", ""),
    ("3", "adopt a first harness repo + discover/register others", ""),
    ("4", "a non-agentic visual configurator (doctor / config / validate)", ""),
    ("5", "Git-referenced presets", ""),
]


def _print_intro() -> None:
    print()
    print(f"  {_BANNER}")
    print(f"  {_TAGLINE}")
    print(f"  version {__version__}  ·  Phase 1 — plugin knowledge")
    print()
    print("  Build-out roadmap (issue #352):")
    for num, desc, here in _ROADMAP:
        marker = f"  <- {here}" if here else ""
        print(f"    {num}. {desc}{marker}")
    print()
    print("  Nothing is installed yet. This build knows the plugins (Phase 1);")
    print("  actually provisioning them lands in Phase 2. Run `configurator")
    print("  plugins` to see what the installer knows.")
    print()


def _fmt_prereq(pr) -> str:
    bits = [pr.name]
    if pr.min:
        bits.append(f">={pr.min}")
    if pr.optional:
        bits.append("(optional)")
    return " ".join(bits)


def _source_line(model: Model) -> str:
    s = model.source
    if s.kind == "checkout":
        return f"discovered from checkout: {s.detail}"
    if s.kind == "remote":
        return f"discovered from remote marketplace: {s.detail}"
    return "no marketplace reachable — showing the authored catalog (unconfirmed)"


def _print_plugins_table(model: Model) -> None:
    print()
    print(f"  {_BANNER} — effective plugin model")
    authored = sum(1 for ep in model.plugins if ep.authored)
    inferred = len(model.plugins) - authored
    print(f"  {len(model.plugins)} plugins · {authored} authored · {inferred} inferred")
    print(f"  {_source_line(model)}")
    print()
    width = max((len(ep.plugin.name) for ep in model.plugins), default=0)
    for ep in sorted(model.plugins,
                     key=lambda e: (KIND_ORDER.get(e.plugin.kind, 99), e.plugin.name)):
        p = ep.plugin
        prereqs = ", ".join(_fmt_prereq(pr) for pr in p.prereqs) or "—"
        mark = "  " if ep.authored else " *"
        print(f"   {mark}{p.name.ljust(width)}  [{p.kind}]  {prereqs}")
    if inferred:
        print()
        print("  * = discovered but not in the authored catalog — running on inferred")
        print("    defaults. Add an entry to data/plugins.toml to teach the installer.")
    print()
    print("  `configurator plugins <name>` for detail · "
          "`--prereqs` for the union · `--reconcile` for coverage.")
    print()


def _print_plugin_detail(model: Model, name: str) -> int:
    ep = model.get(name)
    if ep is None:
        print(f"error: unknown plugin {name!r}. Known: {', '.join(model.names)}")
        return 2
    p = ep.plugin
    print()
    tag = "authored" if ep.authored else "inferred (no catalog entry yet)"
    print(f"  {p.name}  [{p.kind}]  · {tag}")
    if p.summary:
        print(f"  {p.summary}")
    if p.depends_on:
        print(f"  depends on: {', '.join(p.depends_on)}")
    print()
    print("  prerequisites:")
    if p.prereqs:
        for pr in p.prereqs:
            src = f" ({pr.source})" if pr.source else ""
            note = f" — {pr.notes}" if pr.notes else ""
            print(f"    - {_fmt_prereq(pr)}{src}{note}")
    else:
        print("    (none beyond the baseline)")
    if p.config:
        print("  config it manages:")
        for c in p.config:
            print(f"    - {c.path} — {c.description}")
    print("  what to do:")
    for s in p.steps:
        print(f"    - {s.what}")
        if s.runs:
            print(f"        $ {s.runs}")
    print()
    return 0


def _print_prereqs(model: Model) -> None:
    cat = load_catalog()
    print()
    print("  Prerequisites across the whole harness (baseline + all plugins):")
    print()
    for pr in effective_prereqs(model, cat.baseline_prereqs):
        note = f" — {pr.notes}" if pr.notes else ""
        print(f"    - {_fmt_prereq(pr)}{note}")
    print()


def _print_reconcile() -> int:
    cov = coverage()
    print()
    print(f"  Catalog coverage of the discovered membership (source: {cov.source_kind}).")
    print()
    if cov.source_kind == "none":
        print("  no marketplace reachable (no checkout, remote fetch failed) — "
              "cannot confirm membership. The authored catalog still stands alone.")
        print()
        return 0
    if cov.uncovered:
        print("  ○ discovered but NOT in the authored catalog (running on inferred")
        print("    defaults — add an entry to data/plugins.toml to teach the installer):")
        for n in cov.uncovered:
            print(f"      - {n}")
    if cov.phantom:
        print("  ✗ in the authored catalog but NOT discovered (phantom/renamed — fix it):")
        for n in cov.phantom:
            print(f"      - {n}")
    if cov.published_prereq_gaps:
        print("  ✗ a plugin publishes a prereq the catalog does not carry as "
              "source=\"published\":")
        for plug, pr in cov.published_prereq_gaps:
            print(f"      - {plug}: {pr}")
    if cov.ok and not cov.uncovered:
        print("  ✓ every discovered plugin has an authored catalog entry; no drift.")
    elif cov.ok:
        print()
        print("  ✓ no errors (uncovered plugins are handled by inference).")
    print()
    return 0 if cov.ok else 1


def _cmd_plugins(rest: list[str]) -> int:
    head = rest[0] if rest else ""
    if head in ("--reconcile", "-r"):
        return _print_reconcile()
    model = build_model()
    if not rest:
        _print_plugins_table(model)
        return 0
    if head in ("--prereqs", "-p"):
        _print_prereqs(model)
        return 0
    if head.startswith("-"):
        print(f"error: unknown option {head!r} for `plugins`.")
        return 2
    return _print_plugin_detail(model, head)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"configurator {__version__}")
        return 0
    if args and args[0] in ("--help", "-h"):
        print("usage: configurator [--version] [--help] [plugins [<name>|--prereqs|--reconcile]]")
        print()
        print("The standalone copilot-extensions installer & configurator.")
        print()
        print("commands:")
        print("  (no args)              show the app banner + build-out roadmap")
        print("  plugins                list the plugins the installer knows about")
        print("  plugins <name>         show one plugin's prereqs / config / steps")
        print("  plugins --prereqs      the de-duplicated union of all prerequisites")
        print("  plugins --reconcile    coverage of the discovered membership by the catalog")
        print()
        print("Phase 1 builds the dependency-free plugin-knowledge model; later")
        print("phases add prerequisite install, core install, repo adoption/")
        print("discovery, the visual configurator, and presets (issue #352).")
        return 0
    if args and args[0] == "plugins":
        return _cmd_plugins(args[1:])
    _print_intro()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
