#!/usr/bin/env python3
"""为 BIRD 数据库项目生成 README 节点。

依赖前面已经完成的静态/AI/explorer 摘要。
能力本体在 explorer.readme，这里只是一个薄入口。

Usage:
    python -m scripts.BIRD.generate_readme --db craftbeer --train
    python -m scripts.BIRD.generate_readme --db financial
"""
from __future__ import annotations

import argparse
import logging
import sys

from extractor.engine import get_registry, init_workspace, run_modules
from scripts.BIRD.common import get_db_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate README entity for one BIRD database project")
    parser.add_argument("--db", required=True, help="database name")
    parser.add_argument("--train", action="store_true", help="use train_databases instead of dev_databases")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    db_dir = get_db_dir(args.db, train=args.train)
    if not db_dir.exists():
        print(f"Error: database '{args.db}' not found at {db_dir}")
        sys.exit(1)

    registry = get_registry()
    if "agent_readme" not in registry:
        print("Error: agent_readme module not available")
        sys.exit(1)

    workspace, _ = init_workspace(str(db_dir), verbose=args.debug)
    run_modules(["agent_readme"], workspace)
    print(f"README entity generated for project: {db_dir.name}")


if __name__ == "__main__":
    main()
