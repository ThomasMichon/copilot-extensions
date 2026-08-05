#!/usr/bin/env python3
"""Windows Terminal fragment *generation* -- the testable source of truth.

The installer's PowerShell ``Build-TerminalFragment`` (``scripts/install.ps1``)
mirrors this module's rules to write the real
``%LOCALAPPDATA%\\Microsoft\\Windows Terminal\\Fragments\\AgentWorktrees\\agent-worktrees.json``
fragment. That PowerShell path is imperative and disk-coupled, so the
*generation flow* -- "given this machine's local config, which Terminal
profiles get emitted?" -- had no unit-testable Python equivalent and no way to
**preview** the result without actually deploying it.

This module supplies both:

- :func:`build_fragment` -- a **pure** function over explicit inputs
  (:class:`ProjectInput` / :class:`RosterMachine`). No disk, no environment;
  fully unit-testable. It reproduces the PowerShell rules exactly, including the
  SHA-256 stable-GUID algorithm (``New-StableGuid``), the locked ``self.agent``
  diagonal, the default column for unmanaged projects, ``shell``/``agent``
  gating, cross-project GUID de-duplication, and self-skip.
- :func:`collect_local_projects` + :func:`preview_local` -- read *this*
  machine's real ``repos.yaml`` / ``projects.yaml`` / per-project
  ``machines.yaml`` + ``config.yaml`` and feed the pure builder, so the CLI can
  print exactly what the next ``update`` would deploy.

The **selection** semantics (default column = minimal per-agent + bare
cross-machine, the locked diagonal) are delegated to :mod:`agent_worktrees.profiles`
-- the same model the Picker persists -- so this generator and the Picker never
diverge on *what a column means*.

Cross-reference: ``scripts/install.ps1`` -> ``Build-TerminalFragment`` (profile
shapes, seeds), ``Get-DefaultSelection`` (default column), ``Get-SelEnvLabel``
(env vocabulary). Keep the two in lockstep; the parity is asserted structurally
by ``tests/test_terminal_fragment.py``.
"""
from __future__ import annotations

import hashlib
import os
import socket
import struct
import uuid
from dataclasses import dataclass, field

from . import profiles

# Default icon (matches install.ps1's ultimate fallback when no per-project or
# agent-worktrees WSL icon is deployed).
DEFAULT_ICON = r"%USERPROFILE%\.agent-worktrees\aperture-science.ico"

COLOR_SCHEME_NAME = "Aperture Science"

# machines.yaml ssh env name -> the selection's short env label
# (install.ps1 ``Get-SelEnvLabel``).
_SEL_ENV = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}
# machines.yaml ssh env name -> the profile-name env label
# (install.ps1 SSH loop ``$envLabel`` switch).
_SSH_ENV_LABEL = {"windows": "Windows", "wsl": "WSL", "linux": "Linux"}


def sel_env_label(name: str) -> str:
    """Short selection env label (``windows`` -> ``Win``)."""
    return _SEL_ENV.get(name, name)


def ssh_env_label(name: str) -> str:
    """Profile-name env label (``wsl`` -> ``WSL``)."""
    return _SSH_ENV_LABEL.get(name, name)


# ---------------------------------------------------------------------------
# Stable GUID -- byte-for-byte identical to install.ps1 ``New-StableGuid``.
# ---------------------------------------------------------------------------

def stable_guid(seed: str) -> str:
    """SHA-256 -> deterministic GUID string, matching ``New-StableGuid``.

    PowerShell builds ``[guid]::new(int32(hash,0), int16(hash,4),
    int16(hash,6), hash[8..15])`` using little-endian ``BitConverter``. The
    equivalent :class:`uuid.UUID` field construction reproduces the exact same
    canonical string (verified against the live PowerShell for multiple seeds).
    """
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    a = struct.unpack_from("<i", h, 0)[0] & 0xFFFFFFFF
    b = struct.unpack_from("<h", h, 4)[0] & 0xFFFF
    c = struct.unpack_from("<h", h, 6)[0] & 0xFFFF
    rest = h[8:16]
    return str(
        uuid.UUID(
            fields=(a, b, c, rest[0], rest[1], int.from_bytes(rest[2:8], "big"))
        )
    )


def _guid_field(seed: str) -> str:
    """The braced GUID string as it appears in the fragment (``{...}``)."""
    return "{" + stable_guid(seed) + "}"


# ---------------------------------------------------------------------------
# Input model (pure -- no disk).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SshEnvironment:
    """One ``ssh.environments`` entry from a project's machines.yaml."""

    name: str            # windows | wsl | linux
    alias: str
    shell: str = ""      # pwsh | bash


@dataclass(frozen=True)
class RosterMachine:
    """One machine from a project's machines.yaml roster."""

    key: str
    display_name: str
    ssh_ready: bool = False
    hostname: str | None = None
    environments: tuple[SshEnvironment, ...] = ()

    def identities(self) -> set[str]:
        """Lower-cased ids used to recognise the local host (self-skip)."""
        ids = {self.key}
        if self.display_name:
            ids.add(self.display_name)
        if self.hostname:
            ids.add(self.hostname)
        for e in self.environments:
            if e.alias:
                ids.add(e.alias)
        return {i.lower() for i in ids if i}


@dataclass(frozen=True)
class ProjectInput:
    """One registered project's resolved terminal-profile inputs.

    ``selection`` is ``None`` for an **unmanaged** project (no
    ``terminal_profiles`` key -> the default column is substituted) or a set of
    ``"machine|env|kind"`` keys for a **managed** column (including the empty
    set for an explicit ``terminal_profiles: []``).
    """

    name: str
    display: str
    agent_exposed: bool = True
    wsl_distro: str | None = None
    wsl_state: str | None = None
    selection: frozenset[str] | None = None
    roster: tuple[RosterMachine, ...] = ()
    icon: str = DEFAULT_ICON
    wsl_icon: str = DEFAULT_ICON


@dataclass
class EmittedProfile:
    """A single generated Windows Terminal profile (+ provenance for preview)."""

    guid: str
    name: str
    commandline: str
    icon: str
    project: str
    kind: str            # local-agent | local-wsl-agent | local-shell |
    #                      local-wsl-shell | ssh-shell | launch-agent

    def to_wt(self) -> dict:
        """The profile object as written into the fragment JSON."""
        return {
            "guid": self.guid,
            "name": self.name,
            "commandline": self.commandline,
            "icon": self.icon,
            "startingDirectory": "%USERPROFILE%",
            "colorScheme": COLOR_SCHEME_NAME,
            "hidden": False,
        }


@dataclass
class ProjectPlan:
    """Per-project decision trace for ``preview --explain``."""

    name: str
    display: str
    agent_exposed: bool
    managed: bool
    unmanaged_default: bool
    selection_keys: list[str]
    profiles: list[EmittedProfile] = field(default_factory=list)


@dataclass
class FragmentResult:
    """The full generation result."""

    profiles: list[EmittedProfile]
    plans: list[ProjectPlan]
    self_machine: str
    self_env: str

    def fragment(self) -> dict:
        """The Windows Terminal fragment object (``profiles`` + ``schemes``)."""
        return {
            "profiles": [p.to_wt() for p in self.profiles],
            "schemes": [color_scheme()],
        }


# ---------------------------------------------------------------------------
# Color scheme -- identical values to install.ps1.
# ---------------------------------------------------------------------------

def color_scheme() -> dict:
    """The 'Aperture Science' color scheme embedded in the fragment."""
    return {
        "name": COLOR_SCHEME_NAME,
        "background": "#0C0C0C",
        "foreground": "#E8DFD0",
        "cursorColor": "#F6A821",
        "selectionBackground": "#3A3A5C",
        "black": "#0C0C0C",
        "red": "#E24C3E",
        "green": "#6EA667",
        "yellow": "#F6A821",
        "blue": "#3B8EEA",
        "purple": "#9B6BC4",
        "cyan": "#4EC9B0",
        "white": "#D4D4D4",
        "brightBlack": "#3A3A3A",
        "brightRed": "#F44747",
        "brightGreen": "#B5CEA8",
        "brightYellow": "#FFD700",
        "brightBlue": "#6CB6FF",
        "brightPurple": "#D4BFFF",
        "brightCyan": "#7EECD8",
        "brightWhite": "#F0F0F0",
    }


# ---------------------------------------------------------------------------
# Selection helpers.
# ---------------------------------------------------------------------------

def _is_self(machine: RosterMachine, self_machine: str, computer_name: str) -> bool:
    locals_ = {x.lower() for x in (self_machine, computer_name) if x}
    return bool(locals_ & machine.identities())


def _local_display(roster: tuple[RosterMachine, ...], self_machine: str) -> str:
    """This host's display name from the roster (keyed by machine), else key."""
    for m in roster:
        if m.key == self_machine and m.display_name:
            return m.display_name
    return self_machine


def default_selection_keys(
    roster: tuple[RosterMachine, ...],
    self_machine: str,
    local_display: str,
    computer_name: str,
) -> set[str]:
    """The DEFAULT column for an unmanaged project (install.ps1 ``Get-DefaultSelection``).

    Minimal per-agent (this host's native Windows launcher) + bare cross-machine
    (a plain ``shell`` per remote, ready machine x env). Delegates the ON/OFF
    rule to :func:`profiles.default_selection` so the meaning of a column stays
    single-sourced with the Picker.
    """
    candidates = [profiles.self_diagonal(local_display, "Win")]
    for m in roster:
        if _is_self(m, self_machine, computer_name):
            continue
        if not m.ssh_ready:
            continue
        for e in m.environments:
            se = sel_env_label(e.name)
            candidates.append(profiles.TargetSel(m.display_name, se, "shell"))
            candidates.append(profiles.TargetSel(m.display_name, se, "agent"))
    chosen = profiles.default_selection(candidates, local_display, "Win")
    return {f"{s.machine}|{s.env}|{s.kind}" for s in chosen}


def _selected(selection: set[str], machine: str, env: str, kind: str) -> bool:
    return f"{machine}|{env}|{kind}" in selection


# ---------------------------------------------------------------------------
# The pure builder.
# ---------------------------------------------------------------------------

def build_fragment(
    projects: list[ProjectInput],
    self_machine: str,
    *,
    self_env: str = "Win",
    computer_name: str | None = None,
) -> FragmentResult:
    """Generate the terminal fragment for ``projects`` on host ``self_machine``.

    ``self_machine`` is the machine **key** (matches machines.yaml keys);
    ``computer_name`` participates only in the robust self-skip. This mirrors
    ``Build-TerminalFragment`` profile-for-profile and GUID-for-GUID.
    """
    computer_name = (
        computer_name
        or os.environ.get("COMPUTERNAME")
        or socket.gethostname()
    ).lower()

    emitted: list[EmittedProfile] = []
    plans: list[ProjectPlan] = []
    # Shared across ALL projects: local + remote *shell* GUIDs are project-
    # independent, so multiple projects selecting them emit a single profile.
    emitted_shell_guids: set[str] = set()

    for proj in projects:
        local_display = _local_display(proj.roster, self_machine)

        unmanaged = proj.selection is None
        if unmanaged:
            sel = default_selection_keys(
                proj.roster, self_machine, local_display, computer_name
            )
        else:
            sel = set(proj.selection)

        # Lock the self.agent diagonal for an agent-exposed project ("a host
        # always launches itself"), even over an explicit empty '[]'.
        if proj.agent_exposed:
            sel.add(f"{local_display}|Win|agent")

        plan = ProjectPlan(
            name=proj.name,
            display=proj.display,
            agent_exposed=proj.agent_exposed,
            managed=not unmanaged,
            unmanaged_default=unmanaged,
            selection_keys=sorted(sel),
        )

        def _emit(p: EmittedProfile) -> None:
            emitted.append(p)
            plan.profiles.append(p)

        # 1) Local Windows agent (self.agent on a Windows host).
        if _selected(sel, local_display, "Win", "agent"):
            _emit(EmittedProfile(
                guid=_guid_field(f"{proj.name}-local-windows"),
                name=proj.display,
                commandline=f'cmd /c "%USERPROFILE%\\.local\\bin\\{proj.name}.cmd"',
                icon=proj.icon,
                project=proj.name,
                kind="local-agent",
            ))

        # 2) Local WSL agent -- only when WSL support is recorded.
        if (proj.wsl_state and proj.wsl_distro
                and _selected(sel, local_display, "WSL", "agent")):
            _emit(EmittedProfile(
                guid=_guid_field(f"{proj.name}-local-wsl"),
                name=f"{proj.display} (WSL)",
                commandline=f"wsl.exe -d {proj.wsl_distro} -- bash -lc {proj.name}",
                icon=proj.wsl_icon,
                project=proj.name,
                kind="local-wsl-agent",
            ))

        # 3) Local Windows *shell* -- plain login shell, deduped across projects.
        if _selected(sel, local_display, "Win", "shell"):
            g = _guid_field(f"shell-local-{self_machine}-windows")
            if g not in emitted_shell_guids:
                _emit(EmittedProfile(
                    guid=g,
                    name=local_display,
                    commandline="pwsh.exe",
                    icon=proj.icon,
                    project=proj.name,
                    kind="local-shell",
                ))
                emitted_shell_guids.add(g)

        # 4) Local WSL *shell* -- distro optional; deduped across projects.
        if _selected(sel, local_display, "WSL", "shell"):
            g = _guid_field(f"shell-local-{self_machine}-wsl")
            if g not in emitted_shell_guids:
                cmd = f"wsl.exe -d {proj.wsl_distro}" if proj.wsl_distro else "wsl.exe"
                _emit(EmittedProfile(
                    guid=g,
                    name=f"{local_display} (WSL)",
                    commandline=cmd,
                    icon=proj.wsl_icon,
                    project=proj.name,
                    kind="local-wsl-shell",
                ))
                emitted_shell_guids.add(g)

        # 5) SSH profiles from this project's roster.
        for m in proj.roster:
            if _is_self(m, self_machine, computer_name):
                continue
            if not m.ssh_ready:
                continue
            for e in m.environments:
                sel_env = sel_env_label(e.name)
                is_bash_env = e.name in ("wsl", "linux")
                profile_icon = proj.wsl_icon if is_bash_env else proj.icon
                remote_display = m.display_name

                # Plain SSH (shell) -- gated + deduped across projects.
                ssh_guid = _guid_field(f"ssh-{m.key}-{e.name}")
                if (_selected(sel, remote_display, sel_env, "shell")
                        and ssh_guid not in emitted_shell_guids):
                    pname = (f"{remote_display} (WSL)"
                             if ssh_env_label(e.name) == "WSL" else remote_display)
                    _emit(EmittedProfile(
                        guid=ssh_guid,
                        name=pname,
                        commandline=f"ssh {e.alias}",
                        icon=profile_icon,
                        project=proj.name,
                        kind="ssh-shell",
                    ))
                    emitted_shell_guids.add(ssh_guid)

                # Launch-via-SSH (agent) -- gated; NOT deduped (project-specific).
                if _selected(sel, remote_display, sel_env, "agent"):
                    binstub = f"{proj.name}.cmd" if e.shell == "pwsh" else proj.name
                    launch_label = (f"{remote_display} WSL"
                                    if ssh_env_label(e.name) == "WSL"
                                    else remote_display)
                    _emit(EmittedProfile(
                        guid=_guid_field(f"{proj.name}-launch-{m.key}-{e.name}"),
                        name=f"{proj.display} ({launch_label})",
                        commandline=f"ssh -t {e.alias} {binstub}",
                        icon=profile_icon,
                        project=proj.name,
                        kind="launch-agent",
                    ))

        plans.append(plan)

    return FragmentResult(
        profiles=emitted, plans=plans,
        self_machine=self_machine, self_env=self_env,
    )


# ---------------------------------------------------------------------------
# Disk collection (impure) -- assemble this machine's real inputs.
# ---------------------------------------------------------------------------

def _display_from_slug(slug: str) -> str:
    """Title-case a slug ("my-project" -> "My Project") -- install.ps1 ``Get-DisplayName``."""
    return " ".join(w.capitalize() for w in slug.replace("-", " ").split())


def _load_roster(machines_yaml) -> tuple[RosterMachine, ...]:
    """Parse a machines.yaml file into :class:`RosterMachine` rows."""
    import yaml
    from pathlib import Path

    path = Path(machines_yaml)
    if not path.exists():
        return ()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    machines = data.get("machines") if isinstance(data, dict) else None
    if not isinstance(machines, dict):
        return ()
    out: list[RosterMachine] = []
    for key, entry in machines.items():
        if not isinstance(entry, dict):
            continue
        ssh = entry.get("ssh") if isinstance(entry.get("ssh"), dict) else {}
        envs = []
        for e in (ssh.get("environments") or []):
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            envs.append(SshEnvironment(
                name=name,
                alias=str(e.get("alias") or "").strip(),
                shell=str(e.get("shell") or "").strip(),
            ))
        out.append(RosterMachine(
            key=str(key),
            display_name=str(entry.get("display_name") or key),
            ssh_ready=bool(ssh.get("ready")),
            hostname=(str(entry.get("hostname")) if entry.get("hostname") else None),
            environments=tuple(envs),
        ))
    return tuple(out)


def _resolve_icon(project_dir_fn, name: str) -> tuple[str, str]:
    """Resolve (icon, wsl_icon) with install.ps1's project-then-default fallback."""
    proj_root = project_dir_fn(name)
    aw_root = project_dir_fn("agent-worktrees")

    icon = rf"%USERPROFILE%\.{name}\aperture-science.ico"
    if not (proj_root / "aperture-science.ico").exists():
        icon = DEFAULT_ICON

    wsl_icon = rf"%USERPROFILE%\.{name}\aperture-science-wsl.ico"
    if not (proj_root / "aperture-science-wsl.ico").exists():
        wsl_icon = r"%USERPROFILE%\.agent-worktrees\aperture-science-wsl.ico"
        if not (aw_root / "aperture-science-wsl.ico").exists():
            wsl_icon = icon
    return icon, wsl_icon


def collect_local_projects(current_project: str | None = None) -> list[ProjectInput]:
    """Build :class:`ProjectInput` rows from this machine's on-disk config.

    Reads ``repos.yaml`` (anchors + agent exposure), ``projects.yaml``
    (registered projects, display, wsl), each project's ``machines.yaml``
    roster, and each project's ``~/.<name>/config.yaml`` terminal-profile
    selection -- exactly the sources ``Build-TerminalFragment`` consults. The
    ``current_project`` (if any) is placed first, matching the installer's
    ordering (so GUID de-dup resolves identically).
    """
    from pathlib import Path

    from . import config as cfg
    from . import installer
    from . import repos as repos_mod

    registry = repos_mod.read_registry()
    projects_reg = installer.read_projects_registry().get("projects", {}) or {}

    plat = "windows"

    def anchor_for(name: str, entry: dict) -> str | None:
        rp = registry.repos.get(name)
        if rp:
            p = rp.local_path(plat)
            if p and Path(p).is_dir():
                return p
        a = entry.get("anchor") if isinstance(entry, dict) else None
        if isinstance(a, str) and a:
            return a
        rp = registry.repos.get(name)
        return rp.local_path(plat) if rp else None

    def agent_exposed(name: str) -> bool:
        rp = registry.repos.get(name)
        return bool(rp.agent) if rp else True

    def wsl_of(entry: dict) -> tuple[str | None, str | None]:
        w = entry.get("wsl") if isinstance(entry, dict) else None
        if isinstance(w, dict):
            return (w.get("distro"), w.get("state"))
        return (None, None)

    ordered: list[str] = []
    if current_project:
        ordered.append(current_project)
    for name in projects_reg:
        if name not in ordered:
            ordered.append(name)

    out: list[ProjectInput] = []
    for name in ordered:
        entry = projects_reg.get(name, {}) or {}
        anchor = anchor_for(name, entry)
        roster: tuple[RosterMachine, ...] = ()
        if anchor:
            roster = _load_roster(Path(anchor) / "machines.yaml")

        display = (entry.get("display_name") if isinstance(entry, dict) else None) \
            or _display_from_slug(name)

        cfg_path = cfg.project_dir(name) / "config.yaml"
        if profiles.has_selection(cfg_path):
            sel = frozenset(
                f"{s.machine}|{s.env}|{s.kind}"
                for s in profiles.load_selection(cfg_path)
            )
        else:
            sel = None

        distro, state = wsl_of(entry)
        icon, wsl_icon = _resolve_icon(cfg.project_dir, name)

        out.append(ProjectInput(
            name=name,
            display=display,
            agent_exposed=agent_exposed(name),
            wsl_distro=distro,
            wsl_state=state,
            selection=sel,
            roster=roster,
            icon=icon,
            wsl_icon=wsl_icon,
        ))
    return out


def preview_local(
    self_machine: str,
    *,
    current_project: str | None = None,
    self_env: str = "Win",
) -> FragmentResult:
    """Collect this machine's inputs and build the fragment (no disk write)."""
    projects = collect_local_projects(current_project=current_project)
    return build_fragment(projects, self_machine, self_env=self_env)


# ---------------------------------------------------------------------------
# Windows Terminal state reconciliation.
#
# Writing the fragment is only half the job: WT tracks every fragment GUID it
# has ever materialized in ``state.json``'s ``generatedProfiles``. If a GUID is
# in ``generatedProfiles`` but *not* in ``settings.json``'s profile list, WT
# reads that as "the user deleted this generated profile" and HIDES it -- even
# though it is still in the fragment. The installer (``Sync-TerminalState``)
# must therefore prune such GUIDs from ``generatedProfiles`` so WT re-discovers
# them on next launch.
#
# The historical PowerShell pruned by the *old-fragment -> new-fragment delta*
# (only GUIDs "newly added since the previous fragment"). That is **not
# idempotent**: once a GUID is present in two consecutive fragments it is never
# reconsidered, so a one-shot prune lost to the WT-running race (WT overwrites
# ``state.json`` on exit) leaves the profile hidden *forever*, and no later
# ``update`` heals it. That is the "every update makes the fragments worse"
# failure.
#
# :func:`reconcile_generated_profiles` replaces that with a **convergent
# invariant** evaluated every run: a current-fragment GUID that WT has not
# materialized into ``settings.json`` must be absent from ``generatedProfiles``.
# It heals regardless of update history.
# ---------------------------------------------------------------------------

def _norm_guids(guids) -> set[str]:
    return {str(g).strip().lower() for g in (guids or []) if str(g).strip()}


@dataclass
class GeneratedProfilesPlan:
    """Reconciliation result for ``state.json``'s ``generatedProfiles``."""

    keep: list[str]
    remove: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.remove)


def reconcile_generated_profiles(
    fragment_guids,
    settings_guids,
    generated_profiles,
    *,
    stale_guids=(),
    changed_guids=(),
) -> GeneratedProfilesPlan:
    """Compute the pruned ``generatedProfiles`` (idempotent, convergent).

    A GUID is removed from ``generated_profiles`` when it is any of:

    * **not materialized** -- present in the current ``fragment_guids`` but
      absent from ``settings_guids`` (WT is hiding a live fragment profile ->
      force re-discovery). This alone heals the "hidden profile" bug on the
      *next* update, no matter how the drift arose.
    * **stale** -- one of ``stale_guids`` (was in the previous fragment, no
      longer emitted -> WT should forget it).
    * **changed** -- one of ``changed_guids`` (same GUID, new content -> force
      re-discovery so WT picks up the new commandline/name).

    Everything else is kept, preserving user customizations for materialized
    profiles and leaving unrelated (e.g. other-extension) GUIDs untouched.
    Comparison is case-insensitive; the kept/removed lists preserve the original
    ``generated_profiles`` entries.
    """
    frag = _norm_guids(fragment_guids)
    settings = _norm_guids(settings_guids)
    stale = _norm_guids(stale_guids)
    changed = _norm_guids(changed_guids)

    not_materialized = {g for g in frag if g not in settings}
    remove_set = not_materialized | stale | changed

    keep: list[str] = []
    remove: list[str] = []
    for g in (generated_profiles or []):
        (remove if str(g).strip().lower() in remove_set else keep).append(g)
    return GeneratedProfilesPlan(keep=keep, remove=remove)


@dataclass
class WtStateDiagnosis:
    """Read-only assessment of live Windows Terminal state drift."""

    fragment_count: int
    settings_count: int
    generated_count: int
    hidden: list[str]     # in fragment + generatedProfiles, missing from settings
    orphans: list[str]    # in generatedProfiles, in no fragment and not in settings
    duplicate_names: list[tuple[str, int]]  # profile name -> count, when > 1

    @property
    def healthy(self) -> bool:
        return not self.hidden and not self.duplicate_names


def _read_json(path):
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _wt_local_state_dir():
    from pathlib import Path

    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return (Path(base) / "Packages"
            / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState")


def _wt_fragments_dir():
    from pathlib import Path

    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "Microsoft" / "Windows Terminal" / "Fragments"


def diagnose_wt_state() -> WtStateDiagnosis | None:
    """Read live WT fragments + ``settings.json`` + ``state.json`` (read-only).

    Returns ``None`` when the Windows Terminal state directory is unavailable
    (non-Windows, or WT not installed). Never mutates anything.
    """
    from pathlib import Path

    local_state = _wt_local_state_dir()
    frag_dir = _wt_fragments_dir()
    if not local_state or not frag_dir or not Path(local_state).exists():
        return None

    fragment_guids: set[str] = set()
    if Path(frag_dir).exists():
        for jf in Path(frag_dir).rglob("*.json"):
            data = _read_json(jf)
            if isinstance(data, dict):
                for p in (data.get("profiles") or []):
                    if isinstance(p, dict) and p.get("guid"):
                        fragment_guids.add(str(p["guid"]).lower())

    settings = _read_json(Path(local_state) / "settings.json") or {}
    prof_list = (((settings.get("profiles") or {}).get("list"))
                 if isinstance(settings, dict) else None) or []
    settings_guids = {str(p["guid"]).lower()
                      for p in prof_list
                      if isinstance(p, dict) and p.get("guid")}
    name_counts: dict[str, int] = {}
    for p in prof_list:
        if isinstance(p, dict) and p.get("name"):
            name_counts[p["name"]] = name_counts.get(p["name"], 0) + 1

    state = _read_json(Path(local_state) / "state.json") or {}
    generated = {str(g).lower()
                 for g in (state.get("generatedProfiles") or [])
                 if isinstance(state, dict)}

    hidden = sorted(g for g in fragment_guids
                    if g in generated and g not in settings_guids)
    orphans = sorted(g for g in generated
                     if g not in fragment_guids and g not in settings_guids)
    duplicate_names = sorted((n, c) for n, c in name_counts.items() if c > 1)

    return WtStateDiagnosis(
        fragment_count=len(fragment_guids),
        settings_count=len(settings_guids),
        generated_count=len(generated),
        hidden=hidden,
        orphans=orphans,
        duplicate_names=duplicate_names,
    )
