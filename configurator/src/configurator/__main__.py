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
from .core_install import core_status, install_command, install_core
from .harness_state import (
    build_projects,
    build_repos,
    user_enabled_plugins,
)
from .model import Model, build_model, coverage, effective_prereqs
from .prereqs import current_os, detect_baseline, missing
from .provision import apply as provision_apply
from .provision import plan as provision_plan
from .provision import restart_needed

_BANNER = "copilot-extensions Configurator"
_TAGLINE = "the standalone, out-of-plugin installer & configurator"

# Plugins are shown most-setup-first: the core install engine, then services with
# their own installers, then Python libraries, then skills-only knowledge plugins.
KIND_ORDER = {"core": 0, "service": 1, "library": 2, "knowledge": 3}

# The build-out roadmap, shown so a first run is self-explanatory. Kept in step
# with the vision's Features and the umbrella's phase issues (#353-#358).
_ROADMAP = [
    ("0", "out-of-plugin app + one-line bootstrap", "done"),
    ("1", "know each plugin's prerequisites & config (dependency-free)", "done"),
    ("2", "install prerequisites (restart-aware) + the agent-worktrees core", "you are here"),
    ("3", "adopt a first harness repo + discover/register others", ""),
    ("4", "a non-agentic visual configurator (doctor / config / validate)", ""),
    ("5", "Git-referenced presets", ""),
]


def _print_intro() -> None:
    print()
    print(f"  {_BANNER}")
    print(f"  {_TAGLINE}")
    print(f"  version {__version__}  ·  Phase 2 — prerequisites & core install")
    print()
    print("  Build-out roadmap (issue #352):")
    for num, desc, here in _ROADMAP:
        marker = f"  <- {here}" if here else ""
        print(f"    {num}. {desc}{marker}")
    print()
    print("  Run `configurator doctor` to check prerequisites + the core install,")
    print("  `configurator setup` to plan it (add --apply to execute), and")
    print("  `configurator plugins` to see what the installer knows.")
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
    if head in ("--status", "-s"):
        _print_plugins_status(model)
        return 0
    if head.startswith("-"):
        print(f"error: unknown option {head!r} for `plugins`.")
        return 2
    return _print_plugin_detail(model, head)


def _print_plugins_status(model: Model) -> None:
    """Known plugins (from the catalog/marketplace) vs. what is enabled in the
    user-global Copilot settings, with the marketplace each is enabled from."""
    enabled = {e.name: e for e in user_enabled_plugins() if e.enabled}
    print()
    print(f"  {_BANNER} — plugin install/enablement status")
    print(f"  {len(enabled)} plugins enabled user-global")
    print()
    width = max((len(ep.plugin.name) for ep in model.plugins), default=0)
    for ep in sorted(model.plugins,
                     key=lambda e: (KIND_ORDER.get(e.plugin.kind, 99), e.plugin.name)):
        name = ep.plugin.name
        e = enabled.get(name)
        if e:
            mark, state = "✓", f"enabled @{e.marketplace}"
        else:
            mark, state = "·", "not enabled user-global"
        print(f"    {mark} {name.ljust(width)}  [{ep.plugin.kind}]  {state}")
    # Enabled plugins the catalog doesn't know about (other marketplaces).
    known = model.names
    extra = sorted(e.qualified for n, e in enabled.items() if n not in known)
    if extra:
        print()
        print(f"  + {len(extra)} more enabled from other marketplaces "
              "(not copilot-extensions plugins):")
        for q in extra[:12]:
            print(f"      {q}")
        if len(extra) > 12:
            print(f"      … and {len(extra) - 12} more")
    print()


def _cmd_projects(rest: list[str]) -> int:
    projects = build_projects()
    if rest and not rest[0].startswith("-"):
        name = rest[0]
        proj = next((p for p in projects if p.name == name), None)
        if proj is None:
            print(f"error: unknown project {name!r}. "
                  f"Known: {', '.join(p.name for p in projects)}")
            return 2
        print()
        print(f"  project: {proj.name}")
        print(f"    config dir:     {proj.config_dir or '?'}")
        print(f"    expose agent:   {proj.expose_agent}")
        print(f"    knowledge repo: {proj.knowledge_repo or '(none linked)'}")
        print(f"    profiles:       {proj.profiles}")
        if proj.repo:
            print(f"    checkout:       {proj.repo.path or '?'}  [{proj.repo.klass}]")
            print(f"    remote:         {proj.repo.remote or '?'}")
            print(f"    pr model:       {proj.repo.pr_model}")
        print(f"    enabled plugins: {len(proj.enabled_plugins)}")
        for q in list(proj.enabled_plugins)[:20]:
            print(f"      - {q}")
        if len(proj.enabled_plugins) > 20:
            print(f"      … and {len(proj.enabled_plugins) - 20} more")
        print()
        return 0
    print()
    print(f"  {_BANNER} — Projects  (harness repos with binstubs + profiles)")
    print(f"  {len(projects)} registered")
    print()
    width = max((len(p.name) for p in projects), default=0)
    for p in projects:
        klass = p.repo.klass if p.repo else "?"
        kr = f" · knowledge:{p.knowledge_repo}" if p.knowledge_repo else ""
        print(f"    {p.name.ljust(width)}  [{klass}]  "
              f"profiles:{p.profiles}  plugins:{len(p.enabled_plugins)}{kr}")
    print()
    print("  `configurator projects <name>` for detail.")
    print()
    return 0


def _cmd_repos(rest: list[str]) -> int:
    repos = build_repos()
    if rest and not rest[0].startswith("-"):
        name = rest[0]
        repo = next((r for r in repos if r.name == name), None)
        if repo is None:
            print(f"error: unknown repo {name!r}. Known: {', '.join(r.name for r in repos)}")
            return 2
        print()
        print(f"  repo: {repo.name}{'  (project)' if repo.is_project else ''}")
        print(f"    worktree mode: {repo.worktree_mode}")
        print(f"    agent mode:    {'agent-guarded' if repo.agent else 'no-agent'}")
        print(f"    ownership:     {repo.account or '(none)'}")
        print(f"    pr model:      {repo.pr_model}")
        print(f"    remote:        {repo.remote or '?'}")
        print(f"    checkout:      {repo.path or '?'}")
        print(f"    tags:          {', '.join(repo.tags) or '(none)'}")
        print()
        return 0
    print()
    print(f"  {_BANNER} — Repos  (every known repo; * = also a project)")
    print(f"  {len(repos)} registered")
    print()
    width = max((len(r.name) for r in repos), default=0)
    for r in repos:
        star = "*" if r.is_project else " "
        agent = "agent" if r.agent else "no-agent"
        print(f"   {star}{r.name.ljust(width)}  [{r.worktree_mode}·{agent}·{r.pr_model}]"
              f"  {r.account or '-'}")
        print(f"    {' ' * width}   {r.path or '?'}")
    print()
    print("  `configurator repos <name>` for detail.")
    print()
    return 0


def _prereq_line(s) -> str:
    if not s.present:
        state = "optional, absent" if s.optional else "MISSING"
        mark = "○" if s.optional else "✗"
    elif not s.satisfied:
        state = f"{s.version or '?'} < required {s.min_required}"
        mark = "✗"
    else:
        ver = f" {s.version}" if s.version else ""
        state = f"ok{ver}"
        mark = "✓"
    return f"    {mark} {s.name.ljust(9)} {state}"


def _cmd_doctor() -> int:
    statuses = detect_baseline()
    core = core_status()
    print()
    print(f"  {_BANNER} — doctor  (os: {current_os()})")
    print()
    print("  prerequisites:")
    for s in statuses:
        print(_prereq_line(s))
    print()
    print("  agent-worktrees core:")
    print(f"    state: {core.state}")
    print(f"    runtime: {core.runtime_dir} "
          f"({'present' if core.runtime_present else 'absent'}"
          f"{', venv' if core.venv_present else ''})")
    print(f"    binstub: {core.binstub or 'not found in ~/.local/bin'}")
    print()
    gaps = missing(statuses)
    if gaps or not core.installed:
        print("  → not fully set up. Run `configurator setup` to see the plan "
              "(add --apply to execute).")
    else:
        print("  ✓ prerequisites satisfied and the core is installed.")
    print()
    return 0 if (not gaps and core.installed) else 1


def _cmd_setup(rest: list[str]) -> int:
    do_apply = "--apply" in rest
    statuses = detect_baseline()
    gaps = missing(statuses)
    actions = provision_plan(gaps)
    core = core_status()

    print()
    print(f"  {_BANNER} — setup {'(APPLY)' if do_apply else '(plan / dry-run)'}")
    print()
    if not actions:
        print("  prerequisites: all satisfied — nothing to provision.")
    else:
        print("  prerequisites to provision (in order):")
        for a in actions:
            tag = "manual" if a.manual else a.method
            print(f"    - {a.name}  [{tag}]"
                  f"{'  (restart after)' if a.changes_path else ''}")
            if a.command:
                print(f"        $ {a.command}")
            if a.note:
                print(f"        {a.note}")
    results = provision_apply(actions, dry_run=not do_apply)
    manual = [r for r in results if r.skipped_reason == "manual"]

    print()
    print("  agent-worktrees core:")
    cmd = install_command()
    if core.installed:
        print("    ✓ already installed (idempotent — nothing to do).")
    elif cmd is None:
        print("    ! the real installer needs a copilot-extensions checkout;")
        print("      run `configurator setup` from (or after adopting) a checkout.")
    else:
        print(f"    state: {core.state} → drive the harness's own installer:")
        print(f"        $ {' '.join(cmd)}")
    core_res = install_core(dry_run=not do_apply)
    if do_apply and core_res.ran:
        print(f"    installer exit code: {core_res.returncode}")

    print()
    if not do_apply:
        print("  (plan only — nothing was changed. Re-run with --apply to execute.)")
    else:
        if restart_needed(results):
            print("  ⟳ PATH changed — RESTART your shell (or open a new one) before")
            print("    continuing so the newly-installed tools are found.")
        if manual:
            names = ", ".join(r.action.name for r in manual)
            print(f"  ! manual prerequisites still needed: {names} — install them, then re-run.")
    print()
    # Non-zero while anything remains to be done.
    done = not gaps and core.installed
    return 0 if done else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"configurator {__version__}")
        return 0
    if args and args[0] in ("--help", "-h"):
        print("usage: configurator [--version] [--help] <command>")
        print()
        print("The standalone copilot-extensions installer & configurator.")
        print()
        print("commands:")
        print("  (no args)              show the app banner + build-out roadmap")
        print("  doctor                 report prerequisites + the agent-worktrees core")
        print("  setup [--apply]        plan (default) or run prereq provisioning + core install")
        print("  plugins                list the plugins the installer knows about")
        print("  plugins <name>         show one plugin's prereqs / config / steps")
        print("  plugins --prereqs      the de-duplicated union of all prerequisites")
        print("  plugins --reconcile    coverage of the discovered membership by the catalog")
        print("  plugins --status       known plugins vs. what is enabled user-global")
        print("  projects [<name>]      registered projects (harness repos: binstubs + profiles)")
        print("  repos [<name>]         every known repo + its config-state indicators")
        print()
        print("Phase 2 provisions prerequisites + drives the core install; Phase 3")
        print("adds the Manager state views (projects/repos/plugin enablement); later")
        print("phases add the visual configurator and presets (issue #352).")
        return 0
    if args and args[0] == "plugins":
        return _cmd_plugins(args[1:])
    if args and args[0] == "projects":
        return _cmd_projects(args[1:])
    if args and args[0] == "repos":
        return _cmd_repos(args[1:])
    if args and args[0] == "doctor":
        return _cmd_doctor()
    if args and args[0] == "setup":
        return _cmd_setup(args[1:])
    _print_intro()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
