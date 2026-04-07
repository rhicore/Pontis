#!/usr/bin/env python3
"""
Pontis CLI - Interactive VFS Explorer

A thin wrapper around tool_use/commands/ for command line interaction.

Usage:
    pontis <folder>                    # Enter folder and start interactive shell
    pontis ls <pontis_path> [path]     # List directory
    pontis cd <pontis_path> <path>     # Change directory (returns new cwd)
    pontis meta <pontis_path> <path>   # Get metadata
    pontis search <pontis_path> <q>   # Search nodes
    pontis find <pontis_path> <pat>   # Find by pattern
    pontis cat <pontis_path> <path>   # Show content
"""
import os
import sys
import argparse

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from tool_use import (
    ls_command, cd_command, pwd_command,
    meta_command, search_command, find_command, cat_command
)
from tool_use.utils.vfs import PontisVFS

# Tab completion support
try:
    import readline
    HAS_READLINE = True
except ImportError:
    HAS_READLINE = False


class TabCompleter:
    """Tab completion for Pontis shell"""

    def __init__(self, shell: 'PontisShell'):
        self.shell = shell
        self.commands = ['ls', 'cd', 'pwd', 'meta', 'search', 'find', 'cat', 'clear', 'help', 'exit', 'quit']

    def complete(self, text, state):
        """Complete function for readline"""
        if state == 0:
            # Build completion list
            line = readline.get_line_buffer()
            parts = line.split()

            if len(parts) <= 1 and not line.endswith(' '):
                # Completing command
                self.matches = [c for c in self.commands if c.startswith(text)]
            else:
                # Completing path
                self.matches = self._get_path_completions(text)

        try:
            return self.matches[state]
        except IndexError:
            return None

    def _get_path_completions(self, prefix: str) -> list:
        """Get path completions"""
        # Determine base path
        if prefix.startswith('/'):
            base = ''
            partial = prefix[1:]
        elif self.shell.cwd:
            base = self.shell.cwd
            partial = prefix
        else:
            base = ''
            partial = prefix

        # Build full path
        if partial:
            search_dir = os.path.join(self.shell.pontis_path, base, os.path.dirname(partial))
            search_partial = os.path.basename(partial)
        else:
            search_dir = os.path.join(self.shell.pontis_path, base)
            search_partial = ''

        if not os.path.exists(search_dir):
            return []

        # List candidates
        matches = []
        try:
            for entry in os.listdir(search_dir):
                if entry.startswith('.') or entry == '_meta.yml':
                    continue
                if entry.startswith(search_partial):
                    full_path = os.path.join(search_dir, entry)
                    # Check if it's a directory
                    is_dir = os.path.isdir(full_path)
                    # For directories, add trailing slash; for files, no suffix
                    suffix = '/' if is_dir else ''
                    matches.append(entry + suffix)
        except:
            pass

        return matches


class PontisShell:
    """Interactive shell for Pontis VFS"""

    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.pontis_path = os.path.join(self.project_path, ".pontis")
        self.cwd = ""

        # Ensure .pontis exists
        if not os.path.exists(self.pontis_path):
            print(f"No .pontis found in {project_path}")
            print("Run extractor first: python extractor/main.py <folder>")
            sys.exit(1)

        print(f"\n🚀 Pontis VFS Shell")
        print(f"   Project: {self.project_path}")
        print(f"   Type 'help' for commands, 'exit' to quit\n")

        # Setup tab completion
        if HAS_READLINE:
            self.completer = TabCompleter(self)
            readline.set_completer(self.completer.complete)
            readline.parse_and_bind('tab: complete')
            # Don't add space after completion
            readline.set_completer_delims(' \t\n`!@#$^&*()=+[{]}\\|;\',<>?')

    def run(self):
        """Run interactive shell"""
        while True:
            try:
                prompt_cwd = self.cwd if self.cwd else "/"
                prompt = f"\033[36mpontis:{prompt_cwd}\033[0m> "
                cmd_line = input(prompt).strip()

                if not cmd_line:
                    continue

                parts = cmd_line.split(maxsplit=1)
                cmd = parts[0]
                arg = parts[1] if len(parts) > 1 else ""

                result = self._execute(cmd, arg)
                if result:
                    print(result)

            except KeyboardInterrupt:
                print("\n")
                continue
            except EOFError:
                print("\n👋 Goodbye!")
                break

    def _execute(self, cmd: str, arg: str) -> str:
        """Execute a command"""
        if cmd in ("exit", "quit", "q"):
            print("👋 Goodbye!")
            sys.exit(0)

        elif cmd == "help":
            return self._help()

        elif cmd == "ls":
            path = arg or "."
            return ls_command(self.pontis_path, path, self.cwd)

        elif cmd == "ll":
            path = arg or "."
            return ls_command(self.pontis_path, path)

        elif cmd == "cd":
            path = arg or "."
            new_cwd = cd_command(self.pontis_path, self.cwd, path)
            if new_cwd.startswith("Error:"):
                return new_cwd
            self.cwd = new_cwd
            return None

        elif cmd == "pwd":
            return pwd_command(self.cwd)

        elif cmd == "meta":
            if not arg:
                return "Usage: meta <path> [-a] [+key]"
            # 传递所有参数
            meta_args = arg.split()
            return meta_command(self.pontis_path, meta_args, self.cwd)

        elif cmd == "search":
            if not arg:
                return "Usage: search <query>"
            return search_command(self.pontis_path, arg, self.cwd)

        elif cmd == "find":
            if not arg:
                return "Usage: find <pattern>"
            return find_command(self.pontis_path, arg, self.cwd)

        elif cmd == "cat":
            if not arg:
                return "Usage: cat <path>"
            return cat_command(self.pontis_path, arg)

        elif cmd == "clear":
            os.system('clear' if os.name != 'nt' else 'cls')
            return None

        else:
            return f"Unknown command: {cmd}. Type 'help' for available commands."

    def _help(self) -> str:
        """Return help message"""
        return """
┌─────────────────────────────────────────────────────────────┐
│                      Pontis VFS Shell                        │
├─────────────────────────────────────────────────────────────┤
│ Navigation:                                                  │
│   ls [path]        List directory contents                  │
│   cd <path>        Change directory                         │
│   pwd              Show current directory                   │
│                                                              │
│ Information:                                                 │
│   meta <path>      Show detailed node information           │
│   cat <path>       Show content (for scalar values)         │
│                                                              │
│ Search:                                                      │
│   search <query>   Search nodes by keyword                  │
│   find <pattern>   Find nodes by glob pattern (e.g., *.db)  │
│                                                              │
│ Other:                                                       │
│   clear            Clear screen                             │
│   help             Show this help                           │
│   exit/quit/q      Exit shell                               │
└─────────────────────────────────────────────────────────────┘
"""


def main():
    parser = argparse.ArgumentParser(
        description="Pontis - Interactive VFS Explorer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pontis ./my_project              # Enter project and start shell
  pontis ls ./.pontis mydb.db      # List database contents
  pontis meta ./.pontis users.table row_count
        """
    )

    parser.add_argument("command", nargs="?", help="Command or folder to open")
    parser.add_argument("pontis_path", nargs="?", help="Path to .pontis directory")
    parser.add_argument("args", nargs="*", help="Additional arguments")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # If command is a directory, enter interactive shell
    if os.path.isdir(args.command):
        project_path = os.path.abspath(args.command)
        shell = PontisShell(project_path)
        shell.run()
        return

    # Handle direct commands
    cmd = args.command
    pontis_path = args.pontis_path

    if not pontis_path:
        print(f"Usage: pontis {cmd} <pontis_path> [args...]")
        sys.exit(1)

    if not os.path.exists(pontis_path):
        print(f"Error: Path not found: {pontis_path}")
        sys.exit(1)

    extra_args = args.args if args.args else []

    if cmd == "ls":
        path = extra_args[0] if extra_args else "."
        print(ls_command(pontis_path, path))

    elif cmd == "cd":
        if not extra_args:
            print("Usage: pontis cd <pontis_path> <directory>")
            sys.exit(1)
        print(cd_command(pontis_path, "", extra_args[0]))

    elif cmd == "meta":
        if not extra_args:
            print("Usage: pontis meta <pontis_path> <path> [key]")
            sys.exit(1)
        key = extra_args[1] if len(extra_args) > 1 else None
        print(meta_command(pontis_path, extra_args[0], key))

    elif cmd == "search":
        if not extra_args:
            print("Usage: pontis search <pontis_path> <query>")
            sys.exit(1)
        print(search_command(pontis_path, extra_args[0]))

    elif cmd == "find":
        if not extra_args:
            print("Usage: pontis find <pontis_path> <pattern>")
            sys.exit(1)
        print(find_command(pontis_path, extra_args[0]))

    elif cmd == "cat":
        if not extra_args:
            print("Usage: pontis cat <pontis_path> <path>")
            sys.exit(1)
        print(cat_command(pontis_path, extra_args[0]))

    else:
        print(f"Unknown command: {cmd}")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
