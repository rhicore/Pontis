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


def load_config(path: Optional[str] = None) -> Config:
    """Load extractor config, bridging from config.PontisConfig."""
    from config import load_config as _load_pontis_config
    pontis_cfg = _load_pontis_config(path)

    return Config(
        pontis_dir_name=pontis_cfg.pontis_dir_name,
        meta_filename=pontis_cfg.meta_filename,
        sample_size=pontis_cfg.sample_size,
        top_k=pontis_cfg.top_k,
        log_level=pontis_cfg.log_level,
        llm_provider=pontis_cfg.extractor_provider,
        llm_model=pontis_cfg.extractor_model,
        llm_api_key=pontis_cfg.extractor_api_key or None,
        llm_enabled=bool(pontis_cfg.extractor_api_key),
    )


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

    def list_children(self, node: NodeRef) -> List[NodeRef]:
        if not os.path.isdir(node.full_path):
            return []
        children = []
        for name in os.listdir(node.full_path):
            if name.startswith('.'):
                continue
            child_path = os.path.join(node.full_path, name)
            if os.path.isdir(child_path):
                children.append(NodeRef(os.path.join(node.rel_path, name), self.pontis_root))
        return children

    def find_nodes(self, pattern: str) -> List[NodeRef]:
        results = []
        for root, dirs, files in os.walk(self.pontis_root):
            # Only skip hidden dirs (.git, etc.), NOT _entity/_meta internal dirs
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for d in dirs:
                full_path = os.path.join(root, d)
                rel_path = os.path.relpath(full_path, self.pontis_root)
                if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(d, pattern):
                    results.append(NodeRef(rel_path, self.pontis_root))
        return results

    def exists(self, node: NodeRef) -> bool:
        """Check if a node exists in the VFS."""
        return os.path.exists(node.full_path)

    # ==================== Edge Storage ====================

    def _edges_path(self) -> str:
        return os.path.join(self.pontis_root, "_edges.yml")

    def read_edges(self) -> List[Dict[str, str]]:
        """读取所有边"""
        path = self._edges_path()
        if not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return data.get("edges", [])

    def write_edges(self, edges: List[Dict[str, str]]) -> None:
        """写入所有边"""
        os.makedirs(self.pontis_root, exist_ok=True)
        with open(self._edges_path(), 'w', encoding='utf-8') as f:
            yaml.dump({"edges": edges}, f, default_flow_style=False, allow_unicode=True)

    def add_edge(self, from_ref: str, edge_type: str, to_ref: str) -> None:
        """添加一条边"""
        edges = self.read_edges()
        key = (from_ref, edge_type, to_ref)
        if any((e["from"], e["type"], e["to"]) == key for e in edges):
            return
        edges.append({"from": from_ref, "type": edge_type, "to": to_ref})
        self.write_edges(edges)

    def add_edges(self, edge_list: List[Dict[str, str]]) -> None:
        """批量添加边"""
        edges = self.read_edges()
        existing = {(e["from"], e["type"], e["to"]) for e in edges}
        for e in edge_list:
            key = (e["from"], e["type"], e["to"])
            if key not in existing:
                edges.append(e)
                existing.add(key)
        self.write_edges(edges)

    def find_edges(self, from_ref: str = None, edge_type: str = None, to_ref: str = None) -> List[Dict[str, str]]:
        """查询边"""
        edges = self.read_edges()
        return [e for e in edges
                if (from_ref is None or e["from"] == from_ref)
                and (edge_type is None or e["type"] == edge_type)
                and (to_ref is None or e["to"] == to_ref)]


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
