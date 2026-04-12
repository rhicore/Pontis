"""Pontis 全局配置

两个 LLM profile:
- extractor: 廉价模型，用于元数据提取和总结
- agent: 强推理模型，用于交互分析

配置加载优先级:
1. ~/.pontis/config.yml（全局用户配置）
2. <project>/pontis.yml（项目级覆盖）
3. 环境变量（OPENAI_API_KEY, OPENAI_BASE_URL）
"""
import os
from dataclasses import dataclass
from typing import Optional

import yaml


@dataclass
class LLMConfig:
    """LLM 连接配置"""
    provider: str = ""
    model: str = ""
    api_key: str = ""
    max_tokens: int = 4096
    temperature: float = 0.3

    def create_client(self):
        """创建 OpenAI 兼容客户端。"""
        from openai import OpenAI
        return OpenAI(api_key=self.api_key, base_url=self.provider)


@dataclass
class PontisConfig:
    """Pontis 全局配置"""
    # Extractor profile（廉价模型）
    extractor_provider: str = "https://api.deepseek.com"
    extractor_model: str = "deepseek-chat"
    extractor_api_key: str = "sk-9cf27bbb303c44709d26b60c691e5edb"
    extractor_max_tokens: int = 2000
    extractor_temperature: float = 0.2

    # Agent profile（强推理模型）
    agent_provider: str = "https://api.deepseek.com"
    agent_model: str = "deepseek-reasoner"
    agent_api_key: str = "sk-9cf27bbb303c44709d26b60c691e5edb"
    agent_max_tokens: int = 4096
    agent_temperature: float = 0.3

    # 共享配置
    pontis_dir_name: str = ".pontis"
    meta_filename: str = "_meta.yml"
    sample_size: int = 5
    top_k: int = 5
    log_level: str = "INFO"

    def extractor_llm(self) -> LLMConfig:
        return LLMConfig(
            provider=self.extractor_provider,
            model=self.extractor_model,
            api_key=self.extractor_api_key,
            max_tokens=self.extractor_max_tokens,
            temperature=self.extractor_temperature,
        )

    def agent_llm(self) -> LLMConfig:
        return LLMConfig(
            provider=self.agent_provider,
            model=self.agent_model,
            api_key=self.agent_api_key,
            max_tokens=self.agent_max_tokens,
            temperature=self.agent_temperature,
        )


def load_config(project_path: str = None) -> PontisConfig:
    """加载配置。"""
    config = PontisConfig()

    # 1. ~/.pontis/config.yml
    home_config = os.path.expanduser("~/.pontis/config.yml")
    if os.path.exists(home_config):
        with open(home_config, 'r') as f:
            data = yaml.safe_load(f) or {}
        config = _apply_dict(config, data)

    # 2. 项目级 pontis.yml
    if project_path:
        for filename in ["pontis.yml", "pontis.yaml"]:
            path = os.path.join(project_path, filename)
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = yaml.safe_load(f) or {}
                config = _apply_dict(config, data)
                break

    # 3. 环境变量覆盖
    shared_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")

    if not config.extractor_api_key:
        config.extractor_api_key = shared_key
    if not config.agent_api_key:
        config.agent_api_key = shared_key
    if base_url:
        if not config.extractor_provider or config.extractor_provider == "https://api.deepseek.com":
            config.extractor_provider = base_url
        if not config.agent_provider or config.agent_provider == "https://api.deepseek.com":
            config.agent_provider = base_url

    return config


def _apply_dict(config: PontisConfig, data: dict) -> PontisConfig:
    """将 dict 值应用到 config dataclass。"""
    for k, v in data.items():
        if hasattr(config, k):
            setattr(config, k, v)
    return config
