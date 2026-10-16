#!/usr/bin/env python3
"""Audit deterministic candidate inputs without resolving or installing packages."""
from bundle_lock import main

if __name__ == "__main__":
    raise SystemExit(main(["check"]))
