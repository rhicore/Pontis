"""Agent 配置加载

从 config.py 默认值 + YAML + 环境变量加载 agent LLM 配置。
"""
import os
import yaml

from openai import OpenAI


def load_agent_config(project_path: str = None) -> dict:
    """加载 agent LLM 配置，返回包含 provider/model/api_key 等的 dict。"""
    from config import (AGENT_PROVIDER, AGENT_MODEL, AGENT_API_KEY,
                        AGENT_MAX_TOKENS, AGENT_TEMPERATURE)

    cfg = {
        "provider": AGENT_PROVIDER,
        "model": AGENT_MODEL,
        "api_key": AGENT_API_KEY,
        "max_tokens": AGENT_MAX_TOKENS,
        "temperature": AGENT_TEMPERATURE,
    }

    # 1. ~/.pontis/config.yml
    _apply_yaml(cfg, os.path.expanduser("~/.pontis/config.yml"))

    # 2. 项目级 pontis.yml
    if project_path:
        for filename in ["pontis.yml", "pontis.yaml"]:
            _apply_yaml(cfg, os.path.join(project_path, filename))

    # 3. 环境变量覆盖
    env_key = os.environ.get("OPENAI_API_KEY", "")
    env_url = os.environ.get("OPENAI_BASE_URL", "")
    if not cfg["api_key"]:
        cfg["api_key"] = env_key
    if env_url and cfg["provider"] == "https://api.deepseek.com":
        cfg["provider"] = env_url

    return cfg


def _apply_yaml(cfg: dict, path: str):
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        data = yaml.safe_load(f) or {}
    mapping = {
        "agent_provider": "provider",
        "agent_model": "model",
        "agent_api_key": "api_key",
        "agent_max_tokens": "max_tokens",
        "agent_temperature": "temperature",
    }
    for yaml_key, cfg_key in mapping.items():
        if yaml_key in data:
            cfg[cfg_key] = data[yaml_key]
