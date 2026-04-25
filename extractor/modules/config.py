"""Pontis Extractor 默认配置值。"""
import os

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
