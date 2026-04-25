"""Agent 工具函数

动态配置加载等杂项函数。
"""
import os
from utils.llm import apply_yaml


def load_agent_config(project_path: str = None) -> dict:
    """加载 agent LLM 配置，返回包含 provider/model/api_key 等的 dict。"""
    from agent.config import (AGENT_PROVIDER, AGENT_MODEL, AGENT_API_KEY,
                                AGENT_MAX_TOKENS, AGENT_TEMPERATURE,
                                AGENT_THINKING, AGENT_THINKING_EFFORT)

    cfg = {
        "provider": AGENT_PROVIDER,
        "model": AGENT_MODEL,
        "api_key": AGENT_API_KEY,
        "max_tokens": AGENT_MAX_TOKENS,
        "temperature": AGENT_TEMPERATURE,
        "effort": os.environ.get("PONTIS_EFFORT", "mid"),
        "thinking": AGENT_THINKING,
        "thinking_effort": AGENT_THINKING_EFFORT,
    }

    AGENT_YAML_MAPPING = {
        "agent_provider": "provider",
        "agent_model": "model",
        "agent_api_key": "api_key",
        "agent_max_tokens": "max_tokens",
        "agent_temperature": "temperature",
        "agent_effort": "effort",
        "agent_thinking": "thinking",
        "agent_thinking_effort": "thinking_effort",
    }

    # 1. ~/.pontis/config.yml
    apply_yaml(cfg, os.path.expanduser("~/.pontis/config.yml"), AGENT_YAML_MAPPING)

    # 2. 项目级 pontis.yml
    if project_path:
        for filename in ["pontis.yml", "pontis.yaml"]:
            apply_yaml(cfg, os.path.join(project_path, filename), AGENT_YAML_MAPPING)

    # 3. 环境变量覆盖
    env_key = os.environ.get("OPENAI_API_KEY", "")
    env_url = os.environ.get("OPENAI_BASE_URL", "")
    if not cfg["api_key"]:
        cfg["api_key"] = env_key
    if env_url and cfg["provider"] == "https://api.deepseek.com":
        cfg["provider"] = env_url

    return cfg
