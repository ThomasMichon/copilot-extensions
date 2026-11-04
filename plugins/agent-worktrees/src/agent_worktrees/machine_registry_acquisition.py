"""Original machine-registry acquisition, with optional per-call outcomes."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from dropin_registry import ScanAuthority
from machine_transport import merge_machines_yaml as _MERGE_OWNER
from machine_transport import normalize_machine_entries as _NORMALIZE_OWNER
from machine_transport import parse_machines_yaml_file as _PARSE_OWNER

from . import config_acquisition as acquisition
from .acquisition_report import AcquisitionReport, failed, observe


@contextlib.contextmanager
def _outcome(stage):
    start = acquisition.registry_call_started()
    try:
        yield
    except Exception as exc:
        failed(acquisition.current_observations(), stage, stage, f"{stage}-failed", type(exc).__name__)
        raise
    else:
        acquisition.owner_outcome(start, stage)


def _probe(path, method):
    observations = acquisition.current_observations()
    try:
        present = getattr(path, method)()
    except Exception as exc:
        failed(observations, str(path), "machine-registry-candidate", "unreadable-source", type(exc).__name__)
        raise
    if present:
        observe(observations, str(path), "machine-registry-candidate", ScanAuthority.COMPLETE)
    else:
        failed(observations, str(path), "machine-registry-candidate", "presence-provenance-unavailable",
               "legacy presence result does not expose a raw stat outcome")
    return present


def path(repo_dir, *, config=None):
    from . import config as cfg

    with _outcome("machine-registry-path"):
        canonical = Path(repo_dir) / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
        if _probe(canonical, "exists"):
            return canonical
        legacy = Path(repo_dir) / "machines.yaml"
        if _probe(legacy, "exists"):
            return legacy
        selected = acquisition.owner_call(
            cfg._overlay_machines_yaml_path, cfg._MACHINES_OVERLAY_OWNER,
            repo_dir, config=config, source="machine-registry-path",
        )
        return selected if selected is not None else canonical


def overlay(repo_dir, *, config=None):
    from . import config as cfg

    with _outcome("machine-registry-overlay"):
        try:
            from . import state_root

            if config is None:
                acquisition.opaque_source("machine-registry-config")
                config = cfg.load_config(include_control_plane_related_pr=False)
            try:
                default_anchor = config.default_repo.anchor
            except KeyError as exc:
                failed(acquisition.current_observations(), "machine-registry-overlay", "machine-registry-overlay",
                       "source-selection-failed", type(exc).__name__)
                return None
            if not default_anchor or os.path.abspath(default_anchor) != os.path.abspath(str(repo_dir)):
                return None
            from .config_sources import _CONFIG_SOURCE_ANCHORS_OWNER

            sources = acquisition.owner_call(
                state_root.config_source_anchors, _CONFIG_SOURCE_ANCHORS_OWNER,
                config, base_anchor=str(repo_dir), family="registry", source="machine-registry-config-sources",
            )
        except Exception as exc:
            failed(acquisition.current_observations(), "machine-registry-overlay", "machine-registry-overlay",
                   "source-selection-failed", type(exc).__name__)
            return None
        for source in sources:
            if os.path.abspath(source.anchor) == os.path.abspath(str(repo_dir)):
                continue
            candidate = Path(source.anchor) / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
            if _probe(candidate, "exists"):
                return candidate
            legacy = Path(source.anchor) / "machines.yaml"
            if _probe(legacy, "exists"):
                return legacy
        return None


def parse(path, *, require_alias=True):
    from . import config as cfg

    observations = acquisition.current_observations()
    acquisition.opaque_source("machine-registry-document")
    try:
        value = acquisition.owner_call(
            cfg._mt_parse_machines_yaml_file, _PARSE_OWNER, path,
            require_alias=require_alias, source="machine-registry-parser",
        )
    except Exception as exc:
        failed(observations, str(path), "machine-registry-parser", "parse-failed", type(exc).__name__)
        raise
    observe(observations, str(path), "machine-registry-parser", ScanAuthority.COMPLETE)
    return value


def load(repo_dir, *, config=None, require_alias=True):
    from . import config as cfg

    with _outcome("machine-registry"):
        canonical = Path(repo_dir) / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
        legacy = Path(repo_dir) / "machines.yaml"
        have_canonical, have_legacy = _probe(canonical, "is_file"), _probe(legacy, "is_file")
        if not have_canonical and not have_legacy:
            selected = acquisition.owner_call(
                cfg.machines_yaml_path, cfg._MACHINES_PATH_OWNER,
                repo_dir, config=config, source="machine-registry",
            )
            if not _probe(selected, "exists"):
                raise FileNotFoundError(f"Machine registry not found at {selected}")
            entries = acquisition.owner_call(
                cfg._parse_machines_yaml_file, cfg._MACHINES_PARSE_OWNER,
                selected, require_alias=False, source="machine-registry",
            )
        else:
            entries = acquisition.owner_call(
                cfg._mt_merge_machines_yaml, _MERGE_OWNER,
                acquisition.owner_call(
                    cfg._parse_machines_yaml_file, cfg._MACHINES_PARSE_OWNER,
                    legacy, require_alias=False, source="machine-registry",
                ) if have_legacy else None,
                acquisition.owner_call(
                    cfg._parse_machines_yaml_file, cfg._MACHINES_PARSE_OWNER,
                    canonical, require_alias=False, source="machine-registry",
                ) if have_canonical else None,
                source="machine-registry",
            )
        return {
            key: cfg.replace(entry, ssh_environments=[
                env for env in entry.ssh_environments if env.alias or not require_alias
            ])
            for key, entry in acquisition.owner_call(
                cfg._mt_normalize_machine_entries, _NORMALIZE_OWNER,
                entries, observed_hostname=cfg.socket.gethostname(), source="machine-registry",
            ).items()
        }


def load_machines_yaml_report(repo_dir, *, config=None, require_alias=True):
    """Report one original load; delegated document and input frontiers stay opaque."""
    from . import config as cfg

    with acquisition.capture_config_observations() as observations:
        value = acquisition.owner_call(
            cfg.load_machines_yaml, cfg._MACHINES_LOAD_OWNER,
            repo_dir, config=config, require_alias=require_alias, source="machine-registry",
        )
        acquisition.registry_call_finished(0, "machine-registry", stage="machine-registry")
    return AcquisitionReport("machine-registry", value, tuple(observations))
