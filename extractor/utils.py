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
