#!/usr/bin/env python3
"""
Pontis VFS - Metadata Extraction Tool

A standalone tool for extracting metadata from various data sources
and creating .pontis shadow directories.
"""
import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from extractor.config import ExtractorConfig, load_config
from extractor.engine import ModularEngine


def setup_logging(log_level: str = "INFO", log_file: str = None):
    """Setup logging configuration"""
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


def print_summary(target_path: str, config: ExtractorConfig):
    """Print summary of extracted metadata"""
    pontis_path = os.path.join(target_path, config.pontis_dir_name)
    if not os.path.exists(pontis_path):
        print(f"No .pontis directory found at: {pontis_path}")
        return

    print("\n" + "="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)

    # Count nodes by type
    type_counts = {}
    total_nodes = 0

    for root, dirs, files in os.walk(pontis_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            if file == config.meta_filename:
                total_nodes += 1
                meta_path = os.path.join(root, file)
                try:
                    import yaml
                    with open(meta_path, 'r') as f:
                        meta = yaml.safe_load(f)
                    node_type = meta.get('type', 'Unknown')
                    type_counts[node_type] = type_counts.get(node_type, 0) + 1
                except Exception:
                    pass

    print(f"\nTotal nodes extracted: {total_nodes}")
    print("\nBy type:")
    for node_type, count in sorted(type_counts.items()):
        print(f"  {node_type}: {count}")


def main():
    """Main entry point for metadata extraction"""
    parser = argparse.ArgumentParser(
        description="Pontis VFS - Extract metadata from data sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract metadata from a directory
  python -m extractor ./my_data

  # Extract with verbose output
  python -m extractor ./my_data -v

  # Extract with custom config
  python -m extractor ./my_data -c pontis.yml
        """
    )

    parser.add_argument(
        'target',
        help='Directory to scan and extract metadata from'
    )
    parser.add_argument(
        '-c', '--config',
        help='Path to config file (optional)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose (debug) output'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output directory for .pontis (default: same as target)'
    )

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)
    if args.verbose:
        config.log_level = "DEBUG"

    setup_logging(config.log_level, config.log_file)

    # Validate target path
    target_path = os.path.abspath(args.target)
    if not os.path.exists(target_path):
        print(f"Error: Target path does not exist: {target_path}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(target_path):
        print(f"Error: Target is not a directory: {target_path}", file=sys.stderr)
        sys.exit(1)

    # Create engine and run extraction
    engine = ModularEngine(config)

    print(f"Extracting metadata from: {target_path}")
    pontis_path = engine.extract(target_path)

    print(f"\nExtraction complete!")
    print(f"Metadata saved to: {pontis_path}")

    # Print summary
    print_summary(target_path, config)


if __name__ == '__main__':
    main()
