#!/usr/bin/env python3
"""Pontis extractor CLI

Usage:
    # 运行完整 pipeline
    python -m extractor ./my_data
    python -m extractor ./my_data -v

    # 运行单个模块
    python -m extractor run db_column_stats ./my_data
    python -m extractor run db_column_stats,db_table_relations ./my_data -v

    # 列出可用模块
    python -m extractor list
"""
import argparse
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from storage.workspace import Workspace
from extractor.engine import get_registry, CONFIG_MODULES, run_pipeline, init_workspace


def _run_full(args):
    """运行完整 pipeline。"""
    from extractor.full_extract import extract
    extract(args.target, getattr(args, 'config', None), args.verbose)


def _run_modules(args):
    """运行指定的模块（逗号分隔）。"""
    registry = get_registry()
    names = [n.strip() for n in args.modules.split(',')]

    for name in names:
        if name not in registry:
            print(f"Error: unknown module '{name}'")
            print(f"Available: {', '.join(sorted(registry))}")
            sys.exit(1)

    workspace, config = init_workspace(args.target, getattr(args, 'config', None), args.verbose)

    logging.info(f"=== Running modules: {', '.join(names)} ===")
    run_pipeline(names, workspace, config)
    print("Done.")


def _list_modules(args):
    """列出可用模块。"""
    registry = get_registry()
    for name in sorted(registry):
        print(f"  {name}")
    print(f"\n{len(registry)} modules available")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('run', 'list'):
        parser = argparse.ArgumentParser(description="Pontis extractor")
        subparsers = parser.add_subparsers(dest='command')
        subparsers.add_parser('list', help='List available modules')
        run_parser = subparsers.add_parser('run', help='Run specified modules')
        run_parser.add_argument('modules', help='Module name(s), comma-separated')
        run_parser.add_argument('target', help='Directory to scan')
        run_parser.add_argument('-c', '--config', help='Config file path')
        run_parser.add_argument('-v', '--verbose', action='store_true')
        args = parser.parse_args()
        if args.command == 'list':
            _list_modules(args)
        elif args.command == 'run':
            _run_modules(args)
    else:
        # 完整 pipeline（兼容旧用法 python -m extractor ./my_data）
        parser = argparse.ArgumentParser(description="Pontis extractor")
        parser.add_argument('target', help='Directory to scan')
        parser.add_argument('-c', '--config', help='Config file path')
        parser.add_argument('-v', '--verbose', action='store_true')
        _run_full(parser.parse_args())


if __name__ == '__main__':
    main()
