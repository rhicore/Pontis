"""
Grep tool - Search content in files and entities.

Extends Claude Code's GrepTool design with entity support (.chunk).
Uses subprocess ripgrep for physical files, custom logic for entities.

Output formats follow CC spec:
- content mode: file:line:content
- count mode: file:count
- files_with_matches mode: file list with count header
"""
import os
import re
import subprocess
import sys
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool_use.utils.path_parser import parse_path_pattern, ParsedPath

DEFAULT_HEAD_LIMIT = 250
MAX_RESULT_SIZE_CHARS = 20_000

# VCS dirs to exclude from search
VCS_EXCLUDE = ['.git', '.svn', '.hg', '.bzr', '.jj', '.sl']


@dataclass
class GrepParams:
    """Parsed grep parameters matching CC's interface."""
    pattern: str
    path: Optional[str] = None
    glob: Optional[str] = None
    output_mode: str = "files_with_matches"  # content | files_with_matches | count
    context_before: Optional[int] = None  # -B
    context_after: Optional[int] = None   # -A
    context: Optional[int] = None         # -C (overrides -B/-A)
    show_line_numbers: bool = True        # -n
    case_insensitive: bool = False        # -i
    type_filter: Optional[str] = None     # --type
    head_limit: int = DEFAULT_HEAD_LIMIT
    offset: int = 0
    multiline: bool = False


def _parse_grep_params(params: dict) -> GrepParams:
    """Parse a dict of grep parameters into GrepParams."""
    context = params.get('context') or params.get('-C')
    context_before = params.get('-B') if context is None else None
    context_after = params.get('-A') if context is None else None

    return GrepParams(
        pattern=params['pattern'],
        path=params.get('path'),
        glob=params.get('glob'),
        output_mode=params.get('output_mode', 'files_with_matches'),
        context_before=context_before,
        context_after=context_after,
        context=context,
        show_line_numbers=params.get('-n', True),
        case_insensitive=params.get('-i', False),
        type_filter=params.get('type'),
        head_limit=params.get('head_limit', DEFAULT_HEAD_LIMIT),
        offset=params.get('offset', 0),
        multiline=params.get('multiline', False),
    )


def _apply_head_limit(items: list, limit: int, offset: int = 0) -> Tuple[list, Optional[int]]:
    """Apply head_limit with offset. Returns (sliced_items, applied_limit_or_None)."""
    if limit == 0:
        return items[offset:], None
    effective = limit
    sliced = items[offset:offset + effective]
    was_truncated = len(items) - offset > effective
    return sliced, effective if was_truncated else None


def _run_ripgrep(params: GrepParams, search_path: str) -> List[str]:
    """
    Execute ripgrep via subprocess and return raw output lines.

    Args:
        params: Parsed grep parameters
        search_path: Absolute path to search in

    Returns:
        List of output lines from ripgrep
    """
    args = ['rg', '--hidden']

    # Exclude VCS dirs
    for d in VCS_EXCLUDE:
        args.extend(['--glob', f'!{d}'])

    # Max column width to avoid bloated output
    args.extend(['--max-columns', '500'])

    # Multiline
    if params.multiline:
        args.extend(['-U', '--multiline-dotall'])

    # Case insensitive
    if params.case_insensitive:
        args.append('-i')

    # Output mode
    if params.output_mode == 'files_with_matches':
        args.append('-l')
    elif params.output_mode == 'count':
        args.append('-c')

    # Line numbers (content mode only)
    if params.show_line_numbers and params.output_mode == 'content':
        args.append('-n')

    # Context lines (content mode only)
    if params.output_mode == 'content':
        if params.context is not None:
            args.extend(['-C', str(params.context)])
        elif params.context_before is not None:
            args.extend(['-B', str(params.context_before)])
        if params.context_after is not None:
            args.extend(['-A', str(params.context_after)])

    # Pattern (use -e if pattern starts with -)
    if params.pattern.startswith('-'):
        args.extend(['-e', params.pattern])
    else:
        args.append(params.pattern)

    # Type filter
    if params.type_filter:
        args.extend(['--type', params.type_filter])

    # Glob filter
    if params.glob:
        for p in params.glob.split(','):
            p = p.strip()
            if p:
                args.extend(['--glob', p])

    # Search path
    args.append(search_path)

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            cwd=search_path if os.path.isdir(search_path) else os.path.dirname(search_path)
        )
        if result.returncode == 0:
            return result.stdout.rstrip('\n').split('\n') if result.stdout.strip() else []
        return []  # rg returns 1 for no matches
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fallback: if rg not available, use Python re
        return _python_grep_fallback(params, search_path)


def _python_grep_fallback(params: GrepParams, search_path: str) -> List[str]:
    """Fallback grep using Python re when rg is unavailable."""
    results = []
    flags = re.IGNORECASE if params.case_insensitive else 0
    pattern = re.compile(params.pattern, flags)

    if os.path.isfile(search_path):
        files = [search_path]
    else:
        files = []
        for root, dirs, fnames in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in VCS_EXCLUDE and not d.startswith('.')]
            for fname in fnames:
                files.append(os.path.join(root, fname))

    for fpath in files:
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            matches_in_file = []
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    matches_in_file.append((i, line.rstrip()))

            if not matches_in_file:
                continue

            if params.output_mode == 'files_with_matches':
                results.append(fpath)
            elif params.output_mode == 'count':
                results.append(f"{fpath}:{len(matches_in_file)}")
            elif params.output_mode == 'content':
                for line_num, content in matches_in_file:
                    results.append(f"{fpath}:{line_num}:{content}")
        except Exception:
            continue

    return results


def _to_relative_paths(lines: List[str], base_path: str) -> List[str]:
    """Convert absolute paths in output lines to relative paths."""
    results = []
    for line in lines:
        # Try to find the first colon to extract file path
        colon_idx = line.find(':')
        if colon_idx > 0:
            file_part = line[:colon_idx]
            rest = line[colon_idx:]
            if os.path.isabs(file_part):
                try:
                    rel = os.path.relpath(file_part, base_path)
                    results.append(rel + rest)
                    continue
                except ValueError:
                    pass
        results.append(line)
    return results


def _format_content_output(lines: List[str], applied_limit: Optional[int],
                           applied_offset: int) -> str:
    """Format content mode output."""
    content = '\n'.join(lines)
    if applied_limit is not None or applied_offset > 0:
        parts = []
        if applied_limit is not None:
            parts.append(f"limit: {applied_limit}")
        if applied_offset > 0:
            parts.append(f"offset: {applied_offset}")
        content += f"\n\n[Showing results with pagination = {', '.join(parts)}]"
    return content


def _format_count_output(lines: List[str], applied_limit: Optional[int],
                         applied_offset: int) -> str:
    """Format count mode output."""
    total_matches = 0
    file_count = 0
    for line in lines:
        colon_idx = line.rfind(':')
        if colon_idx > 0:
            try:
                count = int(line[colon_idx + 1:])
                total_matches += count
                file_count += 1
            except ValueError:
                file_count += 1

    content = '\n'.join(lines)
    limit_info = ""
    if applied_limit is not None:
        limit_info = f" with pagination = limit: {applied_limit}"

    content += f"\n\nFound {total_matches} total occurrences across {file_count} files.{limit_info}"
    return content


def _format_files_output(filenames: List[str], num_files: int,
                         applied_limit: Optional[int]) -> str:
    """Format files_with_matches mode output."""
    if num_files == 0:
        return "No files found"

    limit_info = f" {applied_limit}" if applied_limit is not None else ""
    result = f"Found {num_files} files{limit_info}\n" + '\n'.join(filenames)
    return result


def grep_command(
    project_path: str,
    params: dict,
    current_cwd: str = ""
) -> str:
    """
    Search content in files and entities.

    Args:
        project_path: Path to project root
        params: Dict with grep parameters (pattern, path, glob, output_mode, etc.)
        current_cwd: Current working directory

    Returns:
        Formatted search results
    """
    p = _parse_grep_params(params)

    if not p.pattern:
        return "Error: No pattern specified"

    # Resolve search path
    search_base = os.path.join(project_path, current_cwd) if current_cwd else project_path
    if p.path:
        search_base = os.path.join(search_base, p.path) if not os.path.isabs(p.path) else p.path

    if not os.path.exists(search_base):
        return f"Path does not exist: {p.path or '.'}"

    # Check if path contains :: (entity grep)
    if p.path and '::' in p.path:
        return _grep_entity(project_path, p, current_cwd)

    # Physical file grep via ripgrep
    raw_results = _run_ripgrep(p, search_base)

    if p.output_mode == 'content':
        limited, applied_limit = _apply_head_limit(raw_results, p.head_limit, p.offset)
        limited = _to_relative_paths(limited, project_path)
        return _format_content_output(limited, applied_limit, p.offset)

    elif p.output_mode == 'count':
        limited, applied_limit = _apply_head_limit(raw_results, p.head_limit, p.offset)
        limited = _to_relative_paths(limited, project_path)
        return _format_count_output(limited, applied_limit, p.offset)

    else:  # files_with_matches (default)
        # Sort by mtime
        sorted_results = _sort_by_mtime(raw_results)
        limited, applied_limit = _apply_head_limit(sorted_results, p.head_limit, p.offset)
        limited = _to_relative_paths(limited, project_path)
        return _format_files_output(limited, len(limited), applied_limit)


def _sort_by_mtime(files: List[str]) -> List[str]:
    """Sort file paths by modification time (newest first)."""
    def mtime_key(f):
        try:
            return -os.path.getmtime(f)
        except OSError:
            return 0
    return sorted(files, key=mtime_key)


def _grep_entity(project_path: str, params: GrepParams, cwd: str) -> str:
    """
    Grep within a logical entity (currently supports .chunk).
    """
    parsed = parse_path_pattern(params.path)
    pontis_root = os.path.join(project_path, ".pontis")

    if not os.path.exists(pontis_root):
        return "No .pontis directory found"

    # Resolve physical file
    file_path = os.path.join(project_path, cwd, parsed.file_pattern) if cwd else os.path.join(project_path, parsed.file_pattern)
    if not os.path.exists(file_path):
        file_path = os.path.join(project_path, parsed.file_pattern)

    entity_pattern = parsed.entity_pattern or "*"

    # Currently only support .chunk entity grep
    results = []

    # Walk entity directories looking for .chunk
    entity_root = os.path.join(pontis_root, parsed.file_pattern, "_entity")
    if not os.path.exists(entity_root):
        return "No entities found"

    flags = re.IGNORECASE if params.case_insensitive else 0
    pattern = re.compile(params.pattern, flags)

    for root, dirs, files in os.walk(entity_root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for d in dirs:
            if d.endswith('.chunk') and fnmatch.fnmatch(d, entity_pattern):
                chunk_dir = os.path.join(root, d)
                raw_file = os.path.join(chunk_dir, "_raw")
                meta_file = os.path.join(chunk_dir, "_meta.yml")

                if os.path.exists(raw_file):
                    try:
                        with open(raw_file, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                        for i, line in enumerate(lines, 1):
                            if pattern.search(line):
                                rel = os.path.relpath(chunk_dir, os.path.join(pontis_root, parsed.file_pattern, "_entity"))
                                results.append(f"{parsed.file_pattern}::{rel}:{i}:{line.rstrip()}")
                    except Exception:
                        pass

    if not results:
        return "No matches found"

    # Apply head_limit
    limited, applied_limit = _apply_head_limit(results, params.head_limit, params.offset)
    content = '\n'.join(limited)
    if applied_limit is not None:
        content += f"\n\n[Showing results with pagination = limit: {applied_limit}]"
    return content


if __name__ == "__main__":
    import json
    if len(sys.argv) < 3:
        print("Usage: python -m tool_use.grep.tool <project_path> <json_params> [cwd]")
        print('  json_params: {"pattern": "TODO", "output_mode": "content"}')
        sys.exit(1)

    _project = sys.argv[1]
    _params = json.loads(sys.argv[2])
    _cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(grep_command(_project, _params, _cwd))
