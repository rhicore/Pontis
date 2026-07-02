#!/usr/bin/env python3
"""Write Spider2-Snow project entries into Pontis/pontis.yml."""

from __future__ import annotations

from pathlib import Path
import sys

PONTIS_ROOT = Path(__file__).resolve().parents[2]
TEXT2SQL_ROOT = PONTIS_ROOT.parent
for _path in (PONTIS_ROOT, TEXT2SQL_ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from scripts.spider.common import iter_spider2_snow_db_ids, sync_spider2_snow_pontis_config


def main() -> int:
    path = sync_spider2_snow_pontis_config()
    print(f"Updated {path} with {len(iter_spider2_snow_db_ids())} Spider2-Snow projects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
