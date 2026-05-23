"""Pontis 共享 LLM 基础设施

提供：YAML 配置加载、LLM 客户端。
"""
import os
import threading
import yaml
import logging
from collections import Counter

from utils.token_metrics import add_usage

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
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                timeout=120,
                **kwargs,
            )
            self._record_usage(response)
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""

    def _record_usage(self, response) -> None:
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
        with self._metrics_lock:
            self._metrics["preprocess_llm_calls"] += 1
            self._metrics["preprocess_llm_input_tokens"] += input_tokens
            self._metrics["preprocess_llm_output_tokens"] += output_tokens
            self._metrics["preprocess_llm_total_tokens"] += total_tokens
        add_usage(
            "preprocess_llm",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
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
