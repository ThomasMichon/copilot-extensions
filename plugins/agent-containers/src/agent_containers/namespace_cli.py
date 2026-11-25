"""Container namespace resolution at the Bridge process boundary."""

import argparse
import asyncio
import json
import sys

from .resolver import ContainerResolver, add_related_plugins


def resolve(args: argparse.Namespace) -> int:
    try:
        spec = asyncio.run(ContainerResolver().resolve_spec(args.name))
    except KeyError as exc:
        print(str(exc).strip("'"), file=sys.stderr)
        return 3  # Bridge translates not-found back to KeyError.
    print(json.dumps(add_related_plugins(spec, getattr(args, "stage_plugins", []))))
    return 0
