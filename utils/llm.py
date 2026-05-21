"""Pontis 共享 LLM 基础设施

提供：YAML 配置加载、LLM 客户端。
"""
import os
import threading
import yaml
import logging

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
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""

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
