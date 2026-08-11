"""Per-project "related repos" -- the directional relationship layer.

Where ``repos.yaml`` (see :mod:`agent_worktrees.repos`) is a **global,
machine-wide catalog** of every checkout, this module models the
**directional, per-project** view: *from the current repo's point of view*,
which other repos are relevant, why, and -- crucially -- **where to actually
work on them**.

The data lives **in-repo and committed**, at
``<anchor>/.agent-worktrees/related.yaml`` (alongside the in-repo
``config.yaml``), with a plain-markdown narrative per related repo under
``<anchor>/.agent-worktrees/related/<name>.md``.  Because it is committed, it
travels with the repo and is shared across machines and collaborators.

Design intent (so we never duplicate the registry):

* ``related.yaml`` keys are **names in the global registry**.  A related entry
  adds only **relationship** (``role`` / ``summary`` / ``doc``), **locus**
  (where work happens: ``local`` / ``machine:<key>`` / ``codespace``, plus
  per-machine availability), and **delegate** (how to hand work to the agent
  that owns the repo).  Checkout paths, class, remote, and ``contributing``
  still resolve from ``repos.yaml`` -- never restated here.
* Per-machine availability and preferred locus are **directional only** -- the
  global registry is intentionally *not* extended with per-machine paths.
* A top-level ``primary:`` marker names the default/primary project repo.

Schema (``<anchor>/.agent-worktrees/related.yaml``)::

    primary: example-web
    related:
      example-web:
        role: product
        summary: "Primary product monorepo we ship changes to."
        doc: related/example-web.md
        locus:
          preferred: codespace          # local | machine:<key> | codespace
          codespace: { repo: org/example-web-codespaces,
                       machine: largePremiumLinux256gb, location: EastUs }
        delegate: { via: agent-codespaces }
        plugins:                        # related-repo plugins agent-bridge side-loads
          - { source: example-web-codespace@example-marketplace }
          - { source: some-plugin@example-marketplace, enable: false }
      copilot-extensions:
        role: tooling
        summary: "Source of the plugins this control plane drives."
        doc: related/copilot-extensions.md
        locus: { preferred: machine:dev6, machines: [dev6, cloud1] }
        delegate: { via: agent-bridge }

All reads degrade safely: a missing or malformed file yields an empty
:class:`RelatedConfig` rather than raising, mirroring the config/registry
loaders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# The in-repo ``.agent-worktrees/`` directory name.  Kept in sync with
# ``config.INREPO_CONFIG_DIRNAME``; defined locally so this module has no
# import-time dependency on the config layer.
INREPO_DIRNAME = ".agent-worktrees"
RELATED_FILENAME = "related.yaml"      # <anchor>/.agent-worktrees/related.yaml
RELATED_DOCS_DIRNAME = "related"       # <anchor>/.agent-worktrees/related/<name>.md

# Descriptive roles a related repo can play, *from the current repo's POV*.
# Stored verbatim (lower-cased) -- unknown values are kept, not coerced, since
# the role is human-facing documentation.  Callers/CLI may validate against
# this set.
VALID_ROLES = ("product", "dependency", "consumer", "tooling", "docs", "sibling")

# How work is handed off to the agent that owns a related repo.
VALID_DELEGATES = ("agent-bridge", "agent-codespaces", "none")

# Ownership relationship of a related repo, from the operator's POV. This is
# **expected-behavior metadata** (e.g. it drives the AI-attribution decision:
# an authored increment in an ``owned`` -- or non-public ``internal`` -- target
# needs no acknowledgement, an ``external`` one does). It is **derived ONCE at
# registration** from the operator's own gh account logins + the repo's remote
# (see :func:`classify_ownership`), then persisted here and treated as
# authoritative -- consumers read this manifest instead of re-inspecting live gh
# accounts. An explicit value always wins over the derivation (e.g. an ADO repo
# the operator wholly owns is marked ``owned`` even though the ADO-host default
# is ``internal``).
#   owned     -- the operator wholly owns the target (their own gh namespace, or
#                an explicitly-owned repo). No AI-acknowledgement on authored
#                increments.
#   internal  -- Microsoft-internal, not owned (e.g. an ADO org repo). No
#                acknowledgement on authored increments, but not the operator's.
#   external  -- public/external, not owned. Authored increments are
#                acknowledged.
VALID_OWNERSHIP = ("owned", "internal", "external")

# Locus "kinds" -- where work on a related repo actually happens.
VALID_LOCUS_KINDS = ("local", "machine", "codespace", "container")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Locus:
    """Where work on a related repo happens, *from the current machine*.

    ``preferred`` is one of ``local``, ``machine:<key>``, ``codespace``, or
    ``container``.  ``machines`` lists the machine keys on which the repo is
    available *locally* (e.g. ``[dev6, cloud1]``) -- the per-machine
    availability the global, per-*platform* registry cannot express.

    Two **cloud/sandbox venues** carry their own provisioning hints, each a
    free-form mapping:

    * ``codespace`` -- a GitHub CodeSpace (``repo`` / ``machine`` / ``location``
      / ``workspace_folder``).  CodeSpaces run in the cloud, so they are
      available from *any* machine.
    * ``container`` -- a local Docker dev-container fleet (``repo`` /
      ``workspace_folder`` plus a ``machines`` list scoping it to the boxes
      that host the fleet).  Unlike a CodeSpace, a container fleet is local, so
      ``machines`` restricts where it can be used (e.g. ``[dev6]``).

    ``workspace_folder`` records the checkout path the venue lands in (e.g.
    ``/workspaces/example-web``), which often differs from the venue ``repo`` name.
    """

    preferred: str = ""
    machines: list[str] = field(default_factory=list)
    codespace: dict[str, Any] = field(default_factory=dict)
    container: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.preferred or self.machines or self.codespace or self.container
        )


@dataclass
class RelatedEntry:
    """A single related repo, keyed by its **global-registry** name."""

    name: str
    role: str = ""
    summary: str = ""
    doc: str = ""                       # relative to ``.agent-worktrees/``
    locus: Locus = field(default_factory=Locus)
    delegate: str = ""                  # the ``via`` value; see VALID_DELEGATES
    # Ownership relationship (one of VALID_OWNERSHIP) + the resolving operator
    # account. Derived once at registration (:func:`classify_ownership`) and
    # then authoritative; an explicit value in related.yaml always wins. Empty
    # ``ownership`` means "not classified" -- consumers fall back to judging the
    # target themselves. See VALID_OWNERSHIP for the AI-attribution semantics.
    ownership: str = ""
    owner: str = ""                     # resolving operator account login (optional)
    # Plugins this control plane side-loads when delegating work to the related
    # repo (the *related-repo* plugin lane -- distinct from a CodeSpace's own
    # ``codespacePlugins``). Each item is a normalized ``{"source": str,
    # "enable": bool}`` mapping; ``source`` is any ``copilot plugin install``
    # source. Consumed by agent-bridge, which injects them into the dispatched
    # agent's launch (``--plugin-dir`` / user-settings), never by agent-worktrees.
    plugins: list[dict[str, Any]] = field(default_factory=list)
    # The checkout anchor this entry was **read from**, set by the state-root
    # config-graft (:func:`read_related_grafted`) so a knowledge-repo overlay
    # entry's narrative ``doc`` -- which is relative to ITS source repo's
    # ``.agent-worktrees/`` -- still resolves against that repo, not the harness
    # base. ``None`` for a plain single-anchor read; never serialized.
    origin_anchor: str | None = None


@dataclass
class RelatedConfig:
    """The full ``related.yaml`` content for one repo."""

    primary: str = ""
    related: dict[str, RelatedEntry] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def related_dir(anchor: str | Path) -> Path:
    """The in-repo ``<anchor>/.agent-worktrees`` directory."""
    return Path(anchor) / INREPO_DIRNAME


def related_path(anchor: str | Path) -> Path:
    """Path to ``<anchor>/.agent-worktrees/related.yaml``."""
    return related_dir(anchor) / RELATED_FILENAME


def docs_dir(anchor: str | Path) -> Path:
    """The narrative docs directory ``<anchor>/.agent-worktrees/related``."""
    return related_dir(anchor) / RELATED_DOCS_DIRNAME


def default_doc_rel(name: str) -> str:
    """Default narrative doc path for ``name`` (relative to ``.agent-worktrees``)."""
    return f"{RELATED_DOCS_DIRNAME}/{name}.md"


def doc_abs_path(anchor: str | Path, entry_or_name: RelatedEntry | str) -> Path:
    """Absolute path to a related repo's narrative doc.

    Resolves the entry's ``doc`` field (or the default ``related/<name>.md``)
    against the in-repo ``.agent-worktrees`` directory. When the entry carries an
    ``origin_anchor`` (a knowledge-overlay config-graft entry), the doc resolves
    against **that** anchor -- a knowledge-repo entry's ``doc`` is relative to the
    knowledge checkout, not the harness base it was grafted onto.
    """
    if isinstance(entry_or_name, RelatedEntry):
        rel = entry_or_name.doc or default_doc_rel(entry_or_name.name)
        if entry_or_name.origin_anchor:
            anchor = entry_or_name.origin_anchor
    else:
        rel = default_doc_rel(entry_or_name)
    return related_dir(anchor) / rel


# ---------------------------------------------------------------------------
# Normalizers / parsers
# ---------------------------------------------------------------------------

def normalize_role(value: str | None) -> str:
    """Lower-case and strip a role; unknown roles are kept verbatim."""
    return (value or "").strip().lower()


def normalize_delegate(value: str | None) -> str:
    """Lower-case and strip a delegate target (the ``via`` value)."""
    return (value or "").strip().lower()


def normalize_ownership(value: str | None) -> str:
    """Lower-case and strip an ownership value; unknown values are dropped.

    Only members of :data:`VALID_OWNERSHIP` are kept -- an unrecognized value
    normalizes to ``""`` (unclassified) so a typo never silently asserts a
    wrong AI-attribution posture.
    """
    v = (value or "").strip().lower()
    return v if v in VALID_OWNERSHIP else ""


def parse_preferred(value: str | None) -> tuple[str, str]:
    """Split a ``locus.preferred`` value into ``(kind, machine)``.

    - ``"local"``        -> ``("local", "")``
    - ``"codespace"``    -> ``("codespace", "")``
    - ``"machine:dev6"`` -> ``("machine", "dev6")``
    - empty / unknown    -> ``(value_lower, "")``  (kind returned verbatim)
    """
    v = (value or "").strip().lower()
    if not v:
        return ("", "")
    if v.startswith("machine:"):
        return ("machine", v.split(":", 1)[1].strip())
    return (v, "")


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def _parse_venue(raw: Any) -> dict[str, Any]:
    """Parse a venue block (``codespace`` / ``container``) into a flat mapping.

    Scalars become strings; a list value (e.g. ``machines: [dev6]``) is kept as
    a list of strings.  Non-dict input degrades to an empty mapping.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = [str(x).strip() for x in v if str(x).strip()]
        else:
            out[str(k)] = str(v)
    return out


def _parse_locus(raw: Any) -> Locus:
    if not isinstance(raw, dict):
        return Locus()
    preferred = str(raw.get("preferred", "")).strip()
    raw_machines = raw.get("machines", [])
    machines = (
        [str(m).strip() for m in raw_machines if str(m).strip()]
        if isinstance(raw_machines, list)
        else []
    )
    return Locus(
        preferred=preferred,
        machines=machines,
        codespace=_parse_venue(raw.get("codespace", {})),
        container=_parse_venue(raw.get("container", {})),
    )


def _parse_delegate(raw: Any) -> str:
    """Accept ``delegate: {via: X}`` (canonical) or a bare ``delegate: X``."""
    if isinstance(raw, dict):
        return normalize_delegate(raw.get("via", ""))
    if isinstance(raw, str):
        return normalize_delegate(raw)
    return ""


def _parse_plugins(raw: Any) -> list[dict[str, Any]]:
    """Normalise a ``plugins`` block to ``[{"source": str, "enable": bool}]``.

    Accepts a list whose items are either a bare source string (shorthand for
    ``{source, enable: true}``) or a mapping with ``source`` (+ optional
    ``enable``). Items without a usable ``source`` are skipped; duplicate
    sources are collapsed (last ``enable`` wins). Never raises.
    """
    if not isinstance(raw, list):
        return []
    out: dict[str, dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, str):
            source, enable = item.strip(), True
        elif isinstance(item, dict):
            source = str(item.get("source", "")).strip()
            enable = bool(item.get("enable", True))
        else:
            continue
        if not source:
            continue
        out[source] = {"source": source, "enable": enable}
    return list(out.values())


def read_related(anchor: str | Path) -> RelatedConfig:
    """Load ``<anchor>/.agent-worktrees/related.yaml``.

    Returns an empty :class:`RelatedConfig` if the file is missing, empty, or
    malformed -- never raises on bad content.
    """
    path = related_path(anchor)
    if not path.exists():
        return RelatedConfig()

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return RelatedConfig()
    if not isinstance(data, dict):
        return RelatedConfig()

    primary = str(data.get("primary", "")).strip()

    related: dict[str, RelatedEntry] = {}
    raw_related = data.get("related", {})
    if isinstance(raw_related, dict):
        for name, entry in raw_related.items():
            # A bare ``name:`` (null value) is a valid minimal link.
            if entry is None:
                entry = {}
            if not isinstance(entry, dict):
                continue
            related[str(name)] = RelatedEntry(
                name=str(name),
                role=normalize_role(entry.get("role", "")),
                summary=str(entry.get("summary", "")).strip(),
                doc=str(entry.get("doc", "")).strip(),
                locus=_parse_locus(entry.get("locus")),
                delegate=_parse_delegate(entry.get("delegate")),
                ownership=normalize_ownership(entry.get("ownership")),
                owner=str(entry.get("owner", "")).strip(),
                plugins=_parse_plugins(entry.get("plugins")),
            )

    return RelatedConfig(primary=primary, related=related)


def _quote(v: str) -> str:
    """Quote a YAML scalar if it contains characters needing escaping."""
    if v == "" or any(c in v for c in (":", "#", "'", '"', "\\", "{", "}", "[", "]")):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


def _emit_venue(name: str, venue: dict[str, Any]) -> str:
    """Render a venue mapping (``codespace`` / ``container``) as inline YAML.

    Scalar values are quoted as needed; a list value renders as a flow
    sequence (``machines: [dev6, cloud1]``).
    """
    parts: list[str] = []
    for k, v in venue.items():
        if isinstance(v, list):
            rendered = ", ".join(_quote(str(x)) for x in v)
            parts.append(f"{k}: [{rendered}]")
        else:
            parts.append(f"{k}: {_quote(str(v))}")
    return f"{name}: {{ {', '.join(parts)} }}"


def _emit_locus(lines: list[str], locus: Locus, indent: str) -> None:
    if locus.is_empty():
        return
    lines.append(f"{indent}locus:")
    inner = indent + "  "
    if locus.preferred:
        lines.append(f"{inner}preferred: {_quote(locus.preferred)}")
    if locus.machines:
        rendered = ", ".join(_quote(m) for m in locus.machines)
        lines.append(f"{inner}machines: [{rendered}]")
    if locus.codespace:
        lines.append(f"{inner}{_emit_venue('codespace', locus.codespace)}")
    if locus.container:
        lines.append(f"{inner}{_emit_venue('container', locus.container)}")


def write_related(anchor: str | Path, cfg: RelatedConfig) -> None:
    """Write ``related.yaml`` with stable, hand-formatted YAML.

    Only non-empty fields are emitted, keeping committed files minimal and
    review-friendly (matching ``repos.write_registry``).
    """
    path = related_path(anchor)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# <repo>/.agent-worktrees/related.yaml",
        "# Directional, per-project related-repos index (this repo's POV).",
        "# Keys are names in the global repos registry (~/.agent-worktrees/repos.yaml);",
        "# this file adds relationship + locus + delegate + ownership -- never checkout paths.",
        "",
    ]

    if cfg.primary:
        lines.append(f"primary: {_quote(cfg.primary)}")
        lines.append("")

    if cfg.related:
        lines.append("related:")
        for name in sorted(cfg.related.keys()):
            entry = cfg.related[name]
            lines.append(f"  {name}:")
            if entry.role:
                lines.append(f"    role: {_quote(entry.role)}")
            if entry.summary:
                lines.append(f"    summary: {_quote(entry.summary)}")
            if entry.doc:
                lines.append(f"    doc: {_quote(entry.doc)}")
            _emit_locus(lines, entry.locus, "    ")
            if entry.delegate:
                lines.append(f"    delegate: {{ via: {_quote(entry.delegate)} }}")
            if entry.ownership:
                lines.append(f"    ownership: {_quote(entry.ownership)}")
            if entry.owner:
                lines.append(f"    owner: {_quote(entry.owner)}")
            if entry.plugins:
                lines.append("    plugins:")
                for p in entry.plugins:
                    src = _quote(str(p.get("source", "")))
                    if p.get("enable", True):
                        lines.append(f"      - {{ source: {src} }}")
                    else:
                        lines.append(f"      - {{ source: {src}, enable: false }}")
            lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Operations (in-memory mutate + persist)
# ---------------------------------------------------------------------------

def get_related(anchor: str | Path, name: str) -> RelatedEntry | None:
    """Return the related entry for ``name``, or ``None``."""
    return read_related(anchor).related.get(name)


def _control_plane_project(anchor: str | Path) -> str | None:
    """The ``control_plane.project`` declared in ``<anchor>/machines.yaml``.

    Accepts both the mapping form (``control_plane: {project: <name>}``) and the
    bare form (``control_plane: <name>``). Returns ``None`` when the file is
    absent/malformed or declares no control plane. Fail-safe (never raises).
    """
    path = Path(anchor) / ".agent-worktrees" / "machines.yaml"
    if not path.is_file():
        path = Path(anchor) / "machines.yaml"  # legacy repo-root fallback
    try:
        if not path.is_file():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    cp = data.get("control_plane")
    val = cp.get("project") if isinstance(cp, dict) else cp
    return val.strip() if isinstance(val, str) and val.strip() else None


def find_control_plane_anchor() -> str | None:
    """Locate the control-plane project's anchor via the global repos registry.

    The control plane is the repo whose ``machines.yaml`` declares
    ``control_plane.project``; its ``related.yaml`` is the canonical, directional
    index this whole control plane coordinates from. Scans registered repo
    anchors for that declaration and returns the named project's anchor (falling
    back to the declaring anchor when the named project isn't separately
    registered). Fail-safe -> ``None``.

    This lets read-only ``related`` lookups (``resolve`` / ``show`` / ``doc``)
    fall back to the control-plane index when run from *inside* a coordinated
    repo's own checkout -- where the cwd-directional index is empty and the
    guidance would otherwise dead-end ("not a related repo").
    """
    from . import repos
    try:
        entries = repos.list_repos()
    except Exception:
        return None
    by_name = {e.name: e for e in entries}
    for e in entries:
        anchor = e.local_path()
        if not anchor:
            continue
        cp = _control_plane_project(anchor)
        if not cp:
            continue
        target = by_name.get(cp)
        tgt_path = target.local_path() if target else None
        return tgt_path or anchor
    return None


def list_related(
    anchor: str | Path, *, role: str | None = None
) -> list[RelatedEntry]:
    """Return related entries, optionally filtered by ``role``, name-sorted."""
    entries = list(read_related(anchor).related.values())
    if role:
        wanted = normalize_role(role)
        entries = [e for e in entries if e.role == wanted]
    return sorted(entries, key=lambda e: e.name)


def get_primary(anchor: str | Path) -> str:
    """Return the ``primary:`` marker (empty string if unset)."""
    return read_related(anchor).primary


# ---------------------------------------------------------------------------
# State-root config-graft (E1e): union related.yaml across config-source anchors
# ---------------------------------------------------------------------------

def read_related_grafted(anchors: list[str | Path]) -> RelatedConfig:
    """Union ``related.yaml`` across ordered config-source anchors.

    This is the E1e **knowledge overlay** (config-graft): a stateless harness
    contributes its (name-free) base ``related.yaml`` while the bound **knowledge
    repo** contributes the real personal entries. ``anchors`` is the overlay order
    (base first, knowledge overlay last), as produced by
    :func:`agent_worktrees.state_root.config_source_anchors`.

    Merge semantics: later anchors **overlay** earlier ones -- on a name collision
    the later entry wins wholesale, and the later ``primary`` wins when set. Each
    returned entry's :attr:`RelatedEntry.origin_anchor` records the anchor it was
    read from so its narrative ``doc`` still resolves against its own repo (see
    :func:`doc_abs_path`).

    A single-anchor list reproduces the pre-graft single-repo behavior exactly, so
    callers can always route reads through this function.
    """
    merged = RelatedConfig()
    for anchor in anchors:
        rc = read_related(anchor)
        if rc.primary:
            merged.primary = rc.primary
        for name, entry in rc.related.items():
            entry.origin_anchor = str(anchor)
            merged.related[name] = entry
    return merged


def list_related_grafted(
    anchors: list[str | Path], *, role: str | None = None
) -> list[RelatedEntry]:
    """Grafted variant of :func:`list_related` over config-source anchors."""
    entries = list(read_related_grafted(anchors).related.values())
    if role:
        wanted = normalize_role(role)
        entries = [e for e in entries if e.role == wanted]
    return sorted(entries, key=lambda e: e.name)


def get_related_grafted(
    anchors: list[str | Path], name: str
) -> RelatedEntry | None:
    """Grafted variant of :func:`get_related` over config-source anchors."""
    return read_related_grafted(anchors).related.get(name)


def get_primary_grafted(anchors: list[str | Path]) -> str:
    """Grafted variant of :func:`get_primary` over config-source anchors."""
    return read_related_grafted(anchors).primary


def set_primary(anchor: str | Path, name: str) -> RelatedConfig:
    """Set the ``primary:`` marker and persist.  Returns the updated config."""
    cfg = read_related(anchor)
    cfg.primary = str(name).strip()
    write_related(anchor, cfg)
    return cfg


def upsert_related(anchor: str | Path, entry: RelatedEntry) -> RelatedConfig:
    """Insert or merge a related entry and persist.

    A merge only overwrites fields that are set on ``entry`` (non-empty),
    preserving existing values otherwise -- so callers can update one field
    without clobbering the rest.
    """
    cfg = read_related(anchor)
    existing = cfg.related.get(entry.name)
    if existing is None:
        cfg.related[entry.name] = entry
    else:
        if entry.role:
            existing.role = entry.role
        if entry.summary:
            existing.summary = entry.summary
        if entry.doc:
            existing.doc = entry.doc
        # Merge locus at the *field* level so a partial update (e.g. only
        # ``--machines``) overwrites just that sub-field and preserves the
        # rest (``preferred`` / ``codespace`` / ``container``).  See #128.
        if entry.locus.preferred:
            existing.locus.preferred = entry.locus.preferred
        if entry.locus.machines:
            existing.locus.machines = entry.locus.machines
        if entry.locus.codespace:
            existing.locus.codespace = entry.locus.codespace
        if entry.locus.container:
            existing.locus.container = entry.locus.container
        if entry.delegate:
            existing.delegate = entry.delegate
        if entry.ownership:
            existing.ownership = entry.ownership
        if entry.owner:
            existing.owner = entry.owner
    write_related(anchor, cfg)
    return cfg


def remove_related(anchor: str | Path, name: str) -> bool:
    """Remove a related entry (and clear ``primary`` if it pointed here).

    Returns ``True`` if an entry was removed.
    """
    cfg = read_related(anchor)
    if name not in cfg.related:
        return False
    del cfg.related[name]
    if cfg.primary == name:
        cfg.primary = ""
    write_related(anchor, cfg)
    return True


# ---------------------------------------------------------------------------
# Ownership classification (derive once at registration; then authoritative)
# ---------------------------------------------------------------------------

def _operator_logins() -> set[str]:
    """The operator's own gh account logins (case-folded), from the accounts
    catalog. These are the namespaces the operator *wholly owns*.

    Reading the static ``accounts.yaml`` catalog is NOT "looking at live gh" --
    it is the recorded identity metadata. The one-time, at-registration
    consultation the ownership model calls for is this comparison; the result is
    baked into related.yaml so consumers never re-derive.
    """
    try:
        from . import accounts
        return {a.login.casefold() for a in accounts.list_accounts() if a.login}
    except Exception:
        return set()


def _is_ado_remote(remote: str) -> bool:
    """Whether a remote is an Azure DevOps / VSTS host (Microsoft-internal)."""
    import re
    r = (remote or "").lower()
    return bool(re.search(r"(\.visualstudio\.com|(^|//|@)[^/]*dev\.azure\.com)", r))


def classify_ownership(name: str) -> tuple[str, str]:
    """Best-effort derive ``(ownership, owner)`` for a registered repo ``name``.

    Consulted **once at registration** (and by ``related classify`` backfill);
    the result is persisted to related.yaml and thereafter authoritative. Only
    the confident cases are asserted -- ambiguous ones return ``("", ...)`` so a
    human curates rather than the tool guessing wrong:

    * gh owner is one of the operator's own account logins -> ``owned``.
    * an Azure DevOps host -> ``internal`` (Microsoft-internal; the operator can
      override to ``owned`` for a repo they wholly own, e.g. their own ADO
      plugin marketplace).
    * otherwise -> ``""`` (unclassified) -- e.g. a public github repo the
      operator merely accesses via an account; visibility/ownership can't be
      told from the remote, so leave it for the operator to set.

    ``owner`` is the resolving operator account login when derivable (else "").
    """
    from . import repos
    reg = repos.find_repo(name)
    remote = reg.remote if reg else ""
    owner_ns = repos.github_owner(remote)
    if owner_ns and owner_ns.casefold() in _operator_logins():
        return "owned", owner_ns
    if _is_ado_remote(remote):
        # Microsoft-internal by host; owner account (if any) via the org map.
        return "internal", (repos.resolve_account(reg) or "")
    return "", (repos.resolve_account(reg) or "")


def effective_ownership(entry: RelatedEntry) -> str:
    """The authoritative ownership for an entry: its explicit value if set,
    else the best-effort derivation from the registry. Never raises."""
    if entry.ownership:
        return entry.ownership
    try:
        return classify_ownership(entry.name)[0]
    except Exception:
        return ""


def owned_targets(anchor: str | Path) -> list[dict[str, str]]:
    """Return the related entries whose effective ownership is ``owned``, each as
    ``{"name", "remote", "slug", "ownership"}`` -- the wholly-owned targets a
    consumer (e.g. the AI-attribution hook) should treat as the operator's own
    space. ``slug`` is the ``owner/name`` for a github remote (else "")."""
    from . import repos
    out: list[dict[str, str]] = []
    for name, entry in sorted(read_related(anchor).related.items()):
        if effective_ownership(entry) != "owned":
            continue
        reg = repos.find_repo(name)
        remote = reg.remote if reg else ""
        owner_ns = repos.github_owner(remote) or ""
        slug = f"{owner_ns}/{name}" if owner_ns else ""
        out.append({"name": name, "remote": remote, "slug": slug,
                    "ownership": "owned"})
    return out


def classify_all(anchor: str | Path, *, overwrite: bool = False) -> list[dict[str, str]]:
    """Backfill ownership for every related entry from the derivation, persisting
    the result. By default only fills **unset** entries (leaving any explicit
    curation intact); ``overwrite=True`` re-derives all. Returns a per-entry
    changelog of ``{"name", "before", "after"}`` for entries that changed."""
    cfg = read_related(anchor)
    changed: list[dict[str, str]] = []
    for name, entry in cfg.related.items():
        if entry.ownership and not overwrite:
            continue
        derived, owner = classify_ownership(name)
        if not derived:
            continue
        before = entry.ownership
        if derived == before and (owner == entry.owner or not owner):
            continue
        entry.ownership = derived
        if owner and not entry.owner:
            entry.owner = owner
        changed.append({"name": name, "before": before or "(unset)",
                        "after": derived})
    if changed:
        write_related(anchor, cfg)
    return changed


# ---------------------------------------------------------------------------
# Narrative-doc scaffolding
# ---------------------------------------------------------------------------

_DOC_TEMPLATE = """\
# {name} — related repo

> Narrative for `{name}` **from this repo's point of view**. Resolve its local
> checkout with `agent-worktrees repos find {name}` — never hardcode a path
> (it varies by machine).

- **Role:** {role}
- **Registry:** `agent-worktrees repos find {name}` (class, remote, paths)
- **Work here via:** `agent-worktrees related resolve {name}`

## Why it matters here

{summary}

## How to make a change

_TODO: where work happens (locus: local / a machine via agent-bridge / a
CodeSpace via agent-codespaces), build/test commands, branch naming, how a PR is
opened, merge style._

## Rules & governing policies

_TODO: conventions, required checks, auth/credential needs, and do-nots._
"""


def scaffold_doc(
    anchor: str | Path, entry: RelatedEntry, *, force: bool = False
) -> tuple[Path, bool]:
    """Create the narrative doc for ``entry`` if missing.

    Returns ``(path, created)``.  An existing file is left untouched unless
    ``force`` is set.
    """
    path = doc_abs_path(anchor, entry)
    if path.exists() and not force:
        return (path, False)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _DOC_TEMPLATE.format(
        name=entry.name,
        role=entry.role or "_(unset)_",
        summary=entry.summary or "_(unset)_",
    )
    path.write_text(body, encoding="utf-8")
    return (path, True)


# ---------------------------------------------------------------------------
# Locus resolution -- "how do I work on this repo, from here, on this machine?"
# ---------------------------------------------------------------------------

def machine_matches(key: str, current: str) -> bool:
    """Loosely match a locus machine key against the current machine name.

    Locus keys are short (``dev6``); the detected machine is often the full
    hostname (``host-dev6``).  A key matches when it equals the current
    name, is its ``-``-suffix, or equals its last ``-``-segment
    (case-insensitive).
    """
    k = (key or "").strip().lower()
    c = (current or "").strip().lower()
    if not k or not c:
        return False
    return c == k or c.endswith("-" + k) or c.split("-")[-1] == k


@dataclass
class Resolution:
    """A plan for how to work on a related repo from the current machine."""

    name: str
    locus_kind: str = "local"        # local | machine | codespace | container
    target_machine: str = ""         # for the ``machine`` kind
    available_here: bool = True
    editing_model: str = ""          # read-only | anchor | worktree | worktree-unadopted
    delegate_via: str = ""           # agent-bridge | agent-codespaces | agent-containers | none
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _venue_machines(venue: dict[str, Any]) -> list[str]:
    """Machine keys a venue is restricted to (empty list = unrestricted)."""
    raw = venue.get("machines") if isinstance(venue, dict) else None
    if isinstance(raw, list):
        return [str(m).strip() for m in raw if str(m).strip()]
    if raw:
        return [str(raw).strip()]
    return []


def _venue_available_here(venue: dict[str, Any], current_machine: str) -> bool:
    """A venue with no ``machines`` is unrestricted; otherwise the current
    machine must match one of them."""
    ms = _venue_machines(venue)
    return (not ms) or any(machine_matches(m, current_machine) for m in ms)


def build_resolution(
    entry: RelatedEntry,
    *,
    current_machine: str,
    repo_class: str | None,
    repo_path: str | None,
    adopted: bool,
    base_repo: bool = False,
) -> Resolution:
    """Compute how to work on ``entry`` from the current machine.

    Pure planner -- the caller injects the current machine, the global-registry
    class/path, whether the repo is adopted (has a launch binstub), and whether
    it is adopted as a **base_repo** (an enlistment / no-worktree monorepo, from
    projects.yaml).  It emits a structured plan (kind, availability, editing
    model, delegation, concrete steps) but never executes anything.

    A ``worktree``-class repo adopted as a ``base_repo`` is edited **in place**
    in the anchor enlistment (one flow at a time), never via ``--new`` worktree
    isolation.  See #143.
    """
    name = entry.name
    kind, target = parse_preferred(entry.locus.preferred)
    if not kind:
        kind = "local"
    machines = entry.locus.machines

    res = Resolution(name=name, locus_kind=kind, target_machine=target,
                     delegate_via=entry.delegate)

    # Local editing model from the global registry class.  A ``worktree`` repo
    # adopted as a ``base_repo`` (enlistment monorepo) is edited in the anchor
    # in place -- ``anchor`` editing, not worktree isolation (#143).
    cls = (repo_class or "").lower()
    if cls == "reference":
        res.editing_model = "read-only"
    elif cls == "singleton":
        res.editing_model = "anchor"
    elif cls == "worktree":
        if base_repo:
            res.editing_model = "anchor"
        else:
            res.editing_model = "worktree" if adopted else "worktree-unadopted"
    else:
        res.editing_model = "unknown"

    def _local_edit_steps() -> list[str]:
        if cls == "reference":
            return [f"Read-only (reference). Resolve the path with "
                    f"`agent-worktrees repos find {name}`; do not edit it."]
        if cls == "singleton" or (cls == "worktree" and base_repo):
            loc = repo_path or f"(run `agent-worktrees repos find {name}`)"
            kindword = ("base_repo enlistment" if cls == "worktree"
                        else "singleton")
            return [f"Edit the anchor checkout directly at {loc} "
                    f"({kindword}: one flow at a time; no `--new` worktree)."]
        if cls == "worktree":
            if adopted:
                return [f"Create an isolated worktree **programmatically** "
                        f"(no mux, no session): `{name} create --json` -- start "
                        f"Copilot in the returned path (or `cd` in and edit in "
                        f"your current session), then `{name} push-changes` / "
                        f"`{name} finalize`. **Never `{name} --new`** from a tool "
                        f"call: it launches an interactive tmux/psmux session for "
                        f"a human at a terminal."]
            return [f"Adopt first: `agent-worktrees register {name}`, then "
                    f"`{name} create --json` (never `{name} --new` -- that is the "
                    f"interactive, human-only launch)."]
        return [f"Resolve the checkout with `agent-worktrees repos find {name}`."]

    if kind == "codespace":
        res.available_here = True  # CodeSpaces are driven from any machine
        cs = entry.locus.codespace or {}
        repo = cs.get("repo", "<codespace-repo>")
        mach = cs.get("machine", "")
        loc = cs.get("location", "")
        ws = cs.get("workspace_folder", "")
        create = f"gh cs create -R {repo}"
        if mach:
            create += f" -m {mach}"
        if loc:
            create += f" -l {loc}"
        res.steps = [
            f"Preferred locus is a CodeSpace (delegate via "
            f"{entry.delegate or 'agent-codespaces'}).",
            f"Provision/reuse: {create}",
            "Dispatch work: `agent-bridge send codespace:<name> \"<task>\"` "
            "(or `agent-codespaces ssh <name>`).",
        ]
        if ws:
            res.notes.append(f"Workspace checkout on the CodeSpace: {ws}.")
        # Surface the container alternative when this machine hosts the fleet.
        if entry.locus.container and _venue_available_here(
            entry.locus.container, current_machine
        ):
            res.notes.append(
                f"A local container fleet is also available here: "
                f"`agent-containers up {name}` then "
                f"`agent-bridge send container:<name> \"<task>\"`."
            )
        return res

    if kind == "container":
        ct = entry.locus.container or {}
        res.available_here = _venue_available_here(ct, current_machine)
        repo = ct.get("repo", "<container-repo>")
        ws = ct.get("workspace_folder", "")
        ct_machines = _venue_machines(ct)
        if res.available_here:
            res.steps = [
                f"Preferred locus is a local container fleet (delegate via "
                f"{entry.delegate or 'agent-containers'}).",
                f"Bring up/reuse the fleet (built from {repo}): "
                f"`agent-containers up {name}`.",
                "Dispatch work: `agent-bridge send container:<name> \"<task>\"`.",
            ]
            if ws:
                res.notes.append(f"Workspace checkout in the container: {ws}.")
        else:
            avail = ", ".join(ct_machines) if ct_machines else "(none configured)"
            via = entry.delegate or "agent-bridge"
            res.available_here = False
            res.notes.append(
                f"Container fleet only available on: {avail} "
                f"(you are on '{current_machine}')."
            )
            res.steps = [
                f"Delegate via {via} to a fleet host: "
                f"`agent-bridge send <machine> \"<task>\"`.",
            ]
            # CodeSpaces, if configured, are the machine-agnostic fallback.
            if entry.locus.codespace:
                cs_repo = entry.locus.codespace.get("repo", "<codespace-repo>")
                res.notes.append(
                    f"Or use the CodeSpace from any machine: "
                    f"`gh cs create -R {cs_repo}` then "
                    f"`agent-bridge send codespace:<name> \"<task>\"`."
                )
        return res

    if kind == "machine":
        res.available_here = machine_matches(target, current_machine)
        if res.available_here:
            res.steps = _local_edit_steps()
        else:
            via = entry.delegate or "agent-bridge"
            res.steps = [
                f"Preferred locus is machine '{target}' (you are on "
                f"'{current_machine}').",
                f"Delegate via {via}: `agent-bridge send {target} \"<task>\"`.",
            ]
        return res

    # kind == "local"
    if machines and not any(machine_matches(m, current_machine) for m in machines):
        res.available_here = False
        via = entry.delegate or "agent-bridge"
        res.notes.append(
            f"Not checked out on '{current_machine}'. Available on: "
            f"{', '.join(machines)}."
        )
        res.steps = [
            f"Delegate via {via} to one of [{', '.join(machines)}]: "
            f"`agent-bridge send <machine> \"<task>\"`.",
        ]
        return res

    res.available_here = True
    res.steps = _local_edit_steps()
    return res


# ---------------------------------------------------------------------------
# Doctor -- validate related.yaml against reality (report-first)
# ---------------------------------------------------------------------------
#
# related.yaml declares the *expected* relationship; the machine's own
# ``repos.yaml`` is the single owning store of *where a checkout actually lives*
# (the fabric's "locations-live-from-the-machine" / "derive-don't-duplicate"
# rule). The doctor verifies the declaration against that truth **on the current
# machine**, plus structural checks it can make anywhere (valid machine keys,
# venue repos present). It is a pure reporter: it never edits related.yaml. The
# headline case -- an entry that claims a local checkout on THIS machine that the
# machine's registry doesn't know -- is surfaced for the *agent* to resolve with
# the user (locate / provide a URL / clone / remove-with-approval), never
# auto-removed.

SEV_ERROR = "error"
SEV_WARNING = "warning"
SEV_INFO = "info"


@dataclass
class RelatedFinding:
    """One related.yaml validation finding (report-first; never auto-applied)."""

    name: str
    kind: str
    severity: str
    detail: str
    suggested_actions: list[str] = field(default_factory=list)
    # Best-effort checkout path the CLI located for a ``local_repo_unregistered``
    # finding (a dir named like the repo under a source root). Empty when none /
    # not searched. Lets the agent offer a concrete ``repos add`` without a hunt.
    candidate_path: str = ""


def _locus_here(locus: Locus, current_machine: str) -> tuple[str, bool, bool]:
    """Return ``(kind, expects_local_checkout, available_here)`` for a locus.

    ``expects_local_checkout`` is True only for the ``local`` / ``machine`` kinds
    (a CodeSpace/container is provisioned from its venue, not the machine's local
    registry, so a missing registry entry there is not a defect).
    """
    kind, target = parse_preferred(locus.preferred)
    if not kind:
        kind = "local"
    if kind == "codespace":
        return kind, False, True
    if kind == "container":
        return kind, False, _venue_available_here(locus.container, current_machine)
    if kind == "machine":
        return kind, True, machine_matches(target, current_machine)
    # local
    ms = locus.machines
    here = (not ms) or any(machine_matches(m, current_machine) for m in ms)
    return kind, True, here


def _referenced_machine_keys(locus: Locus) -> list[str]:
    """Every machine key a locus names (preferred ``machine:<k>``, ``machines``,
    and ``container.machines``), de-duplicated in first-seen order."""
    keys: list[str] = []
    kind, target = parse_preferred(locus.preferred)
    if kind == "machine" and target:
        keys.append(target)
    for m in locus.machines:
        keys.append(m)
    for m in _venue_machines(locus.container):
        keys.append(m)
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        kl = k.strip().lower()
        if kl and kl not in seen:
            seen.add(kl)
            out.append(k)
    return out


def diagnose_related(
    cfg: RelatedConfig,
    *,
    current_machine: str,
    machine_known: Any,          # Callable[[str], bool]
    machines_known_available: bool,
    registry_has: Any,           # Callable[[str], bool]
    registry_remote: Any = None,  # Callable[[str], str] | None
) -> list[RelatedFinding]:
    """Validate a related.yaml against reality (pure; dependency-injected).

    The caller injects the current machine, a ``machine_known`` predicate (does a
    locus machine key resolve in ``machines.yaml`` -- key/alias/hostname), a
    ``registry_has`` predicate (is the name in THIS machine's ``repos.yaml``), and
    an optional ``registry_remote`` (its remote URL, for clone hints). Returns a
    list of :class:`RelatedFinding`; never mutates anything.

    Findings:

    * ``unknown_machine`` (warning) -- a locus names a machine key absent from
      ``machines.yaml``. Skipped when ``machines_known_available`` is False.
    * ``codespace_missing_repo`` / ``container_missing_repo`` (error) -- a venue
      is declared with no ``repo`` to provision from.
    * ``local_repo_unregistered`` (warning) -- the entry claims a *local* checkout
      available on THIS machine, but the machine's ``repos.yaml`` has no entry:
      the file points at a repo the machine can't locate. The agent resolves it
      with the user (locate / URL / clone / remove-with-approval).
    * ``crossmachine_unverifiable`` (info) -- a local/machine entry that targets
      *other* machines only; its local registration can't be checked from here.
    * ``empty_locus`` (info) -- no locus at all; nowhere to resolve work.
    """
    remote_of = registry_remote if callable(registry_remote) else (lambda _n: "")
    findings: list[RelatedFinding] = []

    for name in sorted(cfg.related.keys()):
        entry = cfg.related[name]
        locus = entry.locus

        if locus.is_empty():
            findings.append(RelatedFinding(
                name=name, kind="empty_locus", severity=SEV_INFO,
                detail=f"'{name}' has no locus -- can't determine where to work.",
                suggested_actions=[
                    "set a locus (local | machine:<key> | codespace | container)",
                ],
            ))
            # No further locus-derived checks are meaningful.
            continue

        # 1) Unknown machine keys (structural; checkable anywhere machines.yaml is).
        if machines_known_available:
            for key in _referenced_machine_keys(locus):
                if not machine_known(key):
                    findings.append(RelatedFinding(
                        name=name, kind="unknown_machine", severity=SEV_WARNING,
                        detail=(f"'{name}' locus names machine '{key}', which is "
                                f"not in machines.yaml."),
                        suggested_actions=[
                            f"correct the machine key '{key}'",
                            f"add '{key}' to machines.yaml if it's a real machine",
                        ],
                    ))

        # 2) Venue declared without a repo to provision from.
        if locus.codespace and not str(locus.codespace.get("repo", "")).strip():
            findings.append(RelatedFinding(
                name=name, kind="codespace_missing_repo", severity=SEV_ERROR,
                detail=(f"'{name}' has a codespace locus but no codespace.repo "
                        f"to provision from."),
                suggested_actions=["set codespace.repo <org/repo>"],
            ))
        if locus.container and not str(locus.container.get("repo", "")).strip():
            findings.append(RelatedFinding(
                name=name, kind="container_missing_repo", severity=SEV_ERROR,
                detail=(f"'{name}' has a container locus but no container.repo "
                        f"to build the fleet from."),
                suggested_actions=["set container.repo <org/repo>"],
            ))

        # 3) Local-checkout claims vs the machine's own registry (the headline).
        kind, expects_local, available_here = _locus_here(locus, current_machine)
        if expects_local and not registry_has(name):
            if available_here:
                remote = str(remote_of(name) or "").strip()
                actions = [
                    f"locate the checkout and register it: `repos add {name} "
                    f"<path> --class <class>`",
                ]
                if remote:
                    actions.append(f"clone it fresh from {remote}, then `repos add`")
                else:
                    actions.append(
                        "provide a remote URL (no remote is known for this name)")
                actions.append(
                    f"remove the related entry `related remove {name}` "
                    f"(only with your approval)")
                findings.append(RelatedFinding(
                    name=name, kind="local_repo_unregistered",
                    severity=SEV_WARNING,
                    detail=(f"'{name}' is declared available locally on "
                            f"'{current_machine}', but this machine's repos.yaml "
                            f"has no entry -- it's likely checked out but never "
                            f"registered (or the entry is stale)."),
                    suggested_actions=actions,
                ))
            else:
                targets = ", ".join(locus.machines) or (
                    parse_preferred(locus.preferred)[1] or "(unspecified)")
                findings.append(RelatedFinding(
                    name=name, kind="crossmachine_unverifiable",
                    severity=SEV_INFO,
                    detail=(f"'{name}' targets other machine(s) [{targets}] and "
                            f"isn't registered here -- run `related doctor` on "
                            f"that machine to verify its local registration."),
                    suggested_actions=[],
                ))

    return findings
