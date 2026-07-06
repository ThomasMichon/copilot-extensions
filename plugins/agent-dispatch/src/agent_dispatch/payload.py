"""Content-addressed payload blob store.

A task's Markdown ``payload`` is small enough to live *inline* in the row for
most handoffs, but a graduated handoff can carry a large asset. Rather than bloat
the queue DB (and every ``list``/``find`` result) with kilobytes of prose, a
payload over a threshold is spilled to a **content-addressed blob**: the content
is hashed (SHA-256), written once to ``<root>/<hash>.md``, and the row keeps only
a ``blob:<hash>`` reference.

Content-addressing is deliberate: identical payloads share one file (free
dedup), writes are idempotent (re-storing the same content is a no-op), and a
ref is a stable, verifiable name for its bytes. The store is a plain directory
with no external dependencies, so it works the same on a lone dev box and a
facility coordinator host.

The store is a *server-side* concern -- the coordinator (the single writer) owns
the blob directory alongside its queue DB; clients send/receive payload content
over HTTP and never touch the directory directly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

BLOB_PREFIX = "blob:"


def is_blob_ref(ref: str | None) -> bool:
    """True if ``ref`` names a blob managed by a :class:`PayloadStore`."""
    return bool(ref) and ref.startswith(BLOB_PREFIX)  # type: ignore[union-attr]


class PayloadStore:
    """A content-addressed store of Markdown payload blobs under one directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path_for(self, digest: str) -> Path:
        return self.root / f"{digest}.md"

    def put(self, content: str) -> str:
        """Store ``content`` and return its ``blob:<sha256>`` reference.

        Idempotent: identical content hashes to the same file, so re-storing is a
        no-op and always yields the same reference.
        """
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        path = self._path_for(digest)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            # Write via a temp file + atomic rename so a reader never sees a
            # half-written blob.
            tmp = path.with_suffix(f".md.{digest[:8]}.tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        return f"{BLOB_PREFIX}{digest}"

    def has(self, ref: str) -> bool:
        """True if ``ref`` is a blob ref this store holds."""
        if not is_blob_ref(ref):
            return False
        return self._path_for(ref[len(BLOB_PREFIX) :]).exists()

    def get(self, ref: str) -> str:
        """Read the content behind a ``blob:<sha256>`` reference.

        Raises ``KeyError`` if the ref isn't a blob ref or the blob is missing.
        """
        if not is_blob_ref(ref):
            raise KeyError(f"not a blob ref: {ref!r}")
        path = self._path_for(ref[len(BLOB_PREFIX) :])
        if not path.exists():
            raise KeyError(f"no such payload blob: {ref}")
        return path.read_text(encoding="utf-8")
