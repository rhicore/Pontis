"""Shared embedding client and configuration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import threading
from typing import Iterable

from utils.llm import apply_yaml

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    enabled: bool
    provider: str
    model: str
    api_key: str
    dimensions: int
    batch_size: int = 64

    def get_client(self) -> "EmbeddingClient | None":
        if not self.enabled or not self.api_key:
            return None
        return EmbeddingClient(
            api_key=self.api_key,
            provider=self.provider,
            model=self.model,
            dimensions=self.dimensions,
        )


class EmbeddingClient:
    """OpenAI-compatible embeddings client."""

    def __init__(self, api_key: str, provider: str, model: str, dimensions: int = 0):
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.dimensions = dimensions
        self._local = threading.local()

    def _get_client(self):
        client = getattr(self._local, "client", None)
        if client is not None:
            return client
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("OpenAI package not installed; semantic embedding disabled")
            return None
        kwargs = {"api_key": self.api_key}
        if self.provider:
            kwargs["base_url"] = self.provider
        client = OpenAI(timeout=60.0, max_retries=2, **kwargs)
        self._local.client = client
        return client

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        clean = [str(text or "").strip() for text in texts]
        if not clean:
            return []
        client = self._get_client()
        if not client:
            return []
        kwargs = {"model": self.model, "input": clean}
        if self.dimensions:
            kwargs["dimensions"] = self.dimensions
        response = client.embeddings.create(**kwargs)
        return [list(item.embedding) for item in response.data]

    def embed_one(self, text: str) -> list[float]:
        vectors = self.embed([text])
        return vectors[0] if vectors else []


def load_embedding_config(path: str | None = None) -> EmbeddingConfig:
    import global_config as defaults

    cfg = {
        "provider": defaults.EMBEDDING_PROVIDER,
        "model": defaults.EMBEDDING_MODEL,
        "api_key": defaults.EMBEDDING_API_KEY,
        "dimensions": defaults.EMBEDDING_DIMENSIONS,
        "batch_size": defaults.EMBEDDING_BATCH_SIZE,
    }

    mapping = {
        "embedding_provider": "provider",
        "embedding_model": "model",
        "embedding_api_key": "api_key",
        "embedding_dimensions": "dimensions",
        "embedding_batch_size": "batch_size",
    }
    apply_yaml(cfg, os.path.expanduser("~/.pontis/config.yml"), mapping)
    if path:
        project_dir = os.path.dirname(path) if os.path.isfile(path) else path
        for filename in ("pontis.yml", "pontis.yaml"):
            apply_yaml(cfg, os.path.join(project_dir, filename), mapping)

    provider = os.environ.get("PONTIS_EMBEDDING_PROVIDER", cfg.get("provider") or "")
    if not provider and os.environ.get("OPENAI_BASE_URL"):
        provider = os.environ["OPENAI_BASE_URL"]
    api_key = os.environ.get("PONTIS_EMBEDDING_API_KEY", cfg.get("api_key") or "")
    if not api_key:
        api_key = os.environ.get("DASHSCOPE_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

    return EmbeddingConfig(
        enabled=bool(api_key),
        provider=provider,
        model=os.environ.get("PONTIS_EMBEDDING_MODEL", cfg.get("model") or "text-embedding-v4"),
        api_key=api_key,
        dimensions=int(os.environ.get("PONTIS_EMBEDDING_DIMENSIONS", cfg.get("dimensions") or 1024)),
        batch_size=int(os.environ.get("PONTIS_EMBEDDING_BATCH_SIZE", cfg.get("batch_size") or 10)),
    )
