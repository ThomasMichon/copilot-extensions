"""Scrollable worktree detail card with background-only claim inspection."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from . import derive
from .engine_helpers import C_DIM, C_WARN
from .worktree_assets import claim_coverage, needs_descendant_details


class WtDetailsScreen(ModalScreen[None]):
    """Open from current identity/counts; inspect a full ledger only on demand."""

    CSS = """
    WtDetailsScreen { align: center middle; background: $background 55%; }
    WtDetailsScreen > #details-frame {
        width: 84; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    WtDetailsScreen #details-body { height: auto; max-height: 100%; }
    WtDetailsScreen #details-foot { color: grey; height: 1; padding: 1 0 0 0; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, rec, *, engine=None) -> None:
        super().__init__()
        self._rec = rec
        self._engine = engine
        assets = rec.get("asset_hints") or {}
        self._details = list(assets.get("details") or [])
        self._loading = bool(assets.get("details_available")) or needs_descendant_details(
            rec.get("raw") or rec,
        )
        self._error = None

    def on_mount(self) -> None:
        if not self._loading:
            return
        if self._engine is None:
            self._loading = False
            self._error = "Full claim inspection is unavailable in this viewer."
            self.query_one("#details-content", Static).update(self._body())
            return

        def done(result):
            if not self.is_mounted:
                return
            self._loading = False
            if result is None:
                self._error = "Full claim inspection failed."
            else:
                self._details = result
            self.query_one("#details-content", Static).update(self._body())

        self._engine._run_bg(
            "Loading held claims",
            lambda: self._engine._load_worktree_claim_details(self._rec), done, quiet=True,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="details-frame"):
            with VerticalScroll(id="details-body"):
                yield Static(self._body(), id="details-content")
            yield Static(" Esc/Enter: close", id="details-foot")

    @staticmethod
    def _asset_label(claim) -> str:
        ref, note = (claim.get("ref") or "").strip(), (claim.get("note") or "").strip()
        return f"{ref} \u2014 {note}" if ref and note else ref or note or "(unlabeled)"

    def _body(self) -> Text:
        rec = self._rec
        raw = rec.get("raw") or {}
        text = Text()
        text.append(f"{rec.get('title') or '(untitled)'}\n", style="bold")
        path = raw.get("path") or raw.get("worktree_path") or ""
        if path:
            text.append(f"path    {path}\n", style=C_DIM)
        text.append(f"id      {raw.get('id') or rec.get('id4') or ''}\n", style=C_DIM)
        text.append(
            f"status  {rec.get('state')} \u00b7 {rec.get('machine')} \u00b7 "
            f"{rec.get('env')}\n", style=C_DIM,
        )
        text.append(
            f"age {rec.get('age')} \u00b7 sess {rec.get('sess')} \u00b7 "
            f"turns {rec.get('turns')} \u00b7 PR {rec.get('pr')}\n", style=C_DIM,
        )
        markers = (rec.get("status_markers") or "").strip()
        if markers:
            text.append("\nstatus markers:\n", style="bold")
            for value, is_warn in derive.status_marker_segments(markers, 999):
                text.append(value, style=C_WARN if is_warn else C_DIM)
            text.append("\n")
        relation = (rec.get("reciprocal_relation") or {}).get("state")
        if relation and relation != "unbound":
            text.append(f"\nrelation  {relation}\n", style=C_DIM)
        coverage = claim_coverage(rec)
        if coverage:
            text.append(
                "\n Claim coverage: selected worktree's last loaded projection; "
                "freshness/completeness may be unknown. Read-only details do not "
                "verify resource liveness. Unreachable does not mean stopped or absent.\n",
                style=C_DIM,
            )
        if coverage or rec.get("claims_links") or rec.get("claims_summary"):
            from .engine_helpers import _claims_cell

            text.append("\nclaim preview:\n", style="bold")
            text.append(_claims_cell(rec, 0))
            text.append("\n")
        sid = rec.get("last_session_id")
        if sid:
            if rec.get("mux_live") and rec.get("session_bare_orphan"):
                lock = " \u00b7 \u26a0 inconsistent (mux + stray orphan \u2014 Repair fixes it)"
            elif rec.get("session_bare_orphan"):
                lock = " \u00b7 bound (\u26a0 bare orphan \u2014 Reclaim frees it)"
            elif rec.get("session_lock_live") and not rec.get("mux_live"):
                lock = " \u00b7 bound (lock live \u2014 Reclaim frees it)"
            elif rec.get("session_lock_live"):
                lock = " \u00b7 bound (lock live)"
            elif rec.get("session_lock_stale") and not rec.get("mux_live"):
                lock = " \u00b7 stale lock (residue \u2014 Reclaim clears it)"
            else:
                lock = ""
            text.append(f"\nsession   {sid}{lock}\n", style=C_DIM)
        if self._loading:
            count = (rec.get("asset_hints") or {}).get("held_count", "unknown")
            text.append(f"\nheld claims ({count}): loading full details...\n", style=C_DIM)
        elif self._error:
            text.append(f"\nheld claims: {self._error}\n", style=C_WARN)
        elif self._details:
            heading = "held claims"
            if any(claim.get("state") == "unresolved" for claim in self._details):
                heading += " and unresolved edges"
            text.append(f"\n{heading} ({len(self._details)}):\n", style="bold")
            for claim in self._details:
                owner = " -> ".join(claim.get("path") or [])
                if owner:
                    text.append(f"  owner {owner}\n", style=C_DIM)
                url = claim.get("url") or claim.get("ref") or ""
                text.append(
                    f"  {claim.get('kind', 'resource')} [{claim.get('state') or 'active'}]"
                    f": {self._asset_label(claim)}\n",
                    style=C_WARN if claim.get("state") == "unresolved" else (
                        f"underline link {url}" if url.startswith(("https://", "http://"))
                        else C_DIM
                    ),
                )
        else:
            text.append("\nheld claims: none\n", style=C_DIM)
        text.append(
            "\nUse the Messages action (from the Actions menu) to peek this "
            "worktree's recent conversation.", style=C_DIM,
        )
        return text

    def action_close(self) -> None:
        self.dismiss(None)
