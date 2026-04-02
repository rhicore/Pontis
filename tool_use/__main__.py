#!/usr/bin/env python3
"""
Pontis VFS - CLI Tool for Exploring .pontis Metadata

A standalone CLI tool for navigating and querying .pontis shadow directories.
This tool is completely decoupled from the extraction process.
"""
import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tool_use.tools import ToolContext, ls, meta, search, find


def ls_command(args):
    """Execute ls command"""
    pontis_path = os.path.abspath(args.pontis)
    if not os.path.exists(pontis_path):
        print(f"Error: .pontis directory not found: {pontis_path}", file=sys.stderr)
        sys.exit(1)
    try:
        ctx = ToolContext(pontis_path)
        result = ls(args.path, ctx)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def meta_command(args):
    """Execute meta command"""
    pontis_path = os.path.abspath(args.pontis)
    if not os.path.exists(pontis_path):
        print(f"Error: .pontis directory not found: {pontis_path}", file=sys.stderr)
        sys.exit(1)
    try:
        ctx = ToolContext(pontis_path)
        result = meta(args.path, args.key, ctx)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def search_command(args):
    """Execute search command"""
    pontis_path = os.path.abspath(args.pontis)
    if not os.path.exists(pontis_path):
        print(f"Error: .pontis directory not found: {pontis_path}", file=sys.stderr)
        sys.exit(1)
    try:
        ctx = ToolContext(pontis_path)
        result = search(args.query, args.path, ctx)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def find_command(args):
    """Execute find command"""
    pontis_path = os.path.abspath(args.pontis)
    if not os.path.exists(pontis_path):
        print(f"Error: .pontis directory not found: {pontis_path}", file=sys.stderr)
        sys.exit(1)
    try:
        ctx = ToolContext(pontis_path)
        result = find(args.pattern, args.path, ctx)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cd(path: str, context: ToolContext) -> str:
    """Change directory"""
    import os
    resolved_path = context.resolve_path(path)
    full_path = os.path.join(context.vfs.pontis_root, resolved_path)
    if not os.path.exists(full_path):
        return f"Directory not found: {path}"
    if not os.path.isdir(full_path):
        return f"Not a directory: {path}"
    context.cwd = resolved_path
    return f"Changed to: /{resolved_path}" if resolved_path else "Changed to: /"


def pwd(context: ToolContext) -> str:
    """Print current working directory"""
    cwd = context.cwd
    return f"/{cwd}" if cwd else "/"


def shell_command(args):
    """Interactive shell for exploring .pontis"""
    pontis_path = os.path.abspath(args.pontis)
    if not os.path.exists(pontis_path):
        print(f"Error: .pontis directory not found: {pontis_path}", file=sys.stderr)
        sys.exit(1)

    ctx = ToolContext(pontis_path)

    print(f"Pontis VFS Shell")
    print(f"Connected to: {pontis_path}")
    print("Type 'help' for commands, 'exit' to quit\n")

    while True:
        try:
            cwd = ctx.cwd if ctx.cwd else "/"
            cmd = input(f"pontis:{cwd}> ").strip()

            if not cmd:
                continue

            if cmd in ("exit", "quit"):
                break

            if cmd == "help":
                print_shell_help()
                continue

            if cmd == "pwd":
                print(pwd(ctx))
                continue

            # Parse command
            parts = cmd.split()
            command = parts[0]
            arg = parts[1] if len(parts) > 1 else "."

            if command == "ls":
                print(ls(arg, ctx))
            elif command == "cd":
                result = cd(arg, ctx)
                if result:
                    print(result)
            elif command == "stat":
                print(stat(arg, ctx))
            elif command == "search":
                print(search(arg, ctx=ctx))
            elif command == "find":
                print(find(arg, ctx=ctx))
            else:
                print(f"Unknown command: {command}")

        except KeyboardInterrupt:
            print("\n")
            break
        except EOFError:
            print("\n")
            break
        except Exception as e:
            print(f"Error: {e}")


def print_shell_help():
    """Print shell help"""
    print("""
Available commands:
  ls [path]        - List directory contents
  cd [path]        - Change directory
  pwd              - Show current directory
  stat <path>      - Show detailed node information
  search <query>   - Search for nodes
  find <pattern>   - Find nodes by pattern (e.g., '*.db')
  help             - Show this help
  exit/quit        - Exit shell
""")


def main():
    """Main entry point for CLI tool"""
    parser = argparse.ArgumentParser(
        description="Pontis VFS - CLI tool for exploring .pontis metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List contents of .pontis directory
  python -m tool_use ls ./my_data/.pontis

  # Get metadata about a table
  python -m tool_use meta ./my_data/.pontis mydb.db/orders

  # Get specific metadata key
  python -m tool_use meta ./my_data/.pontis mydb.db/orders row_count

  # Search for columns related to "customer"
  python -m tool_use search ./my_data/.pontis "customer"

  # Find all database files
  python -m tool_use find ./my_data/.pontis "*.db"

  # Interactive shell
  python -m tool_use shell ./my_data/.pontis
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # LS command
    ls_parser = subparsers.add_parser('ls', help='List contents of .pontis directory')
    ls_parser.add_argument('pontis', help='Path to .pontis directory')
    ls_parser.add_argument('path', nargs='?', default='.', help='Path within .pontis to list')

    # META command
    meta_parser = subparsers.add_parser('meta', help='Get metadata of a node')
    meta_parser.add_argument('pontis', help='Path to .pontis directory')
    meta_parser.add_argument('path', help='Path to node within .pontis')
    meta_parser.add_argument('key', nargs='?', help='Specific metadata key to retrieve (optional)')

    # Search command
    search_parser = subparsers.add_parser('search', help='Search for nodes')
    search_parser.add_argument('pontis', help='Path to .pontis directory')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('-p', '--path', default='.', help='Starting path for search')

    # Find command
    find_parser = subparsers.add_parser('find', help='Find nodes by pattern')
    find_parser.add_argument('pontis', help='Path to .pontis directory')
    find_parser.add_argument('pattern', help='Glob pattern (e.g., "*.db")')
    find_parser.add_argument('-p', '--path', default='.', help='Starting path for search')

    # Shell command
    shell_parser = subparsers.add_parser('shell', help='Interactive shell for exploring .pontis')
    shell_parser.add_argument('pontis', help='Path to .pontis directory')

    args = parser.parse_args()

    if args.command == 'ls':
        ls_command(args)
    elif args.command == 'meta':
        meta_command(args)
    elif args.command == 'search':
        search_command(args)
    elif args.command == 'find':
        find_command(args)
    elif args.command == 'shell':
        shell_command(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
