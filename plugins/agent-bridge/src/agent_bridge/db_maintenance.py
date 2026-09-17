"""Database maintenance helpers."""

from __future__ import annotations


class _MaintenanceMixin:
    """Garbage collection and size-management helpers."""

    def gc_eligible_session_ids(
        self, statuses: list[str], cutoff_ts: float
    ) -> list[str]:
        """Return ids of sessions in ``statuses`` last updated before ``cutoff_ts``.

        Used by the GC sweep to find terminal/disconnected sessions whose
        relay metadata is past the retention window.
        """
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.execute_read(
            f"SELECT id FROM sessions WHERE status IN ({placeholders}) "
            "AND updated_at < ? ORDER BY updated_at",
            (*statuses, cutoff_ts),
        )
        return [r["id"] for r in rows]

    def gc_status_summary(self, statuses: list[str]) -> dict[str, float | int | None]:
        """Summarize the retained population for the configured GC statuses."""
        if not statuses:
            return {
                "count": 0,
                "oldest_updated_at": None,
                "newest_updated_at": None,
            }
        placeholders = ",".join("?" for _ in statuses)
        rows = self.execute_read(
            f"SELECT COUNT(*) AS n, MIN(updated_at) AS oldest_updated_at, "
            f"MAX(updated_at) AS newest_updated_at FROM sessions "
            f"WHERE status IN ({placeholders})",
            tuple(statuses),
        )
        row = rows[0] if rows else None
        return {
            "count": int(row["n"]) if row and row["n"] is not None else 0,
            "oldest_updated_at": row["oldest_updated_at"] if row else None,
            "newest_updated_at": row["newest_updated_at"] if row else None,
        }

    def db_size_info(self) -> dict[str, int]:
        """Return page/byte stats for the DB file (drives the VACUUM decision)."""
        conn = self._get_conn()
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        return {
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist,
            "total_bytes": page_size * page_count,
            "free_bytes": page_size * freelist,
        }

    def vacuum(self) -> None:
        """Checkpoint the WAL and VACUUM, returning freed pages to the OS.

        Runs under the write lock so no write interleaves. VACUUM rewrites
        the file; the scratch space it needs is ~the *live* content size
        (tiny when the DB is mostly freelist), so it succeeds even on a
        nearly-full disk. VACUUM cannot run inside an open transaction, so we
        commit any pending one first.
        """
        self.flush()
        conn = self._get_conn()
        with self._write_lock:
            if conn.in_transaction:
                conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
