"""Bounded session-start context derived from worktree registries."""

from __future__ import annotations

from typing import Any

from . import related, state_root


MAX_TOPOLOGY_BYTES = 1_250
MAX_RELATED_ENTRIES = 4


def _clean(value: object, fallback: str = "-") -> str:
    text = (
        str(value or "")
        .strip()
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("`", "'")
    )
    return text or fallback


def _bounded_prefix(prefix: str, required_suffix: str) -> str:
    suffix = (
        "\n[agent-worktrees context truncated]\n"
        f"{required_suffix}"
    )
    budget = MAX_TOPOLOGY_BYTES - len(suffix.encode("utf-8"))
    encoded = prefix.encode("utf-8")
    kept = encoded[: max(0, budget)].decode("utf-8", errors="ignore").rstrip()
    return kept + suffix


def _related_summary(
    config: Any,
    cwd: str,
    *,
    plugin_related_anchors: list[str] | None = None,
) -> tuple[str, str]:
    anchors = state_root.config_source_anchors(config, cwd=cwd)
    plugin_anchors = (
        related.installed_plugin_related_anchors()
        if plugin_related_anchors is None
        else plugin_related_anchors
    )
    topology = related.read_related_grafted(
        [*plugin_anchors, *[item.anchor for item in anchors]]
    )
    primary = _clean(topology.primary)
    entries = sorted(
        topology.related.values(),
        key=lambda item: (
            item.name != topology.primary,
            not (
                item.delegate
                and item.delegate != "none"
                or related.parse_preferred(item.locus.preferred)[0]
                not in {"", "local"}
            ),
            item.name,
        ),
    )
    important = [
        item
        for item in entries
        if item.name == topology.primary
        or (item.delegate and item.delegate != "none")
        or related.parse_preferred(item.locus.preferred)[0] not in {"", "local"}
    ][:MAX_RELATED_ENTRIES]
    rendered = []
    for item in important:
        locus = _clean(item.locus.preferred, "local")
        delegate = _clean(item.delegate, "none")
        rendered.append(
            f"{_clean(item.name)}(role={_clean(item.role)},"
            f"locus={locus},delegate={delegate})"
        )
    return primary, "; ".join(rendered)


def render_registry_context(
    config: Any,
    record: Any,
    *,
    cwd: str,
    pane_id: str | None = None,
    mux_session: str | None = None,
    plugin_related_anchors: list[str] | None = None,
) -> str:
    """Render current checkout, state pairing, and bounded related topology."""

    resolved_state = state_root.resolve_state_root(config, cwd=cwd)
    pair = state_root.resolve_pair(config, cwd=cwd)
    current_kind = _clean(
        getattr(pair.current, "kind", "") if pair.current else "",
        "worktree",
    )
    checkout_writable = (
        bool(getattr(record, "worktree_path", cwd))
        and current_kind == "worktree"
        and _clean(getattr(record, "status", ""), "unknown")
        in {"active", "ready"}
    )
    checkout = (
        "Checkout: "
        f"repo={_clean(getattr(record, 'repo', ''))}; "
        f"id={_clean(getattr(record, 'worktree_id', ''))}; "
        f"role={_clean(getattr(record, 'pair_role', ''), 'unpaired')}; "
        f"kind={current_kind}; "
        f"status={_clean(getattr(record, 'status', ''), 'unknown')}; "
        f"writable={'true' if checkout_writable else 'false'}; "
        "locus=local; delegate=none; "
        f"path={_clean(getattr(record, 'worktree_path', cwd))}."
    )
    if mux_session or pane_id:
        checkout += (
            f" Mux: session={_clean(mux_session)}; pane={_clean(pane_id)}."
        )

    state = (
        "State: "
        f"source={_clean(resolved_state.source)}; "
        f"repo={_clean(resolved_state.repo)}; "
        f"status={'ready' if resolved_state.bound and resolved_state.path else 'unavailable'}; "
        f"path={_clean(resolved_state.path)}."
    )
    if pair.paired and pair.sibling and not pair.error:
        sibling = pair.sibling
        writable = (
            _clean(sibling.role) == "knowledge"
            and bool(sibling.path)
            and _clean(sibling.kind, "worktree") == "worktree"
            and _clean(sibling.status, "unknown") in {"active", "ready"}
        )
        pair_label = (
            "KnowledgePair"
            if _clean(sibling.role) == "knowledge"
            else "Pair"
        )
        pairing = (
            f" {pair_label}: "
            f"role={_clean(sibling.role)}; "
            f"id={_clean(sibling.worktree_id)}; "
            f"kind={_clean(sibling.kind)}; "
            f"status={_clean(sibling.status, 'unknown')}; "
            f"writable={'true' if writable else 'false'}; "
            "locus=local; delegate=none; "
            f"path={_clean(sibling.path)}."
        )
    else:
        pairing = (
            " Pair: unavailable"
            f" ({_clean(pair.error, 'not paired')})."
        )
    state += pairing

    try:
        primary, entries = _related_summary(
            config,
            cwd,
            plugin_related_anchors=plugin_related_anchors,
        )
    except Exception:
        primary, entries = "-", ""
    related_line = f"Related: primary={primary}"
    if entries:
        related_line += f"; important={entries}"
    related_line += "."
    fresh = (
        "Fresh queries: `agent-worktrees state-root --pair --json`; "
        "`agent-worktrees related list`; "
        "`agent-worktrees related resolve <name>`."
    )

    required = "\n".join((checkout, state, fresh))
    candidate = "\n".join((checkout, state, related_line, fresh))
    if len(candidate.encode("utf-8")) <= MAX_TOPOLOGY_BYTES:
        return candidate
    if len(required.encode("utf-8")) <= MAX_TOPOLOGY_BYTES:
        return required
    return _bounded_prefix("\n".join((checkout, state)), fresh)
