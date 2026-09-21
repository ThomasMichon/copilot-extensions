"""Related-repository CLI dispatch extracted from ``__main__``."""

from __future__ import annotations

import os
import subprocess

from . import config as cfg
from . import output, state_root as state_root_mod


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    sub.add_parser("related", help="Per-project related repos (run 'related' for usage)")


def _related_usage() -> None:
    """Print related subcommand usage."""
    project = cfg.active_project() or "agent-worktrees"
    print(f"Usage: {project} related <command> | related --conduct")
    print()
    print("Per-project, directional 'related repos' index (this repo's POV),")
    print("committed at <repo>/.copilot-extensions/agent-worktrees/related.yaml.")
    print("Legacy <repo>/.agent-worktrees/related.yaml remains readable. Keys reference the")
    print("global repos registry; entries add role + locus + delegate + a narrative.")
    print()
    print("Commands:")
    print("  --conduct                             Emit merged session guidance")
    print("  list [--role R] [--json]            List related repos (and the primary)")
    print("       [--require-managed]             Fail outside an adopted project")
    print("  show <name> [--json]                Show a related repo (+ registry context)")
    print("  add <name>                          Link a related repo + scaffold its doc")
    print("     [--role R] [--summary S] [--doc PATH] [--delegate D]")
    print("     [--ownership owned|internal|external] [--owner ACCOUNT]")
    print("     [--locus L] [--machines a,b] [--primary] [--no-scaffold]")
    print("     [--cs-repo R] [--cs-machine M] [--cs-location L]")
    print("     [--cs-workspace DIR]                                (codespace locus)")
    print("     [--container-repo R] [--container-workspace DIR]")
    print("     [--container-machines a,b]                          (container locus)")
    print("  remove <name>                       Unlink (leaves the narrative doc)")
    print("  doc <name>                          Print (scaffold if missing) the narrative")
    print("  doctor [--json]                     Validate entries (repos exist, machines")
    print("                                      + venues valid, local checkouts registered)")
    print("  primary [<name>]                    Show or set the primary related repo")
    print("  resolve [<name>]                    How to work on it from here (locus plan)")
    print("  classify [<name>|--all] [--overwrite]   Derive ownership from gh accounts +")
    print("                                      remote and persist (unset entries only)")
    print(
        "  owners [--json]                     List wholly-owned targets "
        "(ownership=owned) from the control-plane index"
    )
    print()
    print("Any command takes [--repo PATH] to target a specific checkout")
    print("(default: the git repo containing the current directory).")
    print()
    print("Locus (where work happens): local | machine:<key> | codespace | container")
    print("Delegate (how to hand off): agent-bridge | agent-codespaces | agent-containers | none")
    print(
        "Ownership (attribution posture): owned | internal | external "
        "(derived once at registration, then authoritative)"
    )


def _related_opt(rest: list[str], flag: str, default: str | None = None) -> str | None:
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            return rest[i + 1]
    return default


def _related_anchor(rest: list[str]) -> str | None:
    explicit = _related_opt(rest, "--repo")
    if explicit:
        return explicit
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if cp.returncode == 0 and cp.stdout.strip():
            return cp.stdout.strip()
    except Exception:
        pass
    try:
        return cfg.load_config().default_repo.anchor
    except Exception:
        return None


def _related_current_machine(anchors: list[str], base_anchor: str) -> str:
    for candidate in [base_anchor, *anchors]:
        if not candidate:
            continue
        try:
            if cfg.machines_yaml_path(candidate).exists():
                return cfg.detect_machine(candidate)
        except Exception:
            continue
    try:
        return cfg.detect_machine(base_anchor)
    except Exception:
        return ""


def _related_config_source_anchors(
    base_anchor: str,
    *,
    installed_anchors: list[str] | None = None,
) -> list[str]:
    from . import related as _related_mod

    try:
        srcs = state_root_mod.config_source_anchors(
            cfg.load_config(include_control_plane_related_pr=False),
            base_anchor=base_anchor,
        )
        anchors = [
            _related_mod.config_contribution_anchor(s.anchor, s.origin) for s in srcs if s.anchor
        ]
    except Exception:
        anchors = []
    if not anchors:
        anchors = [_related_mod.config_contribution_anchor(base_anchor, "harness")]
    elif os.path.abspath(anchors[0]) != os.path.abspath(base_anchor):
        anchors.insert(0, _related_mod.config_contribution_anchor(base_anchor, "harness"))
    try:
        existing = {_related_mod._anchor_key(a) for a in anchors}
        discovered = (
            _related_mod.installed_plugin_related_anchors()
            if installed_anchors is None
            else installed_anchors
        )
        plugin_anchors = [p for p in discovered if _related_mod._anchor_key(p) not in existing]
    except Exception:
        return anchors
    return [*plugin_anchors, *anchors]


def _related_lookup_anchors(
    rest: list[str],
    anchor: str,
    name: str,
) -> tuple[list[str], bool]:
    from . import related

    anchors = _core()._related_config_source_anchors(anchor)
    if _related_opt(rest, "--repo"):
        return anchors, False
    if related.get_related_grafted(anchors, name) is not None:
        return anchors, False
    cp = related.find_control_plane_anchor()
    if cp and os.path.abspath(cp) != os.path.abspath(anchor):
        cp_anchors = _core()._related_config_source_anchors(cp)
        if related.get_related_grafted(cp_anchors, name) is not None:
            return cp_anchors, True
    return anchors, False


def _hunt_checkout(name: str) -> str | None:
    try:
        from . import repos as _repos

        registry = _repos.read_registry()
    except Exception:
        return None
    plat = cfg.detect_platform()
    roots = []
    try:
        root = registry.srcroot.get(plat) if hasattr(registry, "srcroot") else None
        if root:
            roots.append(root)
    except Exception:
        pass
    candidates = []
    for root in roots:
        try:
            from pathlib import Path

            root_path = Path(root)
            if not root_path.is_dir():
                continue
            for child in root_path.iterdir():
                if child.name.lower() == name.lower() and (child / ".git").exists():
                    candidates.append(child)
        except Exception:
            continue
    seen = set()
    uniq = []
    for c in candidates:
        key = os.path.normcase(str(c.resolve()))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return str(uniq[0]) if len(uniq) == 1 else None


def _related_doctor(anchor: str, rest: list[str], json_out: bool) -> int:
    from . import related, repos

    anchors = _core()._related_config_source_anchors(anchor)
    rc = related.read_related_grafted(anchors)

    current_machine = _core()._related_current_machine(anchors, anchor)

    machines_known_available = True
    machine_entries: dict = {}
    try:
        machine_entries = cfg.load_machines_yaml(anchor)
    except Exception:
        machines_known_available = False

    def _machine_known(key: str) -> bool:
        if not machines_known_available:
            return True
        return cfg.find_machine_entry(machine_entries, key) is not None

    def _registry_has(name: str) -> bool:
        return repos.find_repo(name) is not None

    def _registry_remote(name: str) -> str:
        e = repos.find_repo(name)
        return (e.remote if e else "") or ""

    findings = related.diagnose_related(
        rc,
        current_machine=current_machine,
        machine_known=_machine_known,
        machines_known_available=machines_known_available,
        registry_has=_registry_has,
        registry_remote=_registry_remote,
    )

    for f in findings:
        if f.kind == "local_repo_unregistered":
            found = _hunt_checkout(f.name)
            if found:
                f.candidate_path = found
                f.suggested_actions.insert(
                    0,
                    f"register the checkout found here: `repos add {f.name} "
                    f"{found} --class <class>`",
                )

    if json_out:
        _core()._json_output(
            {
                "current_machine": current_machine,
                "machines_yaml_available": machines_known_available,
                "findings": [
                    {
                        "name": f.name,
                        "kind": f.kind,
                        "severity": f.severity,
                        "detail": f.detail,
                        "suggested_actions": f.suggested_actions,
                        "candidate_path": f.candidate_path,
                    }
                    for f in findings
                ],
            }
        )
    else:
        _render_related_findings(findings, current_machine)

    has_error = any(f.severity == related.SEV_ERROR for f in findings)
    return 1 if has_error else 0


def _render_related_findings(findings: list, current_machine: str) -> None:
    from . import related

    output.header(f"related doctor  (machine: {current_machine or '?'})")
    if not findings:
        output.ok("All related entries validate: repos exist, machines and venues are valid.")
        return
    order = {related.SEV_ERROR: 0, related.SEV_WARNING: 1, related.SEV_INFO: 2}
    icon = {related.SEV_ERROR: "✗", related.SEV_WARNING: "⚠️ ", related.SEV_INFO: "•"}
    for f in sorted(findings, key=lambda x: (order.get(x.severity, 9), x.name)):
        print(f"  {icon.get(f.severity, '-')} [{f.kind}] {f.detail}")
        if f.candidate_path:
            print(f"      found checkout: {f.candidate_path}")
        for a in f.suggested_actions:
            print(f"      - {a}")
    print()
    errs = sum(1 for f in findings if f.severity == related.SEV_ERROR)
    warns = sum(1 for f in findings if f.severity == related.SEV_WARNING)
    infos = sum(1 for f in findings if f.severity == related.SEV_INFO)
    print(f"  {errs} error(s), {warns} warning(s), {infos} info.")
    if warns or errs:
        output.info(
            "Report-only: `related doctor` never edits related.yaml. "
            "Resolve each with the user (locate / provide URL / clone / "
            "register), and remove an entry only with their approval."
        )


def _related_conduct(anchor: str) -> int:
    from . import related

    try:
        config = cfg.load_config(include_control_plane_related_pr=False)
    except Exception:
        return 0

    anchors = _core()._related_config_source_anchors(anchor)
    rel = related.read_related_grafted(anchors)
    related_count = len(rel.related)
    if not config.repos and not related_count:
        return 0

    lines = [
        "## Related-repository guidance",
        "",
        "- Before touching another repo, run "
        "`agent-worktrees related resolve <repo>`; follow its class, locus, "
        "and delegate.",
        "- A non-`none` delegate means hand content work to the owning agent; "
        "do not edit that target from this session.",
        "- CodeSpace/container venue payloads come from the dispatch host's "
        "installed plugins via `--plugin-dir`; ensure listed sources are "
        "installed there.",
        "- Registered repositories are discovered through the active project's "
        "repository tooling.",
        "- Inspect with `agent-worktrees related show <repo>`; plan with "
        "`agent-worktrees related resolve <repo>`.",
        "- After index changes, run `agent-worktrees related doctor`.",
    ]
    if related_count:
        lines.insert(
            -2,
            f"- {related_count} directional related entries: `agent-worktrees related list`.",
        )
    print("\n".join(lines))
    return 0


def cmd_related_dispatch(argv: list[str]) -> int:
    """Route related subcommands (per-project related-repos index)."""
    from . import related, repos

    if not argv or argv[0] in ("--help", "-h"):
        _related_usage()
        return 0 if argv else 1

    sub = argv[0]
    rest = argv[1:]
    if "--help" in rest or "-h" in rest:
        _related_usage()
        return 0

    if sub == "owners":
        json_out = "--json" in rest
        try:
            cp = related.find_control_plane_anchor()
        except Exception:
            cp = None
        base = cp or _core()._related_anchor(rest)
        owners = (
            related.owned_targets_grafted(_core()._related_config_source_anchors(base))
            if base
            else []
        )
        if json_out:
            _core()._json_output(
                {"owned": owners, "count": len(owners), "source": "control-plane" if cp else "cwd"}
            )
        elif not owners:
            print("No wholly-owned related targets (ownership=owned).")
        else:
            output.header("Wholly-owned targets")
            for t in owners:
                print(f"  {t['name']:<24} {t['slug'] or t['remote'] or '-'}")
        return 0

    if not cfg.active_project():
        explicit = _related_opt(rest, "--repo")
        if explicit:
            _core()._activate_project_for_path(explicit)
        else:
            project, _assumed = _core()._resolve_active_project(None)
            if project:
                cfg.set_active_project(project)
    if "--require-managed" in rest and not cfg.active_project():
        output.err("The current repo is not an adopted agent-worktrees project.")
        return 1

    anchor = _core()._related_anchor(rest)
    if not anchor:
        output.err("Could not resolve the current repo. Run inside a repo, or pass --repo <path>.")
        return 1

    if sub == "--conduct":
        return _related_conduct(anchor)

    json_out = "--json" in rest

    if sub == "list":
        role = _related_opt(rest, "--role")
        anchors = _core()._related_config_source_anchors(anchor)
        entries = related.list_related_grafted(anchors, role=role)
        primary = related.get_primary_grafted(anchors)
        if json_out:
            _core()._json_output(
                {
                    "primary": primary,
                    "related": [
                        {
                            "name": e.name,
                            "role": e.role,
                            "summary": e.summary,
                            "doc": related.public_doc(e),
                            "delegate": e.delegate,
                            "ownership": related.effective_ownership(e),
                            "owner": e.owner,
                            "provenance": related.entry_provenance(e),
                            "locus": {
                                "preferred": e.locus.preferred,
                                "machines": e.locus.machines,
                                "codespace": e.locus.codespace,
                                "container": e.locus.container,
                            },
                        }
                        for e in entries
                    ],
                }
            )
        elif not entries:
            print("No related repos linked.")
            print(f"Link one with: {cfg.active_project() or 'agent-worktrees'} related add <name>")
        else:
            output.header("Related Repos")
            for e in entries:
                star = "  *primary" if e.name == primary else ""
                loc = e.locus.preferred or "-"
                print(f"  {e.name:<24} {e.role or '-':<11} locus={loc}{star}")
        return 0

    if sub == "doctor":
        return _related_doctor(anchor, rest, json_out)

    if sub == "show":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related show <name>")
            return 1
        name = rest[0]
        anchors, _via_cp = _core()._related_lookup_anchors(rest, anchor, name)
        e = related.get_related_grafted(anchors, name)
        if e is None:
            output.err(f"'{name}' is not a related repo.")
            return 1
        reg = repos.find_repo(name)
        if json_out:
            _core()._json_output(
                {
                    "name": e.name,
                    "role": e.role,
                    "summary": e.summary,
                    "doc": related.public_doc(e),
                    "delegate": e.delegate,
                    "ownership": related.effective_ownership(e),
                    "ownership_explicit": e.ownership,
                    "owner": e.owner,
                    "provenance": related.entry_provenance(e),
                    "locus": {
                        "preferred": e.locus.preferred,
                        "machines": e.locus.machines,
                        "codespace": e.locus.codespace,
                        "container": e.locus.container,
                    },
                    "registry": None
                    if reg is None
                    else {
                        "class": reg.repo_class,
                        "remote": reg.remote,
                        "path": reg.local_path(),
                    },
                }
            )
            return 0
        output.header(f"Related: {e.name}")
        print(f"  role:     {e.role or '-'}")
        print(f"  summary:  {e.summary or '-'}")
        _own = related.effective_ownership(e)
        if _own:
            _osrc = "explicit" if e.ownership else "derived"
            print(f"  ownership: {_own} ({_osrc})" + (f"  owner={e.owner}" if e.owner else ""))
        print(
            f"  locus:    {e.locus.preferred or '-'}"
            + (f"  machines={e.locus.machines}" if e.locus.machines else "")
            + (f"  codespace={e.locus.codespace}" if e.locus.codespace else "")
            + (f"  container={e.locus.container}" if e.locus.container else "")
        )
        print(f"  delegate: {e.delegate or '-'}")
        provenance = related.entry_provenance(e)
        source = provenance["layer"]
        if provenance.get("plugin"):
            source += f" ({provenance['plugin']})"
        print(f"  provenance: {source}")
        doc = (
            related.public_doc(e)
            if provenance["layer"] == "plugin"
            else str(related.doc_abs_path(anchor, e))
        )
        print(f"  doc:      {doc}")
        if reg is None:
            output.warn(
                f"'{name}' is not in the repos registry "
                f"(add it with: repos add {name} <path> --class <class>)"
            )
        else:
            print(f"  registry: [{reg.repo_class}] {reg.local_path() or '(no local path)'}")
            if reg.remote:
                print(f"            {reg.remote}")
        return 0

    if sub == "add":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related add <name> [--role ...] [--locus ...] ...")
            return 1
        name = rest[0]
        machines_csv = _related_opt(rest, "--machines", "") or ""
        machines = [m.strip() for m in machines_csv.split(",") if m.strip()]
        codespace: dict = {}
        for flag, key in (
            ("--cs-repo", "repo"),
            ("--cs-machine", "machine"),
            ("--cs-location", "location"),
            ("--cs-workspace", "workspace_folder"),
        ):
            v = _related_opt(rest, flag)
            if v:
                codespace[key] = v
        container: dict = {}
        for flag, key in (
            ("--container-repo", "repo"),
            ("--container-workspace", "workspace_folder"),
        ):
            v = _related_opt(rest, flag)
            if v:
                container[key] = v
        ct_machines_csv = _related_opt(rest, "--container-machines", "") or ""
        ct_machines = [m.strip() for m in ct_machines_csv.split(",") if m.strip()]
        if ct_machines:
            container["machines"] = ct_machines
        entry = related.RelatedEntry(
            name=name,
            role=related.normalize_role(_related_opt(rest, "--role", "")),
            summary=_related_opt(rest, "--summary", "") or "",
            doc=_related_opt(rest, "--doc", "") or "",
            locus=related.Locus(
                preferred=(_related_opt(rest, "--locus", "") or "").strip(),
                machines=machines,
                codespace=codespace,
                container=container,
            ),
            delegate=related.normalize_delegate(_related_opt(rest, "--delegate", "")),
            ownership=related.normalize_ownership(_related_opt(rest, "--ownership", "")),
            owner=(_related_opt(rest, "--owner", "") or "").strip(),
        )
        if not entry.ownership:
            derived, owner = related.classify_ownership(name)
            if derived:
                entry.ownership = derived
            if owner and not entry.owner:
                entry.owner = owner
        if repos.find_repo(name) is None:
            output.warn(
                f"'{name}' is not in the repos registry. Link recorded anyway; "
                f"register it with: {cfg.active_project() or 'agent-worktrees'} "
                f"repos add {name} <path> --class <class>"
            )
        related.upsert_related(anchor, entry)
        if "--primary" in rest:
            related.set_primary(anchor, name)
        output.ok(f"Linked related repo '{name}'.")
        if "--no-scaffold" not in rest:
            saved = related.get_related(anchor, name) or entry
            path, created = related.scaffold_doc(anchor, saved)
            if created:
                output.ok(f"Scaffolded narrative: {path}")
            else:
                output.info(f"Narrative exists: {path}")
        return 0

    if sub == "remove":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related remove <name>")
            return 1
        name = rest[0]
        if related.remove_related(anchor, name):
            output.ok(f"Unlinked related repo '{name}' (narrative doc left in place).")
            return 0
        output.err(f"'{name}' is not a related repo.")
        return 1

    if sub == "doc":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related doc <name>")
            return 1
        name = rest[0]
        anchors, _via_cp = _core()._related_lookup_anchors(rest, anchor, name)
        e = related.get_related_grafted(anchors, name)
        if e is None:
            output.err(f"'{name}' is not a related repo. Link it first: related add {name}")
            return 1
        path, created = related.scaffold_doc(anchor, e)
        print(path)
        if created:
            output.ok("(scaffolded)")
        return 0

    if sub == "primary":
        if rest and not rest[0].startswith("-"):
            name = rest[0]
            if related.get_related_grafted(_core()._related_config_source_anchors(anchor), name) is None:
                output.err(f"'{name}' is not a related repo. Link it first.")
                return 1
            related.set_primary(anchor, name)
            output.ok(f"primary = {name}")
        else:
            print(related.get_primary_grafted(_core()._related_config_source_anchors(anchor)) or "(unset)")
        return 0

    if sub == "resolve":
        from . import doctor

        explicit_name = rest[0] if rest and not rest[0].startswith("-") else None
        anchors = _core()._related_config_source_anchors(anchor)
        name = explicit_name or related.get_primary_grafted(anchors)
        via_cp = False
        if not name and not _related_opt(rest, "--repo"):
            cp = related.find_control_plane_anchor()
            if cp and os.path.abspath(cp) != os.path.abspath(anchor):
                cp_anchors = _core()._related_config_source_anchors(cp)
                cp_primary = related.get_primary_grafted(cp_anchors)
                if cp_primary:
                    anchors, name, via_cp = cp_anchors, cp_primary, True
        if not name:
            output.err("Usage: related resolve <name>  (or set a primary first)")
            return 1
        if explicit_name:
            anchors, via_cp = _core()._related_lookup_anchors(rest, anchor, name)
        entry = related.get_related_grafted(anchors, name)
        if entry is None:
            output.err(f"'{name}' is not a related repo.")
            return 1
        reg = repos.find_repo(name)
        current_machine = _core()._related_current_machine(anchors, anchor)
        try:
            projects = doctor._read_projects()
        except Exception:
            projects = {}
        adopted = name in projects
        base_repo = bool(projects.get(name, {}).get("base_repo", False))
        resn = related.build_resolution(
            entry,
            current_machine=current_machine,
            repo_class=(reg.repo_class if reg else None),
            repo_path=(reg.local_path() if reg else None),
            adopted=adopted,
            base_repo=base_repo,
        )
        _gh_slug = repos.github_slug(reg.remote) if (reg and reg.remote) else None
        if json_out:
            _acct_json = repos.resolve_account(reg)
            _core()._json_output(
                {
                    "name": resn.name,
                    "locus_kind": resn.locus_kind,
                    "target_machine": resn.target_machine,
                    "available_here": resn.available_here,
                    "editing_model": resn.editing_model,
                    "base_repo": base_repo,
                    "account": _acct_json,
                    "account_routing_reminder": (
                        (
                            f"Route every gh/API operation for {_gh_slug} through "
                            f"`agent-worktrees repos gh {_gh_slug} -- <gh args>` "
                            f"-- never a bare `gh <cmd>` or `gh auth switch`."
                        )
                        if _acct_json and _gh_slug
                        else (
                            f"Route every gh/API operation for this repo through "
                            f"`agent-worktrees repos gh <owner/name> -- <gh args>` "
                            f"(resolve the exact owner/name slug first; the "
                            f"registry name '{resn.name}' may not match it) -- "
                            f"never a bare `gh <cmd>` or `gh auth switch`."
                        )
                        if _acct_json
                        else None
                    ),
                    "ownership": related.effective_ownership(entry),
                    "owner": entry.owner,
                    "delegate_via": resn.delegate_via,
                    "current_machine": current_machine,
                    "steps": resn.steps,
                    "notes": resn.notes,
                    "explore": resn.explore,
                    "via_control_plane": via_cp,
                }
            )
            return 0
        output.header(f"Resolve: {resn.name}")
        if via_cp:
            output.info(
                "(resolved via the control-plane index -- this repo's own "
                "related.yaml does not list it)"
            )
        if entry.summary:
            print(f"  {entry.summary}")
        avail = "" if resn.available_here else "  (not available here)"
        print(f"  locus:    {entry.locus.preferred or 'local'}{avail}")
        print(
            f"  class:    {reg.repo_class if reg else '(not in registry)'}"
            + (f"  [{resn.editing_model}]" if resn.editing_model else "")
        )
        if reg and reg.local_path():
            print(f"  path:     {reg.local_path()}")
        _acct = repos.resolve_account(reg)
        if _acct:
            _asrc = "explicit" if (reg and reg.account) else "derived"
            print(f"  account:  {_acct} ({_asrc})")
            if _gh_slug:
                output.warn(
                    f"This repo resolves to account '{_acct}', which may "
                    f"differ from the machine's ambient `gh auth` default. "
                    f"Route every `gh`/API operation for it through "
                    f"`agent-worktrees repos gh {_gh_slug} -- <gh args>` (or "
                    f"`repos gh -- <gh args>` from inside its checkout) -- "
                    f"never a bare `gh <cmd>` or `gh auth switch` (the active "
                    f"account is machine-global and racy). Treat a wrapper "
                    f"warning like `could not mint a gh token ... using "
                    f"ambient auth` as a failure to repair before composing "
                    f"or posting."
                )
            else:
                output.warn(
                    f"This repo resolves to account '{_acct}', which may "
                    f"differ from the machine's ambient `gh auth` default. "
                    f"Resolve its exact `owner/name` slug (the registry name "
                    f"'{resn.name}' may not match it) and route every "
                    f"`gh`/API operation through `agent-worktrees repos gh "
                    f"<owner/name> -- <gh args>` -- never a bare `gh <cmd>` "
                    f"or `gh auth switch`."
                )
        if resn.delegate_via:
            print(f"  delegate: {resn.delegate_via}")
        print(f"  machine:  {current_machine or '(unknown)'}")
        for n in resn.notes:
            output.warn(n)
        print()
        if resn.explore:
            print("  Explore (read/understand the code):")
            for e in resn.explore:
                print(f"    - {e}")
            print()
        print("  Plan:")
        for s in resn.steps:
            print(f"    - {s}")
        return 0

    if sub == "classify":
        target = rest[0] if rest and not rest[0].startswith("-") else None
        overwrite = "--overwrite" in rest
        if target and target != "--all":
            derived, owner = related.classify_ownership(target)
            existing = related.get_related(anchor, target)
            if existing is None:
                output.err(f"'{target}' is not a related repo.")
                return 1
            if existing.ownership and not overwrite:
                if json_out:
                    _core()._json_output(
                        {
                            "name": target,
                            "ownership": existing.ownership,
                            "owner": existing.owner,
                            "changed": False,
                            "reason": "already set (use --overwrite)",
                        }
                    )
                else:
                    output.info(
                        f"'{target}' ownership already set to "
                        f"'{existing.ownership}' (use --overwrite to re-derive)."
                    )
                return 0
            if not derived:
                if json_out:
                    _core()._json_output(
                        {
                            "name": target,
                            "ownership": "",
                            "changed": False,
                            "reason": "underivable -- set explicitly with --ownership",
                        }
                    )
                else:
                    output.warn(
                        f"Could not derive ownership for '{target}' from its "
                        f"remote -- set it explicitly with "
                        f"`related add {target} --ownership <owned|internal|external>`."
                    )
                return 0
            related.upsert_related(
                anchor, related.RelatedEntry(name=target, ownership=derived, owner=owner)
            )
            if json_out:
                _core()._json_output(
                    {"name": target, "ownership": derived, "owner": owner, "changed": True}
                )
            else:
                output.ok(
                    f"'{target}' ownership = {derived}" + (f" (owner {owner})" if owner else "")
                )
            return 0
        changed = related.classify_all(anchor, overwrite=overwrite)
        if json_out:
            _core()._json_output({"changed": changed, "count": len(changed)})
        elif not changed:
            output.info(
                "No ownership changes (all entries classified, or "
                "underivable). Pass --overwrite to re-derive explicit ones."
            )
        else:
            output.header("Ownership classified")
            for c in changed:
                print(f"  {c['name']:<24} {c['before']} -> {c['after']}")
        return 0

    output.err(f"Unknown related subcommand: {sub}")
    _related_usage()
    return 1
