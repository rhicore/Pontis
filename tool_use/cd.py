"""cd command - Change directory in physical file system

Usage:
    python -m tool_use.cd <project_path> <directory>

Note: Can only enter real physical directories, not pontis virtual files like .db

Examples:
    python -m tool_use.cd ./my_project dev_databases
    python -m tool_use.cd ./my_project dev_databases/financial
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def is_pontis_virtual_file(full_path: str) -> bool:
    """
    Check if path is a pontis virtual file (directory treated as file).
    These cannot be entered with cd.
    """
    if not os.path.isdir(full_path):
        return False

    name = os.path.basename(full_path)

    # Extensions that are virtual files in pontis
    virtual_extensions = ['.db', '.csv', '.tsv', '.json', '.yaml', '.yml', '.md', '.txt']
    for ext in virtual_extensions:
        if name.endswith(ext):
            return True

    return False


def cd_command(project_path: str, current_cwd: str, path: str) -> str:
    """
    Change directory in physical file system.
    Returns new cwd relative to project root.

    Args:
        project_path: Path to project directory (containing .pontis)
        current_cwd: Current working directory (relative to project)
        path: Path to change to (can be relative or absolute)

    Returns:
        New cwd path relative to project root, or error message
    """
    pontis_root = os.path.join(project_path, ".pontis")
    if not os.path.exists(pontis_root):
        return f"Error: .pontis directory not found in {project_path}"

    try:
        # Build target path
        if os.path.isabs(path):
            # Absolute path - must be within project
            full_path = os.path.normpath(path)
            if not full_path.startswith(os.path.normpath(project_path)):
                return f"Error: Path outside project: {path}"
        elif path.startswith('/'):
            # Project-root relative
            full_path = os.path.join(project_path, path[1:])
            full_path = os.path.normpath(full_path)
        elif current_cwd:
            # Relative to current cwd
            full_path = os.path.join(project_path, current_cwd, path)
            full_path = os.path.normpath(full_path)
        else:
            # Relative to project root
            full_path = os.path.join(project_path, path)
            full_path = os.path.normpath(full_path)

        # Check if path exists
        if not os.path.exists(full_path):
            return f"Error: Path not found: {path}"

        # Check if it's a directory
        if not os.path.isdir(full_path):
            return f"Error: Not a directory: {path}"

        # Check if it's a pontis virtual file (cannot enter)
        if is_pontis_virtual_file(full_path):
            name = os.path.basename(full_path)
            return f"Error: '{name}' is a virtual file (use 'pglob {name} *' to see entities)"

        # Calculate new cwd relative to project root
        new_cwd = os.path.relpath(full_path, project_path)

        # Normalize . to empty string (root)
        if new_cwd == '.':
            new_cwd = ""

        return new_cwd

    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.cd <project_path> <directory>")
        print("Example: python -m tool_use.cd ./my_project dev_databases/financial")
        sys.exit(1)

    project_path = sys.argv[1]
    directory = sys.argv[2]
    current_cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(cd_command(project_path, current_cwd, directory))
