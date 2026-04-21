"""Utilities for Pontis extractor.

Contains: Config, LLM client, and common utilities.
"""
import os
import yaml
import logging
from dataclasses import dataclass
from typing import Optional

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
    """从默认值 + YAML + 环境变量加载 extractor 配置。"""
    import config as _defaults

    cfg = {
        "provider": _defaults.EXTRACTOR_PROVIDER,
        "model": _defaults.EXTRACTOR_MODEL,
        "api_key": _defaults.EXTRACTOR_API_KEY,
        "max_tokens": _defaults.EXTRACTOR_MAX_TOKENS,
        "temperature": _defaults.EXTRACTOR_TEMPERATURE,
        "pontis_dir_name": _defaults.PONTIS_DIR_NAME,
        "meta_filename": _defaults.META_FILENAME,
        "sample_size": _defaults.SAMPLE_SIZE,
        "top_k": _defaults.TOP_K,
        "log_level": _defaults.LOG_LEVEL,
    }

    # 1. ~/.pontis/config.yml
    _apply_yaml(cfg, os.path.expanduser("~/.pontis/config.yml"))

    # 2. 项目级 pontis.yml
    if path:
        project_dir = os.path.dirname(path) if os.path.isfile(path) else path
        for filename in ["pontis.yml", "pontis.yaml"]:
            _apply_yaml(cfg, os.path.join(project_dir, filename))

    # 3. 环境变量覆盖
    env_key = os.environ.get("OPENAI_API_KEY", "")
    env_url = os.environ.get("OPENAI_BASE_URL", "")
    if not cfg["api_key"]:
        cfg["api_key"] = env_key
    if env_url and cfg["provider"] == "https://api.deepseek.com":
        cfg["provider"] = env_url

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
    )


def _apply_yaml(cfg: dict, path: str):
    """将 YAML 文件值应用到 config dict。"""
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        data = yaml.safe_load(f) or {}
    mapping = {
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
    }
    for yaml_key, cfg_key in mapping.items():
        if yaml_key in data:
            cfg[cfg_key] = data[yaml_key]


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

    def complete_messages(self, messages: list, max_tokens: int = 500) -> str:
        """用完整消息列表调用 LLM（支持 prompt caching 前缀共享）。"""
        client = self._get_client()
        if not client:
            return ""
        try:
            response = client.chat.completions.create(
                model=self.config.llm_model,
                messages=messages,
                max_tokens=max_tokens,
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
