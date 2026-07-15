"""Method-local Text2SQL baseline configuration.

This file is local to this baseline method and defines its model and embedding defaults. Secrets are loaded from the repository-root `.env` file and are not duplicated into per-baseline config files.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TEXT2SQL_ROOT = ROOT_DIR.parent
DOTENV_PATH = TEXT2SQL_ROOT / ".env"


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _set_default(name: str, value: str | int | float | bool) -> None:
    if value is None or value == "":
        return
    os.environ.setdefault(name, str(value))


_load_dotenv()

RUN_ID = _first_env(
    "TEXT2SQL_RUN_ID",
    "PONTIS_BIRD_RUN_ID",
    "BASH_AGENT_RUN_ID",
    "DEEPEYE_SQL_RUN_ID",
    "ALPHA_SQL_RUN_ID",
    "CHESS_RUN_ID",
    "BASELINE_RUN_ID",
    default=datetime.now().strftime("%Y%m%d_%H%M%S"),
)

# Shared LLM profile for OpenAI-compatible chat APIs, including local vLLM.
MODEL_PROVIDER = _first_env(
    "MODEL_API_URL",
    "DEEPSEEK_BASE_URL",
    "OPENAI_BASE_URL",
    default="https://api.deepseek.com",
)
MODEL_API_KEY = _first_env(
    "MODEL_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "PONTIS_AGENT_API_KEY",
    "PONTIS_EXTRACTOR_API_KEY",
)
MODEL_NAME = _first_env(
    "MODEL_NAME",
    "DEEPSEEK_MODEL",
    "OPENAI_MODEL",
    "PONTIS_AGENT_MODEL",
    "PONTIS_EXTRACTOR_MODEL",
    default="deepseek-v4-flash",
)
MODEL_TEMPERATURE = float(_first_env("MODEL_TEMPERATURE", "OPENAI_TEMPERATURE", default="0.3"))
MODEL_TOP_P = float(_first_env("MODEL_TOP_P", "OPENAI_TOP_P", default="1.0"))
MODEL_MAX_TOKENS = int(_first_env("MODEL_MAX_TOKENS", "OPENAI_MAX_TOKENS", default="4096"))
MODEL_MAX_CONTEXT = int(_first_env("MODEL_MAX_CONTEXT", "MODEL_MAX_MODEL_LEN", "OPENAI_MAX_CONTEXT", default="24000"))

# Shared embedding profile. Current default matches the existing Pontis setup.
EMBEDDING_PROVIDER = _first_env(
    "EMBEDDING_BASE_URL",
    "PONTIS_EMBEDDING_PROVIDER",
    "DASHSCOPE_BASE_URL",
    default="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
EMBEDDING_MODEL = _first_env(
    "EMBEDDING_MODEL",
    "PONTIS_EMBEDDING_MODEL",
    default="text-embedding-v4",
)
EMBEDDING_API_KEY = _first_env(
    "EMBEDDING_API_KEY",
    "PONTIS_EMBEDDING_API_KEY",
    "DASHSCOPE_API_KEY",
)
EMBEDDING_DIMENSIONS = int(_first_env("EMBEDDING_DIMENSIONS", "PONTIS_EMBEDDING_DIMENSIONS", default="1024"))
EMBEDDING_BATCH_SIZE = int(_first_env("EMBEDDING_BATCH_SIZE", "PONTIS_EMBEDDING_BATCH_SIZE", default="10"))
EMBEDDING_MIN_SIMILARITY = float(_first_env("PONTIS_EMBEDDING_MIN_SIMILARITY", default="0.68"))

# Pontis-compatible names.
EXTRACTOR_PROVIDER = MODEL_PROVIDER
EXTRACTOR_MODEL = MODEL_NAME
EXTRACTOR_API_KEY = _first_env("PONTIS_EXTRACTOR_API_KEY", default=MODEL_API_KEY)
EXTRACTOR_TEMPERATURE = float(_first_env("PONTIS_EXTRACTOR_TEMPERATURE", default="0.2"))
EXTRACTOR_THINKING = _first_env("PONTIS_EXTRACTOR_THINKING", default="true").lower() not in {"0", "false", "no"}
EXTRACTOR_THINKING_EFFORT = _first_env("PONTIS_EXTRACTOR_THINKING_EFFORT", default="high")

AGENT_PROVIDER = MODEL_PROVIDER
AGENT_MODEL = MODEL_NAME
AGENT_API_KEY = _first_env("PONTIS_AGENT_API_KEY", default=MODEL_API_KEY)
AGENT_TEMPERATURE = float(_first_env("PONTIS_AGENT_TEMPERATURE", default=str(MODEL_TEMPERATURE)))
AGENT_THINKING = _first_env("PONTIS_AGENT_THINKING", default="true").lower() not in {"0", "false", "no"}
AGENT_THINKING_EFFORT = _first_env("PONTIS_AGENT_THINKING_EFFORT", default="high")

PONTIS_DIR_NAME = ".pontis"
META_FILENAME = "_meta.yml"
SAMPLE_SIZE = 5
TOP_K = 5
LOG_LEVEL = "INFO"

# Make OpenAI-compatible baselines work without per-repo .env files.
_set_default("OPENAI_BASE_URL", MODEL_PROVIDER)
_set_default("OPENAI_API_KEY", MODEL_API_KEY)
_set_default("OPENAI_MODEL", MODEL_NAME)
_set_default("OPENAI_TEMPERATURE", MODEL_TEMPERATURE)
_set_default("OPENAI_TOP_P", MODEL_TOP_P)
_set_default("OPENAI_MAX_TOKENS", MODEL_MAX_TOKENS)
_set_default("MODEL_API_URL", MODEL_PROVIDER)
_set_default("MODEL_API_KEY", MODEL_API_KEY)
_set_default("MODEL_NAME", MODEL_NAME)
_set_default("MODEL_MAX_TOKENS", MODEL_MAX_TOKENS)
_set_default("MODEL_MAX_CONTEXT", MODEL_MAX_CONTEXT)
_set_default("MODEL_MAX_MODEL_LEN", MODEL_MAX_CONTEXT)
_set_default("TEXT2SQL_RUN_ID", RUN_ID)

_set_default("PONTIS_AGENT_API_KEY", AGENT_API_KEY)
_set_default("PONTIS_EXTRACTOR_API_KEY", EXTRACTOR_API_KEY)
_set_default("PONTIS_AGENT_MODEL", AGENT_MODEL)
_set_default("PONTIS_EXTRACTOR_MODEL", EXTRACTOR_MODEL)
_set_default("PONTIS_EMBEDDING_PROVIDER", EMBEDDING_PROVIDER)
_set_default("PONTIS_EMBEDDING_MODEL", EMBEDDING_MODEL)
_set_default("PONTIS_EMBEDDING_API_KEY", EMBEDDING_API_KEY)
_set_default("PONTIS_EMBEDDING_DIMENSIONS", EMBEDDING_DIMENSIONS)
_set_default("PONTIS_EMBEDDING_BATCH_SIZE", EMBEDDING_BATCH_SIZE)

_set_default("EMBEDDING_BASE_URL", EMBEDDING_PROVIDER)
_set_default("EMBEDDING_MODEL", EMBEDDING_MODEL)
_set_default("EMBEDDING_API_KEY", EMBEDDING_API_KEY)
