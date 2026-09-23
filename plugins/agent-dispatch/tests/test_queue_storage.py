"""Import guard for the split-out storage / creation ``TaskQueue`` mixin.

Behavioral coverage already lives in ``test_queue.py`` and adjacent queue
integration suites. This file only guards direct importability, ``TaskQueue``
composition, compatibility re-exports, and runtime-resolvable annotations for
the extracted storage-facing mixin.
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import (
    AttachmentRecord,
    CreationOutcome,
    Task,
    TaskAttachmentEntry,
    TaskQueue,
    canonical_reviewer_target,
    is_blob_ref,
)
from agent_dispatch.queue_lifecycle import QueueLifecycleMixin
from agent_dispatch.queue_liveness import LivenessMixin
from agent_dispatch.queue_steering import QueueSteeringMixin
from agent_dispatch.queue_common import AttachmentRecord as _CommonAttachmentRecord
from agent_dispatch.queue_common import CreationOutcome as _CommonCreationOutcome
from agent_dispatch.queue_common import Task as _CommonTask
from agent_dispatch.queue_common import TaskAttachmentEntry as _CommonTaskAttachmentEntry
from agent_dispatch.queue_storage import QueueStorageMixin
from agent_dispatch.identity import canonical_reviewer_target as _IdentityReviewerTarget
from agent_dispatch.payload import is_blob_ref as _PayloadIsBlobRef


@pytest.mark.guard
def test_task_queue_inherits_the_storage_mixin():
    assert QueueStorageMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_queue_re_exports_match_storage_dependencies():
    assert AttachmentRecord is _CommonAttachmentRecord
    assert CreationOutcome is _CommonCreationOutcome
    assert Task is _CommonTask
    assert TaskAttachmentEntry is _CommonTaskAttachmentEntry
    assert canonical_reviewer_target is _IdentityReviewerTarget
    assert is_blob_ref is _PayloadIsBlobRef


@pytest.mark.guard
def test_storage_methods_are_directly_importable():
    assert callable(QueueStorageMixin._now)
    assert callable(QueueStorageMixin._canonical_repo)
    assert callable(QueueStorageMixin._canonical_selector_tokens)
    assert callable(QueueStorageMixin._audit)
    assert callable(QueueStorageMixin._record_attachment)
    assert callable(QueueStorageMixin.attachment_history)
    assert callable(QueueStorageMixin.tasks_for_session)
    assert callable(QueueStorageMixin._fetch)
    assert callable(QueueStorageMixin._enqueue_wake)
    assert callable(QueueStorageMixin._payload_needs_spill)
    assert callable(QueueStorageMixin._spill_committed_payload)
    assert callable(QueueStorageMixin.read_payload)
    assert callable(QueueStorageMixin._encode_result)
    assert callable(QueueStorageMixin.read_result)
    assert callable(QueueStorageMixin._legacy_reviewer_target)
    assert callable(QueueStorageMixin._reviewer_target)
    assert callable(QueueStorageMixin.create)
    assert callable(QueueStorageMixin.create_outcome)
    assert callable(QueueStorageMixin.propose)
    assert callable(QueueStorageMixin.propose_outcome)


@pytest.mark.guard
def test_storage_mixin_precedes_dependents_in_task_queue_mro():
    mro = TaskQueue.__mro__
    storage_idx = mro.index(QueueStorageMixin)
    assert storage_idx < mro.index(QueueLifecycleMixin)
    assert storage_idx < mro.index(LivenessMixin)
    assert storage_idx < mro.index(QueueSteeringMixin)


@pytest.mark.guard
def test_storage_annotations_resolve_via_get_type_hints():
    for name in (
        "_now",
        "_canonical_repo",
        "_canonical_selector_tokens",
        "_audit",
        "_record_attachment",
        "attachment_history",
        "tasks_for_session",
        "_fetch",
        "_enqueue_wake",
        "_payload_needs_spill",
        "_spill_committed_payload",
        "read_payload",
        "_encode_result",
        "read_result",
        "_legacy_reviewer_target",
        "_reviewer_target",
        "create",
        "create_outcome",
        "propose",
        "propose_outcome",
    ):
        typing.get_type_hints(getattr(QueueStorageMixin, name))


def test_storage_mixin_methods_work_end_to_end(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    created = q.create_outcome(
        "store payload",
        repo="owner/repo",
        payload_inline="payload body",
    )
    assert isinstance(created, CreationOutcome)
    assert q.read_payload(created.task.id) == "payload body"

    q.claim_one("worker-1", task_id=created.task.id, repo="owner/repo")
    q.start(created.task.id, "worker-1")
    q.bind_owner_session(created.task.id, "worker-1", "session-1")

    history = q.attachment_history(created.task.id)
    reverse = q.tasks_for_session("session-1")
    assert history and isinstance(history[0], AttachmentRecord)
    assert reverse and isinstance(reverse[0], TaskAttachmentEntry)
