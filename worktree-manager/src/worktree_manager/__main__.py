"""Entry point for the copilot-extensions Worktree Manager.

Phase 1: on top of the Phase 0 out-of-plugin skeleton, this adds the
**dependency-free plugin-knowledge model** — the installer's own declarative
catalog of each plugin's prerequisites, managed config, and "what to do" to make
it ready (see ``catalog.py`` + ``data/plugins.toml``). Later phases fill in the
real work — prerequisite provisioning, core install via the harness's own flow,
first-repo adoption + discovery, the non-agentic visual worktree-manager, and
Git-referenced presets — see the effort ``installer-configurator`` and umbrella
issue #352.

Everything here is **programmatic and non-agentic**: no AI agent is in the loop.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .catalog import load_catalog
from .core_install import core_status, install_command, install_core
from .engine_client import (
    EngineError,
    accept_inherited_engine_command,
    engine_available,
    list_worktrees,
)
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
from .self_install import self_install
from .self_install import status as self_status

_BANNER = "copilot-extensions Worktree Manager"
_TAGLINE = "the standalone, out-of-plugin harness control plane"

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
    ("4", "a non-agentic visual worktree-manager (doctor / config / validate)", ""),
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
    print("  Run `worktree-manager doctor` to check prerequisites + the core install,")
    print("  `worktree-manager setup` to plan it (add --apply to execute), and")
    print("  `worktree-manager plugins` to see what the installer knows.")
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
    print("  `worktree-manager plugins <name>` for detail · "
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
    print("  `worktree-manager projects <name>` for detail.")
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
    print("  `worktree-manager repos <name>` for detail.")
    print()
    return 0


def _worktree_row(w) -> str:
    """One rendered worktree line: id4 · machine · repo · STATE sync · title."""
    state = (w.state or "-").upper()
    dirty = " DIRTY" if w.dirty and "DIRTY" not in state else ""
    sync = f" {w.sync_tag}" if w.sync_tag else ""
    title = f"  {w.title}" if w.title else ""
    return (f"    {w.id4}  {(w.machine or '?').ljust(10)}  "
            f"{(w.repo or '?').ljust(18)}  {state}{dirty}{sync}{title}")


def _cmd_worktrees(rest: list[str]) -> int:
    """List worktrees via the engine's ``list --json`` (the process boundary).

    ``worktree-manager worktrees`` -> every project with a live worktree count;
    ``worktree-manager worktrees <project>`` -> that project's worktrees. All data
    comes from shelling out to ``agent-worktrees`` -- the Manager never imports
    the plugin. This is the read/render foundation the Textual Picker builds on.
    """
    if not engine_available():
        print()
        print("  The agent-worktrees engine is not installed yet, so there are no")
        print("  worktrees to show. Set it up with:")
        print("      $ worktree-manager setup --apply")
        print()
        return 1

    projects = build_projects()
    if rest and not rest[0].startswith("-"):
        name = rest[0]
        if name not in {p.name for p in projects}:
            print(f"error: unknown project {name!r}. "
                  f"Known: {', '.join(p.name for p in projects)}")
            return 2
        try:
            worktrees = list_worktrees(name)
        except EngineError as e:
            print(f"error: {e}")
            return 1
        print()
        print(f"  {name} — worktrees  ({len(worktrees)})")
        print()
        if not worktrees:
            print("    (none)")
        for w in worktrees:
            print(_worktree_row(w))
        print()
        print("  `worktree-manager worktrees` for all projects.")
        print()
        return 0

    # No project: a per-project summary with live counts.
    print()
    print(f"  {_BANNER} — Worktrees  (per project, live from the engine)")
    print()
    width = max((len(p.name) for p in projects), default=0)
    total = 0
    for p in projects:
        try:
            n = len(list_worktrees(p.name, classify=False))
        except EngineError:
            print(f"    {p.name.ljust(width)}  (unavailable)")
            continue
        total += n
        print(f"    {p.name.ljust(width)}  {n} worktree(s)")
    print()
    print(f"  {total} worktree(s) across {len(projects)} project(s).")
    print("  `worktree-manager worktrees <project>` to list one.")
    print()
    return 0


def _cmd_contracts(rest: list[str]) -> int:
    """Inspect plugin-contributed Manager surfaces from installed payloads."""
    from .plugin_contracts import discover_contracts

    project = None
    json_output = False
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--json":
            json_output = True
            i += 1
            continue
        if arg == "--project":
            if i + 1 >= len(rest):
                print("error: --project needs a project name")
                return 2
            project = rest[i + 1]
            i += 2
            continue
        print(f"error: unknown option {arg!r} for `contracts`.")
        return 2

    if project:
        known = {p.name for p in build_projects()}
        if project not in known:
            print(f"error: unknown project {project!r}. Known: {', '.join(sorted(known))}")
            return 2

    report = discover_contracts(project=project)
    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    scope = f"project {project}" if project else "user-global enablement"
    print()
    print(f"  {_BANNER} — plugin contribution contracts")
    print(f"  contract v{report.contract_version} · {scope}")
    print()
    if report.contributions:
        for contribution in report.contributions:
            pivot = contribution.pivot
            label = pivot.label if pivot else "(actions/config only)"
            command = " ".join(pivot.list_cmd) if pivot else "—"
            available = "ready" if contribution.command_available else "command missing"
            print(f"    {contribution.qualified_plugin}")
            print(f"      {label} · {available}")
            print(f"      list: {command}")
            print(f"      source: {contribution.source_path}")
    else:
        print("    (no enabled plugin contributions)")
    if report.findings:
        print()
        print("  findings:")
        for finding in report.findings:
            owner = (
                f"{finding.plugin}@{finding.marketplace}"
                if finding.plugin else "(registry)"
            )
            print(f"    {finding.severity}: {finding.code} · {owner}")
            print(f"      {finding.detail}")
    print()
    print("  Add `--json` for the machine-readable report.")
    print()
    return 0


def _cmd_picker(rest: list[str]) -> int:
    """Run, mock, or capture the Manager-owned production Picker."""
    args = list(rest)
    action = "run"
    if args and args[0] in {"mock", "screenshot"}:
        action = args.pop(0)

    values: dict[str, str] = {}
    flags: set[str] = set()
    positionals: list[str] = []
    value_options = {"--screenshot", "--out", "--format", "--pivot", "--wait"}
    flag_options = {"--demo", "--local", "--live", "--json"}
    index = 0
    while index < len(args):
        token = args[index]
        if token in value_options:
            if index + 1 >= len(args):
                print(f"error: {token} needs a value")
                return 2
            values[token] = args[index + 1]
            index += 2
            continue
        if token in flag_options:
            flags.add(token)
            index += 1
            continue
        if token.startswith("-"):
            print(f"error: unknown picker option: {token}")
            return 2
        positionals.append(token)
        index += 1

    if len(positionals) > 1:
        print("error: picker accepts at most one project name")
        return 2

    demo_mode = "--demo" in flags
    legacy_screenshot = values.get("--screenshot")
    if legacy_screenshot:
        action = "screenshot"
    screenshot_out = values.get("--out") or legacy_screenshot
    if screenshot_out:
        screenshot_out = str(Path(screenshot_out).resolve())
    capture_format = values.get("--format", "svg")
    if capture_format not in {"svg", "text", "ansi"}:
        print("error: --format must be svg, text, or ansi")
        return 2
    try:
        wait_pivot = float(values.get("--wait", "0"))
    except ValueError:
        print("error: --wait must be a number")
        return 2

    if demo_mode:
        from . import picker_app
        from .demo import DEMO_PROJECT
        project = DEMO_PROJECT
        source = picker_app.demo_source()
        subtitle = f"{project} · demo (Aperture Labs)"
        on_launch = _demo_launch_preview
        contributions = ()
        context_source = None
    else:
        if action == "run" and not engine_available():
            print()
            print("  The agent-worktrees engine is not installed. Try `--demo` for a")
            print("  mock preview, or `worktree-manager setup --apply` to install it.")
            print()
            return 1
        projects = build_projects()
        project = positionals[0] if positionals else (
            projects[0].name if projects else "")
        if not project:
            print("error: no project to open. Adopt one, or pass a project name.")
            return 2
        if projects and project not in {p.name for p in projects}:
            print(f"error: unknown project {project!r}. "
                  f"Known: {', '.join(p.name for p in projects)}")
            return 2
        if action == "run":
            return _run_production_picker(project)

        from .production_picker import runner

        if action == "mock":
            try:
                decision = runner.run(
                    project,
                    mock_mode=True,
                    local="--local" in flags,
                )
            except Exception as error:
                print(f"error: production Picker mock failed: {error}")
                return 1
            if "--json" in flags:
                print(json.dumps({"mock": True, "decision": decision}))
            else:
                print(f"mock picker exited - decision: {decision!r}")
            return 0

        try:
            captures = runner.capture(
                project,
                live="--live" in flags,
                pivot=values.get("--pivot"),
                wait_pivot=wait_pivot,
            )
        except Exception as error:
            print(f"error: production Picker capture failed: {error}")
            return 1
        content = captures[capture_format]
        if screenshot_out:
            with open(screenshot_out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            if "--json" in flags:
                print(json.dumps({
                    "screenshot": screenshot_out,
                    "format": capture_format,
                    "bytes": len(content),
                }))
            else:
                print(
                    f"  wrote {capture_format} screenshot: {screenshot_out} "
                    f"({len(content)} bytes)"
                )
        else:
            sys.stdout.write(content)
            if not content.endswith("\n"):
                sys.stdout.write("\n")
        return 0

    if action == "screenshot":
        svg = picker_app.capture_svg(
            source,
            project=project,
            subtitle=subtitle,
            contributions=contributions,
            context_source=context_source,
        )
        if screenshot_out:
            with open(screenshot_out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(svg)
            print(f"  wrote screenshot: {screenshot_out}")
        else:
            sys.stdout.write(svg)
            if not svg.endswith("\n"):
                sys.stdout.write("\n")
        return 0
    return picker_app.run_picker(
        source,
        project=project,
        subtitle=subtitle,
        on_launch=on_launch,
        contributions=contributions,
        context_source=context_source,
    )


def _run_production_picker(project: str) -> int:
    """Run the transplanted production UX and act on its launch decision."""
    from . import picker_app
    from .production_picker import runner

    try:
        decision = runner.run(project)
    except Exception as error:
        print(f"error: production Picker failed: {error}")
        return 1
    if not decision:
        return 0

    action = str(decision.get("action") or "")
    options = decision.get("options")
    opts = dict(options) if isinstance(options, dict) else {}
    if action == "resume":
        worktree_id = decision.get("worktree_id")
        if not worktree_id:
            print("error: Picker returned a resume decision with no worktree id.")
            return 1
        return _run_launch(picker_app.LaunchRequest(
            project=project,
            worktree_id=str(worktree_id),
            mode="bare-resume" if opts.get("bare_resume") else "resume",
            title=str(decision.get("title") or "") or None,
            no_mux=bool(opts.get("no_mux")),
            machine=(
                None if decision.get("is_local", True)
                else str(decision.get("machine") or "") or None
            ),
            environment=(
                None if decision.get("is_local", True)
                else str(decision.get("env") or "") or None
            ),
        ))
    if action == "restore":
        worktree_id = decision.get("worktree_id")
        if not worktree_id:
            print("error: Picker returned a restore decision with no worktree id.")
            return 1
        return _restore_production_picker_session(
            project,
            str(worktree_id),
            title=str(decision.get("title") or "") or None,
            is_local=bool(decision.get("is_local", True)),
            machine=str(decision.get("machine") or "") or None,
            environment=str(decision.get("env") or "") or None,
        )
    if action == "new":
        return _run_launch(picker_app.LaunchRequest(
            project=project,
            worktree_id=None,
            mode="base" if opts.get("anchor") else "new",
            no_mux=bool(opts.get("no_mux")),
            machine=(
                None if decision.get("is_local", True)
                else str(decision.get("machine") or "") or None
            ),
            environment=(
                None if decision.get("is_local", True)
                else str(decision.get("env") or "") or None
            ),
        ))
    if action == "refresh":
        return _cmd_update([])
    print(f"error: Picker returned an unsupported decision: {action!r}")
    return 1


def _restore_production_picker_session(
    project: str,
    worktree_id: str,
    *,
    title: str | None,
    is_local: bool,
    machine: str | None,
    environment: str | None,
) -> int:
    """Run platform remux prep, then immediately launch/attach through mux."""
    from . import engine_client, picker_app

    try:
        if is_local:
            # Resolve the interactive launch boundary before terminating the
            # unreachable owner. A missing binstub must fail without mutation.
            engine_client.project_binstub_command(project)
            result = engine_client.run_json(
                project,
                [
                    "remux",
                    "--worktree-id",
                    worktree_id,
                    "--yes",
                    "--json",
                ],
                allow_nonzero=True,
            )
        else:
            from .production_picker.picker_tui import data_ssh, maintenance

            argv = data_ssh.remote_op_argv(
                machine,
                environment,
                "remux",
                worktree_id,
            )
            if argv is None:
                print("error: the selected source does not support session restore.")
                return 1
            result = maintenance._ssh_json(argv)
    except engine_client.EngineError as error:
        print(f"error: could not prepare session restore: {error}")
        return 1

    if not result.get("ok"):
        print(
            "error: could not prepare session restore: "
            f"{result.get('reason') or 'unknown engine error'}"
        )
        return 1

    request = picker_app.LaunchRequest(
        project=project,
        worktree_id=worktree_id,
        mode="resume",
        title=title,
        no_mux=False,
        machine=None if is_local else machine,
        environment=None if is_local else environment,
    )
    if not is_local:
        return _run_launch(request)

    try:
        return engine_client.run_project_passthrough(
            project, ["--worktree-id", worktree_id]
        )
    except engine_client.EngineError as error:
        print(f"error: could not launch restored session: {error}")
        return 1


def _resolve_for(req) -> "tuple[object | None, int]":
    """Resolve a picker :class:`LaunchRequest` to a launch plan (shared).

    Returns ``(plan, 0)`` on success, else ``(None, exit_code)`` after printing
    the engine error. The whole thing goes through the process boundary --
    ``engine_client.resolve_launch_plan`` shells to ``agent-worktrees resolve
    --json`` and never imports the plugin.
    """
    from .engine_client import (
        EngineError,
        EngineFeatureUnavailable,
        launch_plan_from_dict,
        resolve_launch_plan,
    )
    try:
        plan = resolve_launch_plan(
            req.project, worktree_id=req.worktree_id,
            new=(req.mode == "new"), bare_resume=(req.mode == "bare-resume"),
            base=(req.mode == "base"),
            target_machine=getattr(req, "machine", None),
            target_environment=getattr(req, "environment", None),
            target_no_mux=getattr(req, "no_mux", False),
        )
    except EngineFeatureUnavailable:
        from .production_picker import runner

        try:
            plan = launch_plan_from_dict(runner.compatibility_remote_plan(
                req.project,
                machine=req.machine,
                environment=getattr(req, "environment", None),
                worktree_id=req.worktree_id,
                mode=req.mode,
                no_mux=getattr(req, "no_mux", False),
            ))
        except (RuntimeError, OSError, ValueError) as error:
            print(f"error: could not resolve a remote launch plan: {error}")
            return None, 1
    except EngineError as e:
        print(f"error: could not resolve a launch plan: {e}")
        return None, 1
    return plan, 0


def _run_launch(req) -> int:
    """Resolve + execute the operator's launch/resume (real engine)."""
    from . import launcher
    plan, code = _resolve_for(req)
    if plan is None:
        return code
    if plan.action == "none":
        return plan.exit_code
    return launcher.launch(plan, want_mux=not getattr(req, "no_mux", False))


def _demo_launch_preview(req) -> int:
    """Show what the launch/resume *would* run, without starting anything.

    Exercises the same resolve -> compose path (through the fake Aperture engine)
    the real launch uses, then prints the composed argv instead of executing it --
    so the demo never spawns a Copilot session.
    """
    from . import launcher
    plan, code = _resolve_for(req)
    if plan is None:
        return code
    le = launcher.compose_launch(plan)
    print()
    target = req.worktree_id or "a new worktree"
    print(f"  demo launch ({req.mode}) — {target}")
    print(f"    action: {plan.action}   muxed: {le.muxed}   cwd: {le.cwd}")
    print(f"    argv:   {' '.join(le.argv)}")
    print("  (demo mode — not executed)")
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
    selfst = self_status()
    print("  worktree-manager (self):")
    print(f"    installed version: {selfst.installed_version or '(not versioned-installed)'}")
    print(f"    running version:   {__version__}")
    print(f"    binstub: {selfst.binstub or 'not found in ~/.local/bin'}")
    print(f"    root: {selfst.root}")
    from . import source_config as _sc
    _cfg_repo, _cfg_ref = _sc.configured_source()
    print(f"    update source: {_sc.resolved_repo()} @ {_sc.resolved_ref()}"
          f"{' (default)' if not (_cfg_repo or _cfg_ref) else ' (configured)'}")
    print()
    gaps = missing(statuses)
    if gaps or not core.installed:
        print("  → not fully set up. Run `worktree-manager setup` to see the plan "
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
        print("      run `worktree-manager setup` from (or after adopting) a checkout.")
    else:
        print(f"    state: {core.state} → drive the harness's own installer:")
        print(f"        $ {' '.join(cmd)}")
    core_res = install_core(dry_run=not do_apply)
    if do_apply and core_res.ran:
        print(f"    installer exit code: {core_res.returncode}")
        # Refresh: the `core` snapshot above predates the install we just ran, so
        # basing the summary/exit code on it would report failure on a successful
        # first-time install (it would only "pass" on a second run).
        core = core_status()
        if core.installed:
            print("    ✓ core install complete.")
        else:
            print(f"    ! core still {core.state} after install — see output above.")

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


def _cmd_self_install(rest: list[str]) -> int:
    do_apply = "--apply" in rest
    st = self_status()
    res = self_install(dry_run=not do_apply)
    print()
    print(f"  {_BANNER} — self-install {'(APPLY)' if do_apply else '(plan / dry-run)'}")
    print()
    print(f"    payload version:   {res.version or '?'}")
    print(f"    installed version: {st.installed_version or '(none)'}")
    print(f"    root:              {res.root}")
    print(f"    version slot:      {res.slot or '?'}")
    print(f"    marker file:       {res.root}/current-version")
    print(f"    binstub dir:       {local_bin_hint()}")
    print()
    if res.action == "already-current":
        print("  ✓ already current — nothing to do (version-gated no-op).")
    elif res.action == "planned":
        print("  would install this version's slot, publish the current-version")
        print("  marker, and deploy the ~/.local/bin/worktree-manager binstub.")
        print("  (plan only — re-run with --apply to execute.)")
    elif res.action == "installed":
        print(f"  ✓ installed {res.version}: slot + marker written, binstub deployed:")
        for b in res.binstubs:
            print(f"      {b}")
        print("  ensure ~/.local/bin is on PATH, then run `worktree-manager` directly.")
    else:
        print(f"  ! {res.reason}")
        return 1
    print()
    return 0


def local_bin_hint() -> str:
    from .self_install import local_bin
    return str(local_bin())


def _flag_value(rest: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` (None if absent or followed by a flag)."""
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
            return rest[i + 1]
    return None


def _cmd_source(rest: list[str]) -> int:
    """Show or manage the self-update **source** (git repo + ref/branch).

    The source override is a user-level config file
    (``~/.worktree-manager/config.toml`` ``[source]``) — not an env var — so the
    updater can durably track a **fork** or a **canary branch** for future
    updates. ``set`` persists an override; ``reset`` clears it.
    """
    from . import source_config as sc

    action = rest[0] if rest else "show"

    if action == "set":
        repo = _flag_value(rest, "--repo")
        ref = _flag_value(rest, "--ref")
        if repo is None and ref is None:
            print("usage: worktree-manager source set [--repo URL] [--ref BRANCH]")
            print("  (provide at least one of --repo / --ref)")
            return 2
        sc.set_source(repo=repo, ref=ref)
    elif action == "reset":
        # --repo / --ref clear just that field; neither clears both.
        sc.reset_source(repo="--repo" in rest, ref="--ref" in rest)
    elif action in ("show", "--help", "-h"):
        pass
    else:
        print(f"unknown source subcommand: {action!r}")
        print("usage: worktree-manager source [show | set [--repo URL] [--ref BRANCH] | reset [--repo] [--ref]]")
        return 2

    cfg_repo, cfg_ref = sc.configured_source()
    print()
    print(f"  {_BANNER} — source")
    print()
    print(f"    repo:  {sc.resolved_repo()}"
          f"{'' if cfg_repo else '  (default)'}")
    print(f"    ref:   {sc.resolved_ref()}"
          f"{'' if cfg_ref else '  (default)'}")
    print(f"    config: {sc.config_path()}"
          f"{'' if (cfg_repo or cfg_ref) else '  (none — using defaults)'}")
    print()
    if not (cfg_repo or cfg_ref):
        print("  Track a fork / canary branch for future self-updates, e.g.:")
        print("    worktree-manager source set --repo <fork-url> --ref canary")
        print("  Clear with `worktree-manager source reset`.")
        print()
    return 0


def _strip_project(rest: list[str]) -> list[str]:
    """Drop a threaded ``--project <name>`` (update is harness-wide, not scoped)."""
    out: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--project":
            i += 2
            continue
        out.append(rest[i])
        i += 1
    return out


def _cmd_update(rest: list[str]) -> int:
    """Update the harness — the Worktree Manager AS the plugin updater/aligner.

    Where ``<project> update`` lands after the agent-worktrees seam hands off
    (its DQ8 fallback runs the in-plugin update directly). Two steps:

    1. **Self-update the Manager** to the latest out-of-band payload (git fetch →
       versioned slot); best-effort + non-fatal, effective on the next run.
    2. **Orchestrate the harness update** by driving the engine's own mechanics
       via ``agent-worktrees update --no-manager`` (the seam bypass, so this does
       not recurse) — refreshing every plugin payload + runtime, reconciling
       binstubs, and syncing anchors. The Manager sequences + aligns; the plugin
       still does the work. Forwarded flags (``--force`` …) are passed through.
    """
    from . import engine_client as ec
    from .self_install import self_update

    print()
    print(f"  {_BANNER} — update")
    print()

    # 1. Self-update the Manager (best-effort; the new slot takes effect next run).
    print("  Self-updating the Worktree Manager …")
    su = self_update(dry_run=False)
    if su.action == "updated":
        print(f"    ✓ updated {su.previous or '(none)'} → {su.version} "
              "(active on next run)")
    elif su.action == "already-current":
        print(f"    ✓ already current ({su.version})")
    else:
        print(f"    ○ self-update {su.action}: {su.reason} — continuing")

    # 2. Orchestrate the harness/plugin update through the engine, bypassing the
    #    seam (so we do not recurse back into the Manager).
    forwarded = _strip_project(rest)
    print()
    print("  Updating harness plugins + runtimes via agent-worktrees …")
    print()
    try:
        return ec.run_engine_passthrough(None, ["update", "--no-manager", *forwarded])
    except ec.EngineError as e:
        print(f"  ✗ {e}")
        if getattr(e, "install_hint", False):
            print("    Run `worktree-manager setup --apply` to install the engine first.")
        return 1


def main(argv: list[str] | None = None) -> int:
    inherited_warning = accept_inherited_engine_command()
    if inherited_warning:
        print(f"worktree-manager: ignoring invalid provider handoff: "
              f"{inherited_warning}", file=sys.stderr)
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"worktree-manager {__version__}")
        return 0
    if args and args[0] in ("--help", "-h"):
        print("usage: worktree-manager [--version] [--help] <command>")
        print()
        print("The standalone copilot-extensions harness control plane (installer, configurator & worktree launcher).")
        print()
        print("commands:")
        print("  (no args)              show the app banner + build-out roadmap")
        print("  --project NAME         launch NAME's interactive Picker (binstub seam)")
        print("  doctor                 report prerequisites + the agent-worktrees core")
        print("  setup [--apply]        plan (default) or run prereq provisioning + core install")
        print("  self-install [--apply] version the app: current-version marker + ~/.local/bin binstub")
        print("  source                 show the self-update source (git repo + ref/branch)")
        print("  source set [--repo URL] [--ref BRANCH]   track a fork / canary branch for updates")
        print("  source reset [--repo] [--ref]            clear the source override(s)")
        print("  update                 self-update the Manager, then update the harness plugins/runtimes")
        print("  plugins                list the plugins the installer knows about")
        print("  plugins <name>         show one plugin's prereqs / config / steps")
        print("  plugins --prereqs      the de-duplicated union of all prerequisites")
        print("  plugins --reconcile    coverage of the discovered membership by the catalog")
        print("  plugins --status       known plugins vs. what is enabled user-global")
        print("  projects [<name>]      registered projects (harness repos: binstubs + profiles)")
        print("  repos [<name>]         every known repo + its config-state indicators")
        print("  worktrees [<project>]  live worktrees via the agent-worktrees engine (--json)")
        print("  contracts [--project NAME] [--json]")
        print("                         validate plugin-contributed pivots/actions/cards/config")
        print("  picker [<project>]     launch the production Picker (Textual)")
        print("  picker mock [<project>] [--local] [--json]")
        print("                         production UX with simulated mutations")
        print("  picker screenshot [<project>] [--format svg|text|ansi] [--out F]")
        print("                         capture the production Picker headlessly")
        print("  picker --demo          preview the retired minimal scaffold")
        print("                         (in the Picker: l launch/resume · b bare-resume · n new)")
        print()
        print("Phase 2 provisions prerequisites + drives the core install; Phase 3")
        print("adds the Manager state views (projects/repos/plugin enablement); later")
        print("phases add the visual worktree-manager and presets (issue #352).")
        return 0
    if args and args[0] == "plugins":
        return _cmd_plugins(args[1:])
    if args and args[0] == "projects":
        return _cmd_projects(args[1:])
    if args and args[0] == "repos":
        return _cmd_repos(args[1:])
    if args and args[0] == "worktrees":
        return _cmd_worktrees(args[1:])
    if args and args[0] == "contracts":
        return _cmd_contracts(args[1:])
    if args and args[0] == "picker":
        return _cmd_picker(args[1:])
    if args and args[0] == "doctor":
        return _cmd_doctor()
    if args and args[0] == "setup":
        return _cmd_setup(args[1:])
    if args and args[0] == "self-install":
        return _cmd_self_install(args[1:])
    if args and args[0] == "source":
        return _cmd_source(args[1:])
    if args and args[0] == "update":
        return _cmd_update(args[1:])
    if args and args[0] == "--project":
        if len(args) != 2 or not args[1]:
            print("error: --project needs exactly one project name")
            return 2
        return _cmd_picker([args[1]])
    _print_intro()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
