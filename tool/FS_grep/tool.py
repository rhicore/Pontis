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

from typing import Optional, List, Tuple
from dataclasses import dataclass

from tool.config import TOOL_PAGINATION

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
    head_limit: int = TOOL_PAGINATION["grep"].default_limit
    offset: int = 0
    multiline: bool = False


def _parse_grep_params(pattern: str, **kwargs) -> GrepParams:
    """Parse grep parameters into GrepParams."""
    grep_conf = TOOL_PAGINATION["grep"]
    return GrepParams(
        pattern=pattern,
        path=kwargs.get('path'),
        glob=kwargs.get('glob'),
        output_mode=kwargs.get('output_mode', 'files_with_matches'),
        case_insensitive=kwargs.get('ignore_case', False),
        type_filter=kwargs.get('type'),
        head_limit=kwargs.get('head_limit', grep_conf.default_limit),
        offset=kwargs.get('offset', 0),
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
    """Execute ripgrep via subprocess and return raw output lines."""
    args = ['rg', '--hidden']

    for d in VCS_EXCLUDE:
        args.extend(['--glob', f'!{d}'])

    args.extend(['--max-columns', '500'])

    if params.multiline:
        args.extend(['-U', '--multiline-dotall'])

    if params.case_insensitive:
        args.append('-i')

    if params.output_mode == 'files_with_matches':
        args.append('-l')
    elif params.output_mode == 'count':
        args.append('-c')

    if params.show_line_numbers and params.output_mode == 'content':
        args.append('-n')

    if params.output_mode == 'content':
        if params.context is not None:
            args.extend(['-C', str(params.context)])
        elif params.context_before is not None:
            args.extend(['-B', str(params.context_before)])
        if params.context_after is not None:
            args.extend(['-A', str(params.context_after)])

    if params.pattern.startswith('-'):
        args.extend(['-e', params.pattern])
    else:
        args.append(params.pattern)

    if params.type_filter:
        args.extend(['--type', params.type_filter])

    if params.glob:
        for p in params.glob.split(','):
            p = p.strip()
            if p:
                args.extend(['--glob', p])

    args.append(search_path)

    if params.output_mode == 'content' and params.head_limit > 0:
        try:
            return _run_ripgrep_streaming(args, search_path, params.offset + params.head_limit + 1)
        except FileNotFoundError:
            return _python_grep_fallback(params, search_path)

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            cwd=search_path if os.path.isdir(search_path) else os.path.dirname(search_path)
        )
        if result.returncode == 0:
            return result.stdout.rstrip('\n').split('\n') if result.stdout.strip() else []
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _python_grep_fallback(params, search_path)


def _run_ripgrep_streaming(args: List[str], search_path: str, max_lines: int) -> List[str]:
    """Read enough rg output for pagination without buffering huge result sets."""
    cwd = search_path if os.path.isdir(search_path) else os.path.dirname(search_path)
    proc = None
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=cwd,
        )
        lines: List[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            if len(lines) >= max_lines:
                proc.terminate()
                break
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        return lines
    except FileNotFoundError:
        raise
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()


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
    if num_files == 0:
        return "No files found"

    limit_info = f" {applied_limit}" if applied_limit is not None else ""
    result = f"Found {num_files} files{limit_info}\n" + '\n'.join(filenames)
    return result


def _sort_by_mtime(files: List[str]) -> List[str]:
    def mtime_key(f):
        try:
            return -os.path.getmtime(f)
        except OSError:
            return 0
    return sorted(files, key=mtime_key)


def _source_path_for(workspace, rel_path: str = ".") -> Optional[str]:
    root = getattr(workspace, "project_path", "") or ""
    if rel_path in ("", "."):
        return root if root and os.path.isdir(root) else None

    rows = workspace.cypher(
        "MATCH (n:file {path: $path}) RETURN coalesce(n._file_open, n.file_open) AS open_file",
        params={"path": rel_path},
    )
    if not rows and rel_path not in ("", "."):
        rows = workspace.cypher(
            "MATCH (n:file {name: $name}) RETURN coalesce(n._file_open, n.file_open) AS open_file",
            params={"name": os.path.basename(rel_path)},
        )
    if len(rows) == 1:
        path = getattr(rows[0].get("open_file"), "path", None)
        if path:
            return path

    if root and not os.path.isabs(rel_path):
        path = os.path.join(root, rel_path)
        if os.path.exists(path):
            return path
    return rel_path if os.path.isabs(rel_path) and os.path.exists(rel_path) else None


def grep_command(
    workspace,
    pattern: str = "",
    path: str = "",
    output_mode: str = "files_with_matches",
    glob: Optional[str] = None,
    ignore_case: bool = False,
    head_limit: int = 250,
    offset: int = 0,
    current_cwd: str = "",
    **kwargs
) -> str:
    """
    Search content in files and entities.

    Args:
        workspace: Workspace 实例
        pattern: Regex pattern (ripgrep syntax)
        path: File or directory to search
        output_mode: "content", "files_with_matches", or "count"
        glob: File name filter
        ignore_case: Case insensitive search
        head_limit: Max output entries
        offset: Starting index (0-based)
        current_cwd: Current working directory

    Returns:
        Formatted search results
    """
    if not pattern:
        return "Error: No pattern specified"

    grep_conf = TOOL_PAGINATION["grep"]
    if head_limit == 250:
        head_limit = grep_conf.default_limit
    head_limit = min(head_limit, grep_conf.max_limit)

    params = _parse_grep_params(
        pattern=pattern,
        path=path,
        output_mode=output_mode,
        glob=glob,
        ignore_case=ignore_case,
        head_limit=head_limit,
        offset=offset,
    )

    # Resolve search path through storage handle properties only.
    if params.path:
        search_rel = os.path.join(current_cwd, params.path) if current_cwd and not os.path.isabs(params.path) else params.path
    else:
        search_rel = current_cwd or "."
    search_base = search_rel if os.path.isabs(search_rel) else _source_path_for(workspace, search_rel)

    if not search_base or not os.path.exists(search_base):
        return f"Path does not exist: {params.path or '.'}"

    root_path = _source_path_for(workspace, ".") or (search_base if os.path.isdir(search_base) else os.path.dirname(search_base))

    # Physical file grep via ripgrep
    raw_results = _run_ripgrep(params, search_base)

    if params.output_mode == 'content':
        limited, applied_limit = _apply_head_limit(raw_results, params.head_limit, params.offset)
        limited = _to_relative_paths(limited, root_path)
        return _format_content_output(limited, applied_limit, params.offset)

    elif params.output_mode == 'count':
        limited, applied_limit = _apply_head_limit(raw_results, params.head_limit, params.offset)
        limited = _to_relative_paths(limited, root_path)
        return _format_count_output(limited, applied_limit, params.offset)

    else:  # files_with_matches (default)
        sorted_results = _sort_by_mtime(raw_results)
        limited, applied_limit = _apply_head_limit(sorted_results, params.head_limit, params.offset)
        limited = _to_relative_paths(limited, root_path)
        return _format_files_output(limited, len(limited), applied_limit)


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tool.FS_grep.tool <project_name> <json_params> [cwd]")
        sys.exit(1)

    from storage.workspace import Workspace
    ws = Workspace(active_projects=[sys.argv[1]])
    _params = json.loads(sys.argv[2])
    _cwd = sys.argv[3] if len(sys.argv) > 3 else ""
    print(grep_command(ws, **_params, current_cwd=_cwd))
