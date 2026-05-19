"""Utilities for Pontis extractor.

Contains: Config and common utilities. LLM client moved to utils/llm.py.
"""
import os
from dataclasses import dataclass
from typing import Optional

from utils.llm import LLMClient, apply_yaml
from utils.embedding import EmbeddingConfig


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
    llm_thinking: bool = True
    llm_thinking_effort: str = "high"
    brief_max_words: int = 20
    table_brief_max_words: int = 15
    log_level: str = "INFO"
    embedding_provider: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: Optional[str] = None
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 64

    def get_llm(self) -> Optional[LLMClient]:
        """从此配置创建 LLM 客户端。"""
        if not self.llm_enabled or not self.llm_api_key:
            return None
        return LLMClient(
            api_key=self.llm_api_key,
            provider=self.llm_provider,
            model=self.llm_model,
            thinking=self.llm_thinking,
            thinking_effort=self.llm_thinking_effort,
        )

    def get_embedding_config(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            enabled=bool(self.embedding_api_key),
            provider=self.embedding_provider,
            model=self.embedding_model,
            api_key=self.embedding_api_key or "",
            dimensions=self.embedding_dimensions,
            batch_size=self.embedding_batch_size,
        )


def load_config(path: Optional[str] = None) -> Config:
    """从默认值 + YAML + 环境变量加载 extractor 配置。"""
    import global_config as _defaults

    cfg = {
        "provider": _defaults.EXTRACTOR_PROVIDER,
        "model": _defaults.EXTRACTOR_MODEL,
        "api_key": _defaults.EXTRACTOR_API_KEY,
        "max_tokens": _defaults.EXTRACTOR_MAX_TOKENS,
        "temperature": _defaults.EXTRACTOR_TEMPERATURE,
        "thinking": _defaults.EXTRACTOR_THINKING,
        "thinking_effort": _defaults.EXTRACTOR_THINKING_EFFORT,
        "pontis_dir_name": _defaults.PONTIS_DIR_NAME,
        "meta_filename": _defaults.META_FILENAME,
        "sample_size": _defaults.SAMPLE_SIZE,
        "top_k": _defaults.TOP_K,
        "log_level": _defaults.LOG_LEVEL,
        "embedding_provider": _defaults.EMBEDDING_PROVIDER,
        "embedding_model": _defaults.EMBEDDING_MODEL,
        "embedding_api_key": _defaults.EMBEDDING_API_KEY,
        "embedding_dimensions": _defaults.EMBEDDING_DIMENSIONS,
        "embedding_batch_size": _defaults.EMBEDDING_BATCH_SIZE,
    }

    EXTRACTOR_YAML_MAPPING = {
        "extractor_provider": "provider",
        "extractor_model": "model",
        "extractor_api_key": "api_key",
        "extractor_max_tokens": "max_tokens",
        "extractor_temperature": "temperature",
        "pontis_dir_name": "pontis_dir_name",
        "meta_filename": "meta_filename",
        "sample_size": "sample_size",
        "top_k": "top_k",
        "log_level": "log_level",
        "embedding_provider": "embedding_provider",
        "embedding_model": "embedding_model",
        "embedding_api_key": "embedding_api_key",
        "embedding_dimensions": "embedding_dimensions",
        "embedding_batch_size": "embedding_batch_size",
    }

    # 1. ~/.pontis/config.yml
    apply_yaml(cfg, os.path.expanduser("~/.pontis/config.yml"), EXTRACTOR_YAML_MAPPING)

    # 2. 项目级 pontis.yml
    if path:
        project_dir = os.path.dirname(path) if os.path.isfile(path) else path
        for filename in ["pontis.yml", "pontis.yaml"]:
            apply_yaml(cfg, os.path.join(project_dir, filename), EXTRACTOR_YAML_MAPPING)

    # 3. 环境变量覆盖
    env_key = os.environ.get("OPENAI_API_KEY", "")
    env_url = os.environ.get("OPENAI_BASE_URL", "")
    if not cfg["api_key"]:
        cfg["api_key"] = env_key
    if env_url and cfg["provider"] == "https://api.deepseek.com":
        cfg["provider"] = env_url
    if not cfg["embedding_api_key"]:
        cfg["embedding_api_key"] = (
            os.environ.get("PONTIS_EMBEDDING_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
    if not cfg["embedding_provider"]:
        cfg["embedding_provider"] = (
            os.environ.get("PONTIS_EMBEDDING_PROVIDER")
            or os.environ.get("OPENAI_BASE_URL", "")
        )

    return Config(
        pontis_dir_name=cfg["pontis_dir_name"],
        meta_filename=cfg["meta_filename"],
        sample_size=cfg["sample_size"],
        top_k=cfg["top_k"],
        log_level=cfg["log_level"],
        llm_provider=cfg["provider"],
        llm_model=cfg["model"],
        llm_api_key=cfg["api_key"] or None,
        llm_enabled=bool(cfg["api_key"]),
        llm_thinking=cfg.get("thinking", True),
        llm_thinking_effort=cfg.get("thinking_effort", "high"),
        embedding_provider=cfg.get("embedding_provider", ""),
        embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
        embedding_api_key=cfg.get("embedding_api_key") or None,
        embedding_dimensions=int(cfg.get("embedding_dimensions") or 1536),
        embedding_batch_size=int(cfg.get("embedding_batch_size") or 64),
    )
