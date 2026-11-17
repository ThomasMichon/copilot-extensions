"""Navigation-band rendering for operational machines and Config sections."""

from rich.text import Text
from machine_transport import split_machine_environment

from .engine_helpers import C_DIM, C_HINT


class PickerNavigationRenderingMixin:
    def tab_bar(self, width, focused):
        if self._in_configuration():
            return self._configuration_tabs(width, focused)
        # Group consecutive flat entries by machine (the 'All' entry stands
        # alone) so a multi-env machine renders its name once.
        groups = []
        prev = None
        for i, (label, m, e, ok) in enumerate(self.machines):
            tab = self.source_tabs[i] if self.source_tabs else {}
            group_key = (
                (tab.get("physical_machine") or split_machine_environment(m).machine).casefold()
                if tab.get("source_kind") in (None, "machine-ssh")
                else tab.get("source_id")
            )
            if label != "All" and group_key == prev:
                groups[-1].append(i)
            else:
                groups.append([i])
            prev = group_key if label != "All" else None
        blocks = [self._machine_group(g, focused) for g in groups]
        blen = [b.cell_len for b in blocks]
        ng = len(groups)
        selg = next((gi for gi, g in enumerate(groups) if self.machine_idx in g), 0)
        gap = 3
        budget = width - 6   # reserve for overflow markers
        lo = hi = selg
        used = blen[selg]
        while True:
            grew = False
            if hi + 1 < ng and used + gap + blen[hi + 1] <= budget:
                hi += 1
                used += gap + blen[hi]
                grew = True
            if lo - 1 >= 0 and used + gap + blen[lo - 1] <= budget:
                lo -= 1
                used += gap + blen[lo]
                grew = True
            if not grew:
                break
        t = Text(" ")
        t.append("\u2039 " if lo > 0 else "  ", style=C_HINT)
        for k in range(lo, hi + 1):
            if k > lo:
                t.append(" " * gap)
            t.append_text(blocks[k])
        t.append(" \u203a" if hi < ng - 1 else "  ", style=C_HINT)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def _stats_row(self, W):
        """Section hint and counts, with a compact fallback when narrow."""
        hint = ("Section  Ctrl \u25c0\u25b6" if self._in_configuration()
                else "Machine  Ctrl \u25c0\u25b6")
        use = self.status_text(False)
        if 1 + len(hint) + 3 + use.cell_len + 1 > W:
            use = self.status_text(True)
        t = Text(" ")
        t.append(hint, style=C_DIM)
        gap = W - t.cell_len - use.cell_len - 1
        t.append(" " * max(1, gap))
        t.append_text(use)
        t.append(" " * max(0, W - t.cell_len))
        return t
