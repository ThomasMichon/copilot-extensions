"""Whole-file chunk batches shared by preparation and ordinary application."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from agent_index.indexing.work_counts import current_counts

if TYPE_CHECKING:
    from agent_index.chunking.base import Chunk
    from agent_index.index_config import ModelProfile
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.sources.base import FileEntry

logger = logging.getLogger(__name__)
Checkpoint = tuple[str, str, str, int]
ResumeIndex = dict[tuple[str, str], tuple[str | None, float]]


@dataclass
class FileBatch:
    chunks: list[Chunk]
    checkpoints: list[Checkpoint]


def file_batches(
    files: list[FileEntry], *, path_index: PathIndex,
    model_profiles: dict[str, ModelProfile] | None,
    stream_batch_size: int, force: bool, resume_since: float | None,
    chunker_revision: str, progress_cb: ProgressCallback | None = None,
    resume_index: ResumeIndex | None = None,
) -> Iterator[FileBatch]:
    """Keep every file's cleanup and checkpoint behind all its chunk writes."""
    from agent_index.chunking import get_chunker
    from agent_index.indexing.fingerprints import file_fingerprint
    from agent_index.store.content_mutations import coalesce_chunks

    if stream_batch_size < 1:
        raise ValueError("Embedding batch size must be positive")
    counts = current_counts()
    if resume_index is None:
        resume_index = path_index.get_entries([(entry.source, entry.path) for entry in files])
    if counts is not None:
        counts.checkpoint_lookup_keys += len(files)
        counts.checkpoint_rows_found += len(resume_index)
    batch = FileBatch([], [])
    for entry in files:
        if progress_cb:
            progress_cb.check_cancelled()
        fingerprint = file_fingerprint(entry, model_profiles, chunker_revision=chunker_revision)
        prior = resume_index.get((entry.source, entry.path))
        if prior is not None and prior[0] == fingerprint and (
            not force or (resume_since is not None and prior[1] >= resume_since)
        ):
            if counts is not None:
                counts.files_reused += 1
            continue
        chunker, _lang = get_chunker(entry.path)
        try:
            chunks = chunker.chunk(entry.content, entry.path, source=entry.source)
        except Exception:
            logger.warning("Failed to chunk %s", entry.path, exc_info=True)
            raise
        if getattr(entry, "metadata", None):
            chunks = [replace(chunk, metadata=entry.metadata) for chunk in chunks]
        chunks = coalesce_chunks(chunks)
        if counts is not None:
            counts.files_chunked += 1
            counts.chunks_generated += len(chunks)
        batch.chunks.extend(chunks)
        batch.checkpoints.append((entry.source, entry.path, fingerprint, len(chunks)))
        if len(batch.chunks) >= stream_batch_size:
            yield batch
            batch = FileBatch([], [])
    if batch.checkpoints:
        yield batch
