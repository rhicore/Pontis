"""Text Info Generator - 通用文本文件信息生成器

职责：
- 匹配所有文本编码文件节点
- 添加文本文件共有的元信息（编码、字符数、行数、行长度统计等）

独立执行：
    python -m extractor.text_info ./my_data
"""
import os
import logging
from typing import Optional, Dict, Any, Set
from storage.workspace import Workspace
from extractor.modules.utils.src import file_exists, get_file_path, open_text_file

logger = logging.getLogger(__name__)

# 文本文件后缀集合
TEXT_EXTENSIONS: Set[str] = {
    # 纯文本
    '.txt', '.log', '.text', '.readme',
    # 标记语言
    '.md', '.markdown', '.rst', '.adoc', '.asciidoc',
    # 代码文件
    '.py', '.pyw', '.pyi',  # Python
    '.js', '.mjs', '.cjs',  # JavaScript
    '.ts', '.tsx', '.jsx',  # TypeScript / React
    '.java', '.class',       # Java
    '.c', '.h',              # C
    '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx',  # C++
    '.go',                   # Go
    '.rs',                   # Rust
    '.rb', '.erb',           # Ruby
    '.php', '.phtml',        # PHP
    '.swift',                # Swift
    '.kt', '.kts',           # Kotlin
    '.scala', '.sc',         # Scala
    '.r', '.rmd',            # R
    '.pl', '.pm',            # Perl
    '.lua',                  # Lua
    '.elm',                  # Elm
    '.clj', '.cljs',         # Clojure
    '.ex', '.exs',           # Elixir
    '.erl', '.hrl',          # Erlang
    '.fs', '.fsx',           # F#
    '.hs', '.lhs',           # Haskell
    '.ml', '.mli',           # OCaml
    '.groovy',               # Groovy
    '.cs', '.csx',           # C#
    '.vb', '.vbs',           # Visual Basic
    '.dart',                 # Dart
    '.jl',                   # Julia
    '.nim',                  # Nim
    '.cr',                   # Crystal
    '.d',                    # D
    '.f90', '.f95', '.f03', '.for',  # Fortran
    '.m', '.mm',             # Objective-C
    '.ps1', '.psm1', '.psd1',  # PowerShell
    # 数据/配置格式
    '.sql', '.ddl', '.dml',  # SQL
    '.json', '.jsonl',       # JSON
    '.yaml', '.yml',         # YAML
    '.xml', '.xsd', '.xslt', '.svg',  # XML
    '.toml',                 # TOML
    '.ini', '.cfg', '.conf', '.config', '.properties',  # Config
    '.csv', '.tsv',          # Tabular data
    '.html', '.htm', '.xhtml',  # HTML
    '.css', '.scss', '.sass', '.less',  # CSS
    '.graphql', '.gql',      # GraphQL
    '.proto',                # Protocol Buffers
    '.thrift',               # Apache Thrift
    # 脚本文件
    '.sh', '.bash', '.zsh', '.fish', '.csh', '.tcsh', '.ksh',  # Shell
    '.bat', '.cmd', '.nt',   # Windows Batch
    '.vbs', '.vba',          # VBScript
    '.awk',                  # AWK
    '.sed',                  # SED
    # 版本控制/构建
    '.gitignore', '.gitattributes', '.gitmodules',
    '.dockerignore', 'dockerfile', '.dockerfile',
    '.npmignore', '.editorconfig',
    'makefile', '.makefile', '.mk', '.mak',
    'cmake', '.cmake', 'cmakelists.txt',
    'vagrantfile', 'gemfile', 'rakefile',
    # 文档
    '.tex', '.latex', '.bib',
    '.po', '.pot', '.mo',    # gettext
    '.strings',              # iOS strings
    '.resx', '.resw',        # .NET resources
    # 其他
    '.env', '.envrc', '.env.local', '.env.development', '.env.production',
    '.htaccess', '.htpasswd',
    '.srt', '.vtt', '.sub',  # Subtitles
    '.ics', '.ical',         # Calendar
    '.diff', '.patch',       # Patches
}

# 文件名（无后缀）也可能是文本文件
TEXT_FILENAMES: Set[str] = {
    'makefile', 'rakefile', 'gemfile', 'vagrantfile',
    'dockerfile', 'jenkinsfile', 'brewfile',
    '.gitignore', '.gitattributes', '.gitmodules',
    '.dockerignore', '.editorconfig', '.npmignore',
    '.env', '.envrc', '.env.local',
    'readme', 'license', 'copying', 'authors', 'contributors',
    'changelog', 'changes', 'news', 'history',
    'install', 'setup', 'configure',
    'manifest', 'manifest.in',
    'requirements', 'requirements-dev', 'requirements-test',
    'pipfile', 'pipfile', 'poetry.lock', 'yarn.lock', 'package-lock.json',
    'cmakelists.txt', 'cmakecache.txt',
}


def is_text_file(filename: str) -> bool:
    """判断是否为文本文件"""
    name_lower = filename.lower()
    base_name = os.path.splitext(name_lower)[0]
    ext = os.path.splitext(name_lower)[1]

    # 检查后缀
    if ext in TEXT_EXTENSIONS:
        return True

    # 检查完整文件名
    if name_lower in TEXT_FILENAMES or base_name in TEXT_FILENAMES:
        return True

    return False


def generate(workspace: Workspace) -> None:
    """为所有文本文件节点生成信息"""
    logger.info("=== Generating Text info ===")

    processed = set()

    # 通过统一索引发现文本文件（含虚拟实体）
    rows = workspace.cypher("MATCH (n) RETURN n")
    for row in rows:
        props = row.get("n", {})
        name = props.get("name", "")
        basename = os.path.basename(name)
        if not is_text_file(basename):
            continue
        rel_path = props.get("path", name)
        if rel_path in processed:
            continue
        processed.add(rel_path)
        try:
            _generate_for_text(rel_path, workspace)
        except Exception as e:
            logger.warning(f"Failed to generate info for {rel_path}: {e}")


def _generate_for_text(path: str, workspace: Workspace) -> bool:
    """为单个文本文件生成通用元信息"""
    meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": path})
    meta = meta_rows[0].get("n") if meta_rows else None
    if not meta:
        return False

    # 跳过已处理的
    if "encoding" in meta:
        return False

    rel_path = meta.get("path")
    if not rel_path or not file_exists(workspace, rel_path):
        return False

    try:
        # 检测编码
        file_path = get_file_path(workspace, rel_path)
        if not file_path:
            return False
        encoding = _detect_encoding(file_path)

        # 读取内容
        with open_text_file(workspace, rel_path, 'r', encoding=encoding, errors='ignore') as f:
            content = f.read()

        # 基础文本统计
        lines = content.splitlines()
        line_count = len(lines)
        char_count = len(content)

        # 行统计
        empty_lines = sum(1 for line in lines if not line.strip())
        non_empty_lines = line_count - empty_lines
        line_lengths = [len(line) for line in lines]
        avg_line_length = round(sum(line_lengths) / len(line_lengths), 2) if line_lengths else 0
        max_line_length = max(line_lengths) if line_lengths else 0

        # 字符分布统计
        char_stats = _analyze_characters(content)

        # 更新meta
        workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": path, "props": {
            "encoding": encoding,
            "char_count": char_count,
            "line_count": line_count,
            "empty_line_count": empty_lines,
            "non_empty_line_count": non_empty_lines,
            "avg_line_length": avg_line_length,
            "max_line_length": max_line_length,
            **char_stats,
        }})

        logger.info(f"  Text info: {path} ({line_count} lines, {char_count} chars)")
        return True

    except Exception as e:
        logger.debug(f"Could not get text info: {e}")
        return False


def _detect_encoding(file_path: str) -> str:
    """检测文件编码"""
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw = f.read(10000)  # 读取前10KB检测
            if not raw:
                return 'utf-8'
            result = chardet.detect(raw)
            return result.get('encoding', 'utf-8') or 'utf-8'
    except ImportError:
        return 'utf-8'
    except:
        return 'utf-8'


def _analyze_characters(content: str) -> Dict[str, Any]:
    """分析字符组成"""
    if not content:
        return {
            "letter_count": 0,
            "digit_count": 0,
            "space_count": 0,
            "punct_count": 0,
            "other_count": 0,
        }

    letters = sum(1 for c in content if c.isalpha())
    digits = sum(1 for c in content if c.isdigit())
    spaces = sum(1 for c in content if c.isspace())
    puncts = sum(1 for c in content if not c.isalnum() and not c.isspace())
    others = len(content) - letters - digits - spaces - puncts

    return {
        "letter_count": letters,
        "digit_count": digits,
        "space_count": spaces,
        "punct_count": puncts,
        "other_count": others,
    }
