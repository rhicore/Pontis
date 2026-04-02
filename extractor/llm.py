"""LLM interface for semantic enrichment"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

from common.config import Config

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    """Base class for LLM clients"""

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        """Send prompt to LLM and return completion"""
        pass


class PlaceholderLLMClient(BaseLLMClient):
    """Placeholder LLM client - returns fixed responses"""

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        logger.warning("LLM not configured - returning placeholder")
        return "[AI-generated content placeholder]"


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude client"""

    def __init__(self, config: Config):
        super().__init__(config)
        self.api_key = config.llm_api_key
        self.model = config.llm_model

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ""


class OpenAIClient(BaseLLMClient):
    """OpenAI GPT client"""

    def __init__(self, config: Config):
        super().__init__(config)
        self.api_key = config.llm_api_key
        self.model = config.llm_model

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return ""


def get_llm_client(config: Config) -> BaseLLMClient:
    """Factory function to get appropriate LLM client"""
    if not config.llm_enabled:
        return PlaceholderLLMClient(config)

    if config.llm_provider == "anthropic":
        return AnthropicClient(config)
    elif config.llm_provider == "openai":
        return OpenAIClient(config)
    else:
        logger.warning(f"Unknown LLM provider: {config.llm_provider}")
        return PlaceholderLLMClient(config)
