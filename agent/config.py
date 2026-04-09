"""Agent configuration - reads from common.config."""
from common.config import load_config as _load_pontis_config


def load_agent_config(project_path: str = None):
    """Load agent LLM config via common.config.PontisConfig."""
    cfg = _load_pontis_config(project_path)
    return cfg.agent_llm()
