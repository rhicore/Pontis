"""Pontis 全局默认配置值。"""
import os
from pathlib import Path

# 加载项目根目录 .env 文件
_dotenv = Path(__file__).resolve().parent / ".env"
if _dotenv.exists():
    for line in _dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# Extractor profile
EXTRACTOR_PROVIDER = "https://api.deepseek.com"
EXTRACTOR_MODEL = "deepseek-v4-flash"
EXTRACTOR_API_KEY = os.environ.get("PONTIS_EXTRACTOR_API_KEY", "")
EXTRACTOR_MAX_TOKENS = 2000
EXTRACTOR_TEMPERATURE = 0.2
EXTRACTOR_THINKING = True
EXTRACTOR_THINKING_EFFORT = "high"

# Shared
PONTIS_DIR_NAME = ".pontis"
META_FILENAME = "_meta.yml"
SAMPLE_SIZE = 5
TOP_K = 5
LOG_LEVEL = "INFO"

# Agent profile
AGENT_PROVIDER = "https://api.deepseek.com"
AGENT_MODEL = "deepseek-v4-flash"
AGENT_API_KEY = os.environ.get("PONTIS_AGENT_API_KEY", "")
AGENT_MAX_TOKENS = 4096
AGENT_TEMPERATURE = 0.3
AGENT_THINKING = True
AGENT_THINKING_EFFORT = "high"
