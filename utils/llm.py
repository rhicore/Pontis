"""Pontis 共享 LLM 基础设施

提供：YAML 配置加载、LLM 客户端、DeepSeek 思考模式参数构建。
"""
import os
import yaml
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def apply_yaml(cfg: dict, path: str, mapping: dict):
    """将 YAML 文件值应用到 config dict。

    Args:
        cfg: 配置字典（原地修改）
        path: YAML 文件路径
        mapping: {yaml_key: cfg_key} 映射
    """
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        data = yaml.safe_load(f) or {}
    for yaml_key, cfg_key in mapping.items():
        if yaml_key in data:
            cfg[cfg_key] = data[yaml_key]


def build_thinking_kwargs(thinking: bool, thinking_effort: str, **extra) -> dict:
    """构建 LLM 调用参数，处理 DeepSeek 思考模式。

    Returns:
        kwargs dict（不含 model/messages/tools）
    """
    kwargs = {"max_tokens": extra.pop("max_tokens", 500), **extra}
    if thinking:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = thinking_effort
    else:
        kwargs["temperature"] = extra.pop("temperature", 0.3)
    return kwargs


class LLMClient:
    """Simple LLM client wrapper."""

    def __init__(self, api_key: str, provider: str, model: str,
                 thinking: bool = True, thinking_effort: str = "high"):
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.thinking = thinking
        self.thinking_effort = thinking_effort
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.provider,
                )
            except ImportError:
                logger.error("OpenAI package not installed")
        return self._client

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        client = self._get_client()
        if not client:
            return ""
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                **build_thinking_kwargs(
                    self.thinking, self.thinking_effort,
                    max_tokens=max_tokens,
                ),
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""

    def complete_messages(self, messages: list, max_tokens: int = 500) -> str:
        """用完整消息列表调用 LLM（支持 prompt caching 前缀共享）。"""
        client = self._get_client()
        if not client:
            return ""
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                **build_thinking_kwargs(
                    self.thinking, self.thinking_effort,
                    max_tokens=max_tokens,
                ),
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""
