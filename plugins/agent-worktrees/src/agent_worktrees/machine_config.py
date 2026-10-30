"""The machine-local project root as an ``.agent-*`` config overlay source.

A stateless harness keeps machine-specific entries out of its tree: its setup
writes them under the machine-local project root (``agent-worktrees get
config-dir``, e.g. ``~/.<project>/.agent-worktrees/related.yaml``). That root
joins :func:`state_root.config_source_anchors` between the launch repo and the
knowledge overlay, so a session in any checkout of the project resolves them.
"""
from __future__ import annotations

import os

from dropin_registry import ScanAuthority

from . import config as cfg
from .acquisition_report import AcquisitionReport, SourceObservation, failed, observe
from .source_probes import probe_source

_MARKERS = (
    ".agent-worktrees",  # marketplace-isolation: allow legacy-compatibility
    os.path.join(".copilot-extensions", "agent-worktrees"),
)


def machine_config_root(base: str | None, *, project: str | None = None) -> str | None:
    """The selected project's local config root; omitted project retains ambient lookup."""
    return _machine_config_root(base, project=project)


def _machine_config_root(base, *, project=None, observations=None):
    try:
        root = str(cfg.project_dir(project))
    except Exception as exc:  # noqa: BLE001 -- legacy omission remains unchanged
        failed(observations, "machine-config", "machine-source", "source-selection-failed", type(exc).__name__)
        return None
    if observations is None:
        return config_overlay_root(root, base)
    return _config_overlay_root(root, base, observations)


def config_overlay_root(root: str, base: str | None = None) -> str | None:
    """Classify an already-resolved project root using the normal overlay markers."""
    return _config_overlay_root(root, base)


def _config_overlay_root(root, base=None, observations=None):
    if base and os.path.abspath(root) == os.path.abspath(base):
        observe(observations, root, "machine-source", ScanAuthority.ABSENT)
        return None
    for marker in _MARKERS:
        candidate = os.path.join(root, marker)
        if observations is None:
            directory = os.path.isdir(candidate)
        else:
            directory = probe_source(candidate, "directory", observations, "machine-source-marker")
            if directory:
                observe(observations, candidate, "machine-source-marker", ScanAuthority.COMPLETE)
        if directory:
            observe(observations, root, "machine-source", ScanAuthority.COMPLETE)
            return root
    return None


def machine_config_root_report(
    base: str | None, *, project: str | None = None,
) -> AcquisitionReport[str | None]:
    """Select the normal overlay value while retaining lookup/marker uncertainty."""
    observations: list[SourceObservation] = []
    value = _machine_config_root(base, project=project, observations=observations)
    return AcquisitionReport("machine-config-root", value, tuple(observations))
