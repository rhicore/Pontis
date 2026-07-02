#!/usr/bin/env python3
"""BIRD-style entry point for Spider2-Snow extract."""

from pathlib import Path
import sys

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from scripts.spider.extract_spider2_snow import main


if __name__ == "__main__":
    raise SystemExit(main())
