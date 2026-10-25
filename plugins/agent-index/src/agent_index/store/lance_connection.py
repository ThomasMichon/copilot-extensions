"""Content-store connection policy for independent API readers and writers."""

from datetime import timedelta


def connect_content_store(path, *, source_aware: bool):
    import lancedb

    # Source-aware stores fence checkout_latest through their domain already.
    options = {} if source_aware else {"read_consistency_interval": timedelta(0)}
    return lancedb.connect(str(path), **options)
