"""Agent 工具函数

动态配置加载等杂项函数。
"""
import os
from utils.llm import apply_yaml


def load_agent_config(project_path: str = None) -> dict:
    """加载 agent LLM 配置，返回包含 provider/model/api_key 等的 dict。"""
    import global_config as _defaults

    cfg = {
        "provider": _defaults.AGENT_PROVIDER,
        "model": _defaults.AGENT_MODEL,
        "api_key": _defaults.AGENT_API_KEY,
        "temperature": _defaults.AGENT_TEMPERATURE,
        "effort": os.environ.get("PONTIS_EFFORT", "mid"),
        "thinking": _defaults.AGENT_THINKING,
        "thinking_effort": _defaults.AGENT_THINKING_EFFORT,
    }

    AGENT_YAML_MAPPING = {
        "agent_provider": "provider",
        "agent_model": "model",
        "agent_api_key": "api_key",
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
    model_url = os.environ.get("MODEL_API_URL", "")
    model_key = os.environ.get("MODEL_API_KEY", "")
    model_name = os.environ.get("MODEL_NAME", "")
    env_key = os.environ.get("OPENAI_API_KEY", "")
    env_url = os.environ.get("OPENAI_BASE_URL", "")
    env_model = os.environ.get("PONTIS_AGENT_MODEL", "")

    if model_url:
        cfg["provider"] = model_url
        cfg["thinking"] = False
    elif env_url and cfg["provider"] == "https://api.deepseek.com":
        cfg["provider"] = env_url

    if model_key:
        cfg["api_key"] = model_key
    elif not cfg["api_key"]:
        cfg["api_key"] = env_key

    if model_name:
        cfg["model"] = model_name
    elif env_model:
        cfg["model"] = env_model

    return cfg
