"""Static native command-hook timeout checks; never execute hook code.

The behavioral contract is reviewed separately in the authoring reference.
Missing declarations remain advisory for legacy hosts. These offline findings
are not runtime authorization decisions or proof of a hook's actual latency.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from scan_plugin_sources import PluginSource, _plugin_hook_files
from scan_session_context import _editable_plugin_footprint


def _hook_files(root: Path, sources: list[PluginSource]):
    """Yield owned documents first, then selected plugin documents once."""
    owned = [root / 'hooks.json']
    owned.extend(sorted((root / '.github' / 'hooks').glob('*.json')))
    owned.extend(root / path for path in (
        '.github/copilot/settings.json', '.github/copilot/settings.local.json',
        '.claude/settings.json', '.claude/settings.local.json',
    ))
    for payload in sorted((root / 'plugins').glob('*')):
        if payload.is_dir():
            owned.extend(sorted(_plugin_hook_files(payload)))
    seen: set[Path] = set()
    candidates = [(path, True, None) for path in owned]
    for source in sorted(sources, key=lambda source: not source.controlled):
        if (not source.controlled
                and _editable_plugin_footprint(root, source) != source.payload_root):
            continue
        candidates.extend((path, source.controlled, source)
                          for path in sorted(_plugin_hook_files(source.payload_root)))
    for path, controlled, source in candidates:
        resolved = path.resolve()
        if resolved not in seen and path.is_file():
            seen.add(resolved)
            yield path, controlled, source


def scan_hooks(root: Path, report, plugin_sources: list[PluginSource] | None = None):
    """Report invalid explicit native command timeouts without host side effects."""
    for path, controlled, source in _hook_files(root, plugin_sources or []):
        owner = (f'plugin {source.origin} version {source.version or "unknown"}'
                 if source else 'repository')
        if source and source.source:
            owner += f' (source: {source.source})'
        try:
            document = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, ValueError):
            report.add('warning', 'hook-timeout-unavailable', path,
                       f'{owner}: hook declarations could not be read as JSON; '
                       'inspect this file before retrying static review. '
                       'No hook was executed; runtime behavior is unverified.')
            continue
        hooks = document.get('hooks') if isinstance(document, dict) else None
        if not isinstance(hooks, dict):
            continue
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or entry.get('type', 'command') != 'command':
                    continue
                # Compatibility containers and timeout fields have a different contract.
                if 'hooks' in entry or ('timeout' in entry and 'timeoutSec' not in entry):
                    continue
                location = f'{str(event)[:80]}[{index}]'
                if 'timeoutSec' not in entry:
                    report.add('warning', 'hook-timeout-missing', path,
                               f'{owner}: {location} omits native timeoutSec; '
                               'legacy host default is retained. Declare an explicit '
                               'finite positive number and review inner/cleanup budgets. '
                               'No hook was executed; static review is safe to retry.')
                    continue
                timeout = entry['timeoutSec']
                valid = (not isinstance(timeout, bool)
                         and isinstance(timeout, (int, float)) and timeout > 0
                         and (not isinstance(timeout, float) or math.isfinite(timeout)))
                if not valid:
                    report.add('blocking' if controlled else 'warning',
                               'hook-timeout-invalid', path,
                               f'{owner}: {location} timeoutSec must be a finite positive '
                               'number (not a boolean). Correct the declaration and '
                               'retry static review; no hook was executed. '
                               'This finding does not change runtime allow/deny policy.')
