"""Utilities for Pontis extractor.

Contains: Config, VFSStorage, LLM client, and common utilities.
"""
import os
import yaml
import pickle
import fnmatch
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


# ==================== Configuration ====================

@dataclass
class Config:
    """Pontis extractor configuration"""
    pontis_dir_name: str = ".pontis"
    meta_filename: str = "_meta.yml"
    sample_size: int = 5
    top_k: int = 5
    llm_enabled: bool = False
    llm_provider: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_api_key: Optional[str] = None
    brief_max_words: int = 20
    table_brief_max_words: int = 15
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: str) -> "Config":
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return cls(**data)


def load_config(path: Optional[str] = None) -> Config:
    if path and os.path.exists(path):
        return Config.from_file(path)
    for p in ["pontis.yml", "pontis.yaml", os.path.expanduser("~/.pontis/config.yml")]:
        if os.path.exists(p):
            return Config.from_file(p)
    return Config()


# ==================== Node Reference ====================

class NodeRef:
    """Reference to a node in the VFS tree."""

    def __init__(self, rel_path: str, pontis_root: str):
        self.rel_path = rel_path
        self.pontis_root = pontis_root

    @property
    def full_path(self) -> str:
        return os.path.join(self.pontis_root, self.rel_path)

    @property
    def meta_path(self) -> str:
        return os.path.join(self.full_path, "_meta.yml")

    @property
    def name(self) -> str:
        return os.path.basename(self.rel_path)

    @property
    def suffix(self) -> Optional[str]:
        name = self.name
        if "." in name:
            return name[name.rfind("."):]
        return None

    @property
    def stem(self) -> str:
        name = self.name
        if "." in name:
            return name[:name.rfind(".")]
        return name


# ==================== VFS Storage ====================

class VFSStorage:
    """Storage backend for Pontis VFS metadata."""

    def __init__(self, pontis_root: str):
        self.pontis_root = pontis_root
        self.project_root = os.path.dirname(pontis_root)  # .pontis 的父目录
        self._cache: Dict[str, Dict[str, Any]] = {}

    def resolve_path(self, rel_path: str) -> str:
        """将相对于项目根目录的路径解析为绝对路径"""
        return os.path.join(self.project_root, rel_path)

    def read_meta(self, node: NodeRef) -> Optional[Dict[str, Any]]:
        cache_key = node.rel_path
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        if not os.path.exists(node.meta_path):
            return None

        try:
            with open(node.meta_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self._cache[cache_key] = data
            return data.copy()
        except Exception as e:
            logger.warning(f"Failed to read {node.meta_path}: {e}")
            return None

    def write_meta(self, node: NodeRef, data: Dict[str, Any]) -> None:
        self.ensure_dir(node.full_path)
        with open(node.meta_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        self._cache[node.rel_path] = data

    def ensure_dir(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def write_bin(self, node: NodeRef, filename: str, data: bytes) -> None:
        """Write binary data (deprecated, use write_raw for JSON data)."""
        self.ensure_dir(node.full_path)
        with open(os.path.join(node.full_path, filename), 'wb') as f:
            f.write(data)

    def read_bin(self, node: NodeRef, filename: str) -> Optional[bytes]:
        """Read binary data."""
        path = os.path.join(node.full_path, filename)
        if not os.path.exists(path):
            return None
        with open(path, 'rb') as f:
            return f.read()

    def write_raw(self, node: NodeRef, data: Any) -> None:
        """Write JSON-serializable data to _raw file."""
        import json
        self.ensure_dir(node.full_path)
        raw_path = os.path.join(node.full_path, "_raw")
        with open(raw_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_raw(self, node: NodeRef) -> Optional[Any]:
        """Read JSON data from _raw file."""
        import json
        raw_path = os.path.join(node.full_path, "_raw")
        if not os.path.exists(raw_path):
            return None
        with open(raw_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def write_text(self, node: NodeRef, content: str) -> None:
        """Write raw text content to _raw file (without JSON wrapping)."""
        self.ensure_dir(node.full_path)
        raw_path = os.path.join(node.full_path, "_raw")
        with open(raw_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def read_text(self, node: NodeRef) -> Optional[str]:
        """Read raw text content from _raw file."""
        raw_path = os.path.join(node.full_path, "_raw")
        if not os.path.exists(raw_path):
            return None
        with open(raw_path, 'r', encoding='utf-8') as f:
            return f.read()

    def list_children(self, node: NodeRef) -> List[NodeRef]:
        if not os.path.isdir(node.full_path):
            return []
        children = []
        for name in os.listdir(node.full_path):
            if name.startswith('_') or name.startswith('.'):
                continue
            child_path = os.path.join(node.full_path, name)
            if os.path.isdir(child_path):
                children.append(NodeRef(os.path.join(node.rel_path, name), self.pontis_root))
        return children

    def find_nodes(self, pattern: str) -> List[NodeRef]:
        results = []
        for root, dirs, files in os.walk(self.pontis_root):
            dirs[:] = [d for d in dirs if not d.startswith('_') and not d.startswith('.')]
            for d in dirs:
                full_path = os.path.join(root, d)
                rel_path = os.path.relpath(full_path, self.pontis_root)
                if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(d, pattern):
                    results.append(NodeRef(rel_path, self.pontis_root))
        return results

    def exists(self, node: NodeRef) -> bool:
        """Check if a node exists in the VFS."""
        return os.path.exists(node.full_path)


# ==================== LLM Client ====================

class LLMClient:
    """Simple LLM client wrapper."""

    def __init__(self, config: Config):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(
                    api_key=self.config.llm_api_key,
                    base_url=self.config.llm_provider
                )
            except ImportError:
                logger.error("OpenAI package not installed")
        return self._client

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        client = self._get_client()
        if not client:
            return ""
        try:
            response = client.chat.completions.create(
                model=self.config.llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""


def get_llm(config: Optional[Config] = None) -> Optional[LLMClient]:
    """Get LLM client. If config not provided, loads default config."""
    if config is None:
        config = load_config()
    if not config.llm_enabled or not config.llm_api_key:
        return None
    return LLMClient(config)
