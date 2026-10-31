"""Explicit project/workspace bindings for Configuration provider list queries."""

from .. import context
from ... import picker_review


def list_context(pivot, machine: object) -> dict:
    result = {"machine": "" if machine is None else str(machine)}
    if pivot.placement == "config" and any(
        token in argument
        for argument in pivot.list_cmd
        for token in ("{repo}", "{configuration_workspace}")
    ):
        binding = context.project_bootstrap()
        result.update(
            repo=context.project(),
            configuration_workspace=binding.workspace if binding and binding.workspace else "",
        )
    return result


def run_list(runtime, machine: object, generation: object = None) -> None:
    try:
        ctx = list_context(runtime.pivot, machine)
    except RuntimeError as exc:
        runtime._finish(machine, ("error", [], str(exc)), {})
        return
    if runtime.pivot.stream and not picker_review.active():
        runtime._run_list_stream(machine, ctx)
        return
    state, rows, error, summary = runtime._exec_list(ctx)
    with runtime._lock:
        if not runtime._generation_matches(machine, generation):
            return
        runtime._inflight.discard(machine)
        if state == "error" and not rows:
            rows = runtime._cache.get(machine, ("idle", [], ""))[1]
        runtime._cache[machine] = (state, rows, error)
        runtime._summaries[machine] = summary
