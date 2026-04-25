"""Pontis Agent 默认配置值。"""
import os
from pathlib import Path

# 加载 .env 文件
_dotenv = Path(__file__).resolve().parent.parent / ".env"
if _dotenv.exists():
    for line in _dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# Agent profile
AGENT_PROVIDER = "https://api.deepseek.com"
AGENT_MODEL = "deepseek-v4-flash"
AGENT_API_KEY = os.environ.get("PONTIS_AGENT_API_KEY", "")
AGENT_MAX_TOKENS = 4096
AGENT_TEMPERATURE = 0.3
AGENT_THINKING = True
AGENT_THINKING_EFFORT = "high"
