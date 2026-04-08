#!/usr/bin/env python3
"""
Pontis CLI - Interactive VFS Explorer

A thin wrapper around tool_use/commands/ for command line interaction.

Usage:
    pontis <folder>                    # Enter folder and start interactive shell
    pontis ls <project_path> [path]    # List directory
    pontis cd <project_path> <path>    # Change directory (returns new cwd)
    pontis meta <project_path> <file> [entity]   # Get metadata
    pontis read <project_path> <file> [entity]   # Read content
    pontis glob <project_path> <file> [pattern]  # Search entities
"""
import os
import sys
import argparse

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from tool_use import (
    ls_command, cd_command,
    glob_command, grep_command, meta_command, read_command,
    pglob_command, pmeta_command, pread_command  # backward compatibility
)

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
        self.commands = ['ls', 'cd', 'pwd', 'glob', 'grep', 'meta', 'read', 'clear', 'help', 'exit', 'quit']

    def complete(self, text, state):
        """Complete function for readline"""
        if state == 0:
            line = readline.get_line_buffer()
            parts = line.split()

            if len(parts) <= 1 and not line.endswith(' '):
                self.matches = [c for c in self.commands if c.startswith(text)]
            else:
                self.matches = self._get_path_completions(text)

        try:
            return self.matches[state]
        except IndexError:
            return None

    def _get_path_completions(self, prefix: str) -> list:
        """Get path completions for physical files"""
        if prefix.startswith('/'):
            base = ''
            partial = prefix[1:]
        elif self.shell.cwd:
            base = self.shell.cwd
            partial = prefix
        else:
            base = ''
            partial = prefix

        if partial:
            search_dir = os.path.join(self.shell.project_path, base, os.path.dirname(partial))
            search_partial = os.path.basename(partial)
        else:
            search_dir = os.path.join(self.shell.project_path, base)
            search_partial = ''

        if not os.path.exists(search_dir):
            return []

        matches = []
        try:
            for entry in os.listdir(search_dir):
                if entry.startswith('.') or entry == '.pontis':
                    continue
                if entry.startswith(search_partial):
                    full_path = os.path.join(search_dir, entry)
                    is_dir = os.path.isdir(full_path) and not self._is_virtual_file(full_path)
                    suffix = '/' if is_dir else ''
                    matches.append(entry + suffix)
        except:
            pass

        return matches

    def _is_virtual_file(self, full_path: str) -> bool:
        """Check if path is a pontis virtual file"""
        if not os.path.isdir(full_path):
            return False
        name = os.path.basename(full_path)
        virtual_extensions = ['.db', '.csv', '.tsv', '.json', '.yaml', '.yml', '.md', '.txt']
        return any(name.endswith(ext) for ext in virtual_extensions)


class PontisShell:
    """Interactive shell for Pontis VFS"""

    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.pontis_path = os.path.join(self.project_path, ".pontis")
        self.cwd = ""

        if not os.path.exists(self.pontis_path):
            print(f"No .pontis found in {project_path}")
            print("Run extractor first: python -m extractor <folder>")
            sys.exit(1)

        print(f"\n🚀 Pontis VFS Shell")
        print(f"   Project: {self.project_path}")
        print(f"   Type 'help' for commands, 'exit' to quit\n")

        if HAS_READLINE:
            self.completer = TabCompleter(self)
            readline.set_completer(self.completer.complete)
            readline.parse_and_bind('tab: complete')
            readline.set_completer_delims(r' \t\n`!@#$^&*()=+[{]}\|;\',<>?')

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
            return ls_command(self.project_path, path, self.cwd)

        elif cmd == "cd":
            path = arg or "."
            new_cwd = cd_command(self.project_path, self.cwd, path)
            if new_cwd.startswith("Error:"):
                return new_cwd
            self.cwd = new_cwd
            return None

        elif cmd == "pwd":
            return f"{self.cwd}" if self.cwd else "/"

        elif cmd == "glob":
            # glob <file> [pattern]
            args = arg.split(maxsplit=1)
            if len(args) < 1:
                return "Usage: glob <physical_file> [pattern]"
            physical_file = args[0]
            pattern = args[1] if len(args) > 1 else "*"
            return glob_command(self.project_path, physical_file, pattern, current_cwd=self.cwd)

        elif cmd == "grep":
            # grep <file> [entity] <pattern> [options]
            args = arg.split()
            if len(args) < 2:
                return "Usage: grep <physical_file> [entity_name] <pattern> [options]"
            return grep_command(self.project_path, args[0], args[1:], current_cwd=self.cwd)

        elif cmd == "meta":
            # meta <file> [entity] [options]
            args = arg.split()
            if len(args) < 1:
                return "Usage: meta <physical_file> [entity_name] [options]"
            return meta_command(self.project_path, *args, current_cwd=self.cwd)

        elif cmd == "read":
            # read <file> [entity] [options]
            args = arg.split()
            if len(args) < 1:
                return "Usage: read <physical_file> [entity_name] [options]"
            return read_command(self.project_path, *args, current_cwd=self.cwd)

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
│ Physical File Navigation:                                    │
│   ls [path]        List directory (with Pontis meta info)   │
│   cd <path>        Change directory (physical only)         │
│   pwd              Show current directory                   │
│                                                              │
│ Knowledge Graph (Pontis Entities):                           │
│   glob <file> [pat]   Search entities (default: all)        │
│   grep <file> [ent] <pat>  Grep content in file/entity      │
│   meta <file> [ent]   Show entity metadata                  │
│   read <file> [ent]   Read entity content                   │
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
  pontis ./my_project                    # Enter project and start shell
  pontis ls ./my_project                 # List project root
  pontis glob ./my_project mydb.db "*.table"
  pontis read ./my_project mydb.db users.table
        """
    )

    parser.add_argument("command", nargs="?", help="Command or folder to open")
    parser.add_argument("project_path", nargs="?", help="Path to project directory")
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
    project_path = args.project_path

    if not project_path:
        print(f"Usage: pontis {cmd} <project_path> [args...]")
        sys.exit(1)

    if not os.path.exists(project_path):
        print(f"Error: Path not found: {project_path}")
        sys.exit(1)

    extra_args = args.args if args.args else []

    if cmd == "ls":
        path = extra_args[0] if extra_args else "."
        print(ls_command(project_path, path))

    elif cmd == "cd":
        if not extra_args:
            print("Usage: pontis cd <project_path> <directory>")
            sys.exit(1)
        print(cd_command(project_path, "", extra_args[0]))

    elif cmd == "glob":
        if not extra_args:
            print("Usage: pontis glob <project_path> <file> [pattern]")
            sys.exit(1)
        physical_file = extra_args[0]
        pattern = extra_args[1] if len(extra_args) > 1 else "*"
        print(glob_command(project_path, physical_file, pattern))

    elif cmd == "grep":
        if len(extra_args) < 2:
            print("Usage: pontis grep <project_path> <file> [entity] <pattern>")
            sys.exit(1)
        print(grep_command(project_path, extra_args[0], extra_args[1:]))

    elif cmd == "meta":
        if not extra_args:
            print("Usage: pontis meta <project_path> <file> [entity] [options]")
            sys.exit(1)
        print(meta_command(project_path, *extra_args))

    elif cmd == "read":
        if not extra_args:
            print("Usage: pontis read <project_path> <file> [entity] [options]")
            sys.exit(1)
        print(read_command(project_path, *extra_args))

    else:
        print(f"Unknown command: {cmd}")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
