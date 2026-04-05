#!/usr/bin/env python3
"""Pontis metadata extractor CLI

Usage:
    python -m extractor ./my_data
    python -m extractor ./my_data -v
"""
import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from extractor import extract


def main():
    parser = argparse.ArgumentParser(description="Pontis metadata extractor")
    parser.add_argument('target', help='Directory to scan')
    parser.add_argument('-c', '--config', help='Config file path')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')

    args = parser.parse_args()
    extract(args.target, args.config, args.verbose)


if __name__ == '__main__':
    main()
