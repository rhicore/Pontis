"""ProjectStore — 存储抽象层

tool_use 通过 ProjectStore 访问所有元数据和实体，不直接操作 .pontis/ 目录。
get_meta() 自动 enrich 虚属性，调用方不区分存储属性和计算属性。

以后换存储后端（文件 → 数据库）只需实现新的 Store 类。
"""
import os
import fnmatch
import glob as _glob
import sqlite3
from typing import Dict, Iterator, List, Optional, Tuple

import yaml


class ProjectStore:
    """Read-only storage abstraction over .pontis/ directory.

    All tool_use modules receive a store instance instead of project_path.
    """

    def __init__(self, project_path: str):
        self._project_path = os.path.abspath(project_path)
        self._pontis_root = os.path.join(self._project_path, ".pontis")
        self._meta_cache: Dict[str, Optional[dict]] = {}

    # ==================== Properties ====================

    @property
    def project_path(self) -> str:
        return self._project_path

    @property
    def pontis_exists(self) -> bool:
        return os.path.exists(self._pontis_root)

    # ==================== Metadata ====================

    def get_meta(self, path: str, entity_path: str = "") -> Optional[dict]:
        """Get enriched metadata for a file or entity.

        Reads _meta.yml, enriches with virtual properties.
        Returns None if not found.
        """
        cache_key = f"{path}::{entity_path}" if entity_path else path

        if cache_key in self._meta_cache:
            cached = self._meta_cache[cache_key]
            return dict(cached) if cached else None

        raw = self._read_raw_meta(path, entity_path)
        if raw is None:
            self._meta_cache[cache_key] = None
            return None

        # Enrich with virtual properties
        from storage.virtual_props import enrich_meta
        enriched = enrich_meta(raw, self._project_path, path, entity_path)
        self._meta_cache[cache_key] = enriched
        return dict(enriched)

    def meta_exists(self, path: str, entity_path: str = "") -> bool:
        """Check if metadata exists for a file or entity."""
        if entity_path:
            meta_path = os.path.join(
                self._pontis_root, path, "_entity", entity_path, "_meta.yml"
            )
        else:
            meta_path = os.path.join(self._pontis_root, path, "_meta.yml")
        return os.path.exists(meta_path)

    def _read_raw_meta(self, path: str, entity_path: str = "") -> Optional[dict]:
        """Read raw _meta.yml without enrichment. Internal only."""
        if entity_path:
            meta_path = os.path.join(
                self._pontis_root, path, "_entity", entity_path, "_meta.yml"
            )
        else:
            meta_path = os.path.join(self._pontis_root, path, "_meta.yml")

        if not os.path.exists(meta_path):
            return None

        try:
            with open(meta_path, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    # ==================== Entity Discovery ====================

    def glob_entities(self, file_path: str, pattern: str = "*") -> List[str]:
        """List entities under a file matching fnmatch pattern.

        Returns entity relative paths (e.g., "users.table", "users.id.INT.col").
        """
        entity_root = os.path.join(self._pontis_root, file_path, "_entity")
        if not os.path.exists(entity_root):
            return []

        results = []
        for root, dirs, files in os.walk(entity_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for d in dirs:
                if fnmatch.fnmatch(d, pattern):
                    rel = os.path.relpath(os.path.join(root, d), entity_root)
                    results.append(rel)
        return results

    def walk_all_metas(self) -> Iterator[Tuple[str, dict]]:
        """Walk ALL _meta.yml files in .pontis/.

        Yields (rel_path, enriched_meta) for every _meta.yml found.
        Used for keyword search.
        """
        if not os.path.exists(self._pontis_root):
            return

        for root, dirs, files in os.walk(self._pontis_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in files:
                if fname != '_meta.yml':
                    continue
                rel_dir = os.path.relpath(root, self._pontis_root)
                raw = self._read_raw_meta_by_path(os.path.join(root, fname))
                if raw is not None:
                    # Determine if this is a file-level or entity-level meta
                    # for virtual prop enrichment
                    parts = rel_dir.replace(os.sep, "/").split("/")
                    if "_entity" in parts:
                        # Entity: extract file_rel and entity_path
                        idx = parts.index("_entity")
                        file_rel = "/".join(parts[:idx])
                        entity_path = "/".join(parts[idx + 1:])
                        from storage.virtual_props import enrich_meta
                        meta = enrich_meta(raw, self._project_path, file_rel, entity_path)
                    else:
                        # File-level
                        from storage.virtual_props import enrich_meta
                        meta = enrich_meta(raw, self._project_path, rel_dir)
                    yield (rel_dir, meta)

    def walk_entities(self, file_path: str,
                      entity_pattern: str = "*") -> Iterator[Tuple[str, dict]]:
        """Walk entities under a file. Yields (entity_rel_path, enriched_meta)."""
        entity_root = os.path.join(self._pontis_root, file_path, "_entity")
        if not os.path.exists(entity_root):
            return

        for root, dirs, files in os.walk(entity_root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for d in dirs:
                if fnmatch.fnmatch(d, entity_pattern):
                    entity_rel = os.path.relpath(os.path.join(root, d), entity_root)
                    meta = self.get_meta(file_path, entity_rel)
                    if meta is not None:
                        yield (entity_rel, meta)

    @staticmethod
    def _read_raw_meta_by_path(meta_path: str) -> Optional[dict]:
        """Read a specific _meta.yml by absolute path."""
        try:
            with open(meta_path, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return None

    # ==================== Content Retrieval ====================

    def read_raw_content(self, file_path: str, entity_path: str) -> Optional[str]:
        """Read _raw content for an entity (chunk text etc.).

        Returns None if _raw file doesn't exist.
        """
        raw_path = os.path.join(
            self._pontis_root, file_path, "_entity", entity_path, "_raw"
        )
        if not os.path.exists(raw_path):
            return None
        try:
            with open(raw_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception:
            return None

    def resolve_db_path(self, file_rel_path: str) -> Optional[str]:
        """Resolve the actual database file path from metadata.

        Reads meta.path from _meta.yml and resolves to absolute path.
        """
        meta = self._read_raw_meta(file_rel_path)
        if not meta:
            return None
        source_path = meta.get('path')
        if not source_path:
            return None
        db_path = os.path.join(self._project_path, source_path)
        return db_path if os.path.exists(db_path) else None

    # ==================== Physical File System ====================

    def glob_physical_files(self, pattern: str, cwd: str = "") -> List[str]:
        """Glob physical files in the project directory.

        Returns relative paths. Excludes .pontis, .git, and hidden dirs.
        Sorted by modification time (newest first).
        """
        search_root = os.path.join(self._project_path, cwd) if cwd else self._project_path
        full_pattern = os.path.join(search_root, pattern)

        matches = _glob.glob(full_pattern, recursive=True)

        results = []
        for m in matches:
            rel = os.path.relpath(m, self._project_path)
            # Skip .pontis and hidden dirs
            parts = rel.split(os.sep)
            if '.pontis' in parts:
                continue
            if any(p.startswith('.') for p in parts):
                continue
            results.append(rel)

        results.sort(
            key=lambda p: os.path.getmtime(os.path.join(self._project_path, p)),
            reverse=True
        )
        return results

    def file_exists(self, path: str) -> bool:
        return os.path.exists(os.path.join(self._project_path, path))

    def is_directory(self, path: str) -> bool:
        return os.path.isdir(os.path.join(self._project_path, path))

    def list_dir(self, path: str) -> List[str]:
        """List non-hidden entries in a directory."""
        full = os.path.join(self._project_path, path)
        try:
            return [e for e in os.listdir(full) if not e.startswith('.')]
        except Exception:
            return []

    def get_file_size(self, path: str) -> int:
        return os.path.getsize(os.path.join(self._project_path, path))

    def read_physical_file(self, path: str, offset: int = 1,
                           limit: Optional[int] = None) -> str:
        """Read a physical file with line numbers.

        Returns cat -n style output.
        """
        full = os.path.join(self._project_path, path)
        try:
            with open(full, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            total = len(lines)
            start = max(0, offset - 1)

            if start >= total:
                return f"Warning: offset ({offset}) beyond file length ({total} lines)."

            if limit is not None:
                end = min(start + limit, total)
            else:
                end = total
                if end - start > 2000:
                    end = start + 2000

            output = []
            for i, line in enumerate(lines[start:end], start=start + 1):
                output.append(f"{i}\t{line.rstrip()}")

            if end < total:
                remaining = total - end
                output.append(
                    f"File has {remaining} more lines after line {end} (total {total})."
                )
            return '\n'.join(output)
        except Exception as e:
            return f"Error reading file: {e}"
