"""Producers -- the things that *create* tasks on a coordinator.

The coordinator core owns only the queue (enqueue/claim/transition/browse/
recover/emit). It deliberately runs **no** scheduler and **no** webhook/PR
logic (see the effort's non-goals). Producers live here, outside the core, as
opt-in modules driven by declarative specs:

* :mod:`agent_dispatch.producers.schedule` -- a scheduler/timer producer that
  turns a JSON schedule spec into ``create --not-before`` calls (idempotently,
  via a deterministic ``dedup_key`` per occurrence). Drive it one-shot from
  cron / a systemd timer / ``manage_schedule`` (``schedule tick``) or with the
  built-in loop (``schedule serve``).
* :mod:`agent_dispatch.producers.webhook` -- a reactive producer: a small
  HTTP app that maps generic git-forge **PR-merge** and **telemetry/alert**
  events onto tasks (stamping ``source`` / ``origin_ref``, deduped).

Both talk to the coordinator through the ordinary :class:`DispatchClient`, so
they need no privileged access -- a producer is just any client that can POST.
"""

from __future__ import annotations

__all__ = ["schedule", "webhook"]
