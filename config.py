"""Pontis 全局静态配置

只存配置值，不含任何函数。
加载逻辑由各模块自行实现：
  - agent/utils.py → load_agent_config()
  - extractor/modules/utils/config.py → load_config()

配置来源（优先级从高到低）：
1. ~/.pontis/config.yml（全局用户配置）
2. <project>/pontis.yml（项目级覆盖）
3. 环境变量（PONTIS_API_KEY, OPENAI_API_KEY 等）
4. 此文件中的默认值
"""
import os

# Extractor profile（廉价模型，用于元数据提取和总结）
EXTRACTOR_PROVIDER = "https://api.deepseek.com"
EXTRACTOR_MODEL = "deepseek-v4-flash"
EXTRACTOR_API_KEY = os.environ.get("PONTIS_EXTRACTOR_API_KEY", "")
EXTRACTOR_MAX_TOKENS = 2000
EXTRACTOR_TEMPERATURE = 0.2
EXTRACTOR_THINKING = True
EXTRACTOR_THINKING_EFFORT = "high"

# Agent profile（强推理模型，用于交互分析）
AGENT_PROVIDER = "https://api.deepseek.com"
AGENT_MODEL = "deepseek-v4-flash"
AGENT_API_KEY = os.environ.get("PONTIS_AGENT_API_KEY", "")
AGENT_MAX_TOKENS = 4096
AGENT_TEMPERATURE = 0.3
AGENT_THINKING = True
AGENT_THINKING_EFFORT = "high"

# 共享配置
PONTIS_DIR_NAME = ".pontis"
META_FILENAME = "_meta.yml"
SAMPLE_SIZE = 5
TOP_K = 5
LOG_LEVEL = "INFO"
