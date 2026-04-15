#!/usr/bin/env python3
"""Pontis extractor CLI

Usage:
    # 运行完整 pipeline
    python -m extractor ./my_data
    python -m extractor ./my_data -v

    # 运行单个模块
    python -m extractor run db_column_stats ./my_data
    python -m extractor run skeleton ./my_data -v

    # 列出可用模块
    python -m extractor list
"""
import argparse
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from storage import Store
from extractor.modules._utils import load_config
from extractor.registry import _get_registry, _CONFIG_MODULES


def _run_full(args):
    """运行完整 pipeline。"""
    from extractor.registry import extract
    extract(args.target, getattr(args, 'config', None), args.verbose)


def _run_module(args):
    """运行单个模块。"""
    registry = _get_registry()
    name = args.module

    if name not in registry:
        print(f"Error: unknown module '{name}'")
        print(f"Available: {', '.join(sorted(registry))}")
        sys.exit(1)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level,
                        format='%(message)s' if not args.verbose else '%(levelname)s: %(message)s')

    target_path = Path(args.target).resolve()
    if not target_path.exists():
        print(f"Error: {target_path} does not exist", file=sys.stderr)
        sys.exit(1)

    config = load_config(getattr(args, 'config', None))
    store = Store(str(target_path))
    func = registry[name]

    logging.info(f"=== Running module: {name} ===")

    try:
        if name in _CONFIG_MODULES:
            func(store, config=config)
        else:
            func(store)
    except Exception as e:
        logging.error(f"Module {name} failed: {e}")
        sys.exit(1)

    print("Done.")


def _list_modules(args):
    """列出可用模块。"""
    registry = _get_registry()
    for name in sorted(registry):
        print(f"  {name}")
    print(f"\n{len(registry)} modules available")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('run', 'list'):
        parser = argparse.ArgumentParser(description="Pontis extractor")
        subparsers = parser.add_subparsers(dest='command')
        subparsers.add_parser('list', help='List available modules')
        run_parser = subparsers.add_parser('run', help='Run a single module')
        run_parser.add_argument('module', help='Module name')
        run_parser.add_argument('target', help='Directory to scan')
        run_parser.add_argument('-c', '--config', help='Config file path')
        run_parser.add_argument('-v', '--verbose', action='store_true')
        args = parser.parse_args()
        if args.command == 'list':
            _list_modules(args)
        elif args.command == 'run':
            _run_module(args)
    else:
        # 完整 pipeline（兼容旧用法 python -m extractor ./my_data）
        parser = argparse.ArgumentParser(description="Pontis extractor")
        parser.add_argument('target', help='Directory to scan')
        parser.add_argument('-c', '--config', help='Config file path')
        parser.add_argument('-v', '--verbose', action='store_true')
        _run_full(parser.parse_args())


if __name__ == '__main__':
    main()
