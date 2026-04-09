#!/usr/bin/env python3
"""
Pontis CLI - Interactive Data Analysis Agent

Usage:
    pontis <folder>         # Open agent chat for a project folder
    pontis shell <folder>   # Open legacy VFS shell (debug)
"""
import os
import sys
import argparse


def main():
    parser = argparse.ArgumentParser(
        description="Pontis - Interactive Data Analysis Agent",
    )
    parser.add_argument("target", nargs="?", help="Project folder to analyze")
    parser.add_argument("--shell", action="store_true", help="Open legacy VFS shell")
    args = parser.parse_args()

    if not args.target:
        parser.print_help()
        sys.exit(1)

    project_path = os.path.abspath(args.target)
    if not os.path.isdir(project_path):
        print(f"Error: {project_path} is not a directory")
        sys.exit(1)

    pontis_path = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_path):
        print(f"No .pontis found in {project_path}")
        print("Run extractor first: python -m extractor.extract <folder>")
        sys.exit(1)

    # Add project root to path
    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)

    if args.shell:
        from pontis_shell import PontisShell
        shell = PontisShell(project_path)
        shell.run()
    else:
        from agent.agent import PontisAgent
        agent = PontisAgent(project_path)
        agent.run()


if __name__ == "__main__":
    main()
