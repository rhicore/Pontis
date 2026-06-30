"""Pontis 共享 LLM 基础设施

提供：YAML 配置加载、LLM 客户端。
"""
import os
import threading
import yaml
import logging
import sys
from collections import Counter
from pathlib import Path

from utils.token_metrics import add_usage

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))
_TOOLS_ROOT = _ROOT / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.append(str(_TOOLS_ROOT))

from token_cache_accounting import normalize_cache_accounting, serialize_request  # noqa: E402

logger = logging.getLogger(__name__)


def apply_yaml(cfg: dict, path: str, mapping: dict):
    """将 YAML 文件值应用到 config dict。"""
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        data = yaml.safe_load(f) or {}
    for yaml_key, cfg_key in mapping.items():
        if yaml_key in data:
            cfg[cfg_key] = data[yaml_key]


class LLMClient:
    """Simple LLM client wrapper.

    每个线程持有独立的 openai.OpenAI 实例（httpx.Client 不是线程安全的）。
    """

    def __init__(self, api_key: str, provider: str, model: str,
                 thinking: bool = True, thinking_effort: str = "high"):
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.thinking = thinking
        self.thinking_effort = thinking_effort
        self._local = threading.local()
        self._metrics = Counter()
        self._metrics_lock = threading.Lock()

    def _get_client(self):
        client = getattr(self._local, 'client', None)
        if client is None:
            try:
                import openai
                client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.provider,
                )
                self._local.client = client
            except ImportError:
                logger.error("OpenAI package not installed")
        return client

    def _call(self, client, messages: list) -> str:
        kwargs = {}
        if self.thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = self.thinking_effort
        else:
            kwargs["temperature"] = 0.3
        try:
            prompt_text = serialize_request(messages)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                timeout=120,
                **kwargs,
            )
            self._record_usage(response, prompt_text=prompt_text)
            self._local.previous_prompt_text = prompt_text
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""

    def _record_usage(self, response, *, prompt_text: str | None = None) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        input_tokens = (
            getattr(usage, "prompt_tokens", None)
            if getattr(usage, "prompt_tokens", None) is not None
            else getattr(usage, "input_tokens", 0)
        )
        output_tokens = (
            getattr(usage, "completion_tokens", None)
            if getattr(usage, "completion_tokens", None) is not None
            else getattr(usage, "output_tokens", 0)
        )
        total_tokens = getattr(usage, "total_tokens", None)
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        total_tokens = int(total_tokens) if total_tokens is not None else input_tokens + output_tokens
        cache = normalize_cache_accounting(
            usage=usage,
            input_tokens=input_tokens,
            current_prompt=prompt_text,
            previous_prompt=getattr(self._local, "previous_prompt_text", None),
        )
        with self._metrics_lock:
            self._metrics["preprocess_llm_calls"] += 1
            self._metrics["preprocess_llm_input_tokens"] += input_tokens
            self._metrics["preprocess_llm_output_tokens"] += output_tokens
            self._metrics["preprocess_llm_total_tokens"] += total_tokens
            self._metrics["preprocess_llm_cached_input_tokens"] += cache["cached_input_tokens"]
            self._metrics["preprocess_llm_uncached_input_tokens"] += cache["uncached_input_tokens"]
            self._metrics["preprocess_llm_cache_hit_input_tokens"] += cache["cache_hit_input_tokens"]
            self._metrics["preprocess_llm_cache_miss_input_tokens"] += cache["cache_miss_input_tokens"]
            self._metrics["preprocess_llm_cache_unknown_input_tokens"] += cache["cache_unknown_input_tokens"]
            self._metrics["preprocess_llm_fresh_input_tokens"] += cache["fresh_input_tokens"]
        add_usage(
            "preprocess_llm",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cache["cached_input_tokens"],
            uncached_input_tokens=cache["uncached_input_tokens"],
            cache_hit_input_tokens=cache["cache_hit_input_tokens"],
            cache_miss_input_tokens=cache["cache_miss_input_tokens"],
            cache_unknown_input_tokens=cache["cache_unknown_input_tokens"],
            fresh_input_tokens=cache["fresh_input_tokens"],
        )

    def metrics(self) -> dict:
        with self._metrics_lock:
            return dict(self._metrics)

    def complete(self, prompt: str) -> str:
        client = self._get_client()
        if not client:
            return ""
        return self._call(client, [{"role": "user", "content": prompt}])

    def complete_messages(self, messages: list) -> str:
        """用完整消息列表调用 LLM（支持 prompt caching 前缀共享）。"""
        client = self._get_client()
        if not client:
            return ""
        return self._call(client, messages)
