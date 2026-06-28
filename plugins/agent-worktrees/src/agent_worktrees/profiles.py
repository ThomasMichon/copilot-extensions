#!/usr/bin/env python3
"""Terminal-profile *selection* model -- the Picker's Profiles grid, persisted.

Each machine owns **one column** of the host x target matrix: the set of launch
targets that *this* machine's terminal app carries a profile for, for the active
repo. The selection is the source of truth (operator direction, effort
``worktree-picker-tty-overhaul``):

- **Adoption** seeds the column with only the locked **self.agent** diagonal
  ("this host launches itself as an agent").
- The **Picker** edits the column (and, over SSH, other machines' columns).
- ``<project> update`` / ``install --refresh-profiles`` **mirrors** the column
  to the real Windows Terminal fragment / Tabby profiles.

Storage: a top-level ``terminal_profiles`` list in the machine-local, per-repo
``~/.<project>/config.yaml`` (the config dir is already per-repo, so a top-level
key there is implicitly scoped to this repo + this machine):

    terminal_profiles:
      - {machine: Lambda-Core, env: Win, kind: agent}   # self (locked)
      - {machine: Borealis,    env: WSL, kind: shell}

This module only models + persists the *selection*. Mirroring it to terminal
apps is the installer's job (PowerShell ``Build-TerminalFragment``).

This is a DIFFERENT system from ``config.copilot_profiles`` (the agent *backend*
cloud/local-model selection) -- the two must not be conflated.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_KEY = "terminal_profiles"
KINDS = ("agent", "shell")


@dataclass(frozen=True)
class TargetSel:
    """One selected launch target in a machine's terminal-profile column.

    ``machine`` / ``env`` are the picker's display labels (machines.yaml
    ``display_name`` + short env label ``Win`` / ``WSL`` / ``Linux``), matching
    the roster axes so the grid lines up. ``kind`` is ``agent`` (a worktree
    launch) or ``shell`` (a plain login shell).
    """

    machine: str
    env: str
    kind: str

    def as_dict(self) -> dict[str, str]:
        return {"machine": self.machine, "env": self.env, "kind": self.kind}

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.machine, self.env, self.kind)


def self_diagonal(machine: str, env: str) -> TargetSel:
    """The locked self.agent target every host always carries for this repo."""
    return TargetSel(machine, env, "agent")


def seed_selection(machine: str, env: str) -> list[TargetSel]:
    """Initial column at adoption: the self.agent diagonal only (self-only)."""
    return [self_diagonal(machine, env)]


def _coerce(raw) -> list[TargetSel]:
    """Parse a raw ``terminal_profiles`` value into validated TargetSel rows.

    Tolerant: skips malformed entries, normalizes ``kind`` to the known set
    (defaulting to ``agent``), and de-duplicates while preserving order.
    """
    out: list[TargetSel] = []
    seen: set[tuple[str, str, str]] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        machine = str(item.get("machine") or "").strip()
        env = str(item.get("env") or "").strip()
        kind = str(item.get("kind") or "agent").strip().lower()
        if not machine or not env:
            continue
        if kind not in KINDS:
            kind = "agent"
        sel = TargetSel(machine, env, kind)
        if sel.key in seen:
            continue
        seen.add(sel.key)
        out.append(sel)
    return out


def load_selection(config_path: Path) -> list[TargetSel]:
    """Read this machine's terminal-profile column from ``config.yaml``.

    Returns ``[]`` when the file or key is absent (never raises): an empty
    column means "no profiles selected yet" (pre-adoption-seed legacy configs).
    """
    try:
        if not config_path.exists():
            return []
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    return _coerce(raw.get(CONFIG_KEY))


def has_selection(config_path: Path) -> bool:
    """Whether ``config.yaml`` carries an explicit ``terminal_profiles`` key.

    Distinguishes a **managed** column (the key is present -- adoption seeded it
    or the Picker wrote it) from a **legacy/unmanaged** config (no key). The
    distinction matters because absent must mean the same thing to the Picker
    and the installer's mirror: legacy = "all targets" (the historical
    emit-everything behavior), managed = exactly the listed selection.
    """
    try:
        if not config_path.exists():
            return False
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(raw, dict) and CONFIG_KEY in raw


def normalize_selection(
    sels: list[TargetSel], self_machine: str, self_env: str
) -> list[TargetSel]:
    """Force-include the locked self.agent diagonal and de-dupe, stable order.

    The self.agent profile is mandatory (a host always launches itself), so it
    is always present regardless of what the caller passed.
    """
    diag = self_diagonal(self_machine, self_env)
    out: list[TargetSel] = [diag]
    seen = {diag.key}
    for s in sels:
        if s.key not in seen:
            seen.add(s.key)
            out.append(s)
    return out


def save_selection(
    config_path: Path,
    sels: list[TargetSel],
    *,
    self_machine: str,
    self_env: str,
) -> list[TargetSel]:
    """Persist this machine's column into ``config.yaml`` (read-modify-write).

    Preserves every other key in the file; only the ``terminal_profiles`` list
    is rewritten. The locked self.agent diagonal is always included. Returns the
    normalized selection actually written.
    """
    config_path = Path(config_path)
    data: dict = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, yaml.YAMLError):
            data = {}
    normalized = normalize_selection(sels, self_machine, self_env)
    data[CONFIG_KEY] = [s.as_dict() for s in normalized]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    return normalized
