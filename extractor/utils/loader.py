"""Utilities for Pontis extractor.

Contains: Config and common utilities. LLM client moved to utils/llm.py.
"""
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from utils.llm import LLMClient, apply_yaml
from utils.embedding import EmbeddingConfig


@dataclass
class Config:
    """Pontis extractor configuration"""
    pontis_dir_name: str = ".pontis"
    meta_filename: str = "_meta.yml"
    sample_size: int = 5
    top_k: int = 5
    llm_enabled: bool = False
    llm_provider: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_api_key: Optional[str] = None
    llm_thinking: bool = True
    llm_thinking_effort: str = "high"
    brief_max_words: int = 20
    table_brief_max_words: int = 15
    log_level: str = "INFO"
    embedding_provider: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: Optional[str] = None
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 64
    overlap_value_overlap_enabled: bool = True
    overlap_name_overlap_enabled: bool = True
    overlap_same_schema_only: bool = False
    overlap_skip_same_table_group: bool = True
    overlap_same_table_overlap_enabled: bool = True
    overlap_same_table_group_representative_only: bool = True
    overlap_domain_filter_enabled: bool = True
    overlap_shape_filter_enabled: bool = False
    overlap_key_like_only: bool = False
    overlap_require_name_token_overlap: bool = False
    overlap_name_token_overlap_first: bool = False
    overlap_require_repeated_key_name: bool = False
    overlap_top_k_per_column: int = 0
    overlap_generic_token_top_k: int = 5
    overlap_max_value_candidate_pairs: int = 5000
    overlap_value_match_method: str = "sql"
    overlap_minhash_num_perm: int = 128
    overlap_minhash_min_matching_hashes: int = 1
    overlap_minhash_jaccard_threshold: float = 0.0
    overlap_minhash_max_sql_verify_pairs: int = 5000
    overlap_snowflake_minhash_column_batch_size: int = 128
    overlap_snowflake_minhash_value_partitions: int = 1
    overlap_snowflake_minhash_max_warehouse_running: int = 0
    overlap_snowflake_minhash_warehouse_poll_seconds: int = 30
    overlap_lazo_containment_threshold: float = 0.01
    overlap_lazo_confidence: float = 0.99
    overlap_sample_bloom_sample_size: int = 2048
    overlap_sample_bloom_false_positive_rate: float = 0.0001
    overlap_sample_bloom_initial_capacity: int = 8192
    overlap_sample_bloom_growth_factor: int = 4
    overlap_sample_bloom_min_hits: int = 1
    overlap_sample_bloom_sample_rows: int = 0
    overlap_sample_bloom_max_domain_members: int = 0
    overlap_adaptive_sample_initial_size: int = 256
    overlap_adaptive_sample_size: int = 1024
    overlap_adaptive_sample_max_size: int = 4096
    overlap_adaptive_sample_min_overlap: float = 0.01
    overlap_adaptive_sample_confidence: float = 0.99
    preprocess_token_metrics: Counter = field(default_factory=Counter)

    def get_llm(self) -> Optional[LLMClient]:
        """从此配置创建 LLM 客户端。"""
        if not self.llm_enabled or not self.llm_api_key:
            return None
        return LLMClient(
            api_key=self.llm_api_key,
            provider=self.llm_provider,
            model=self.llm_model,
            thinking=self.llm_thinking,
            thinking_effort=self.llm_thinking_effort,
        )

    def get_embedding_config(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            enabled=bool(self.embedding_api_key),
            provider=self.embedding_provider,
            model=self.embedding_model,
            api_key=self.embedding_api_key or "",
            dimensions=self.embedding_dimensions,
            batch_size=self.embedding_batch_size,
        )

    def add_preprocess_token_metrics(self, metrics: dict | None) -> None:
        if not metrics:
            return
        for key, value in metrics.items():
            self.preprocess_token_metrics[key] += int(value or 0)

    def get_preprocess_token_metrics(self) -> dict:
        metrics = dict(self.preprocess_token_metrics)
        llm_total = int(metrics.get("preprocess_llm_total_tokens", 0) or 0)
        embedding_total = int(metrics.get("preprocess_embedding_total_tokens", 0) or 0)
        metrics["preprocess_total_tokens"] = llm_total + embedding_total
        return metrics


def load_config(path: Optional[str] = None) -> Config:
    """从默认值 + YAML + 环境变量加载 extractor 配置。"""
    from config import global_config as _defaults

    cfg = {
        "provider": _defaults.EXTRACTOR_PROVIDER,
        "model": _defaults.EXTRACTOR_MODEL,
        "api_key": _defaults.EXTRACTOR_API_KEY,
        "temperature": _defaults.EXTRACTOR_TEMPERATURE,
        "thinking": _defaults.EXTRACTOR_THINKING,
        "thinking_effort": _defaults.EXTRACTOR_THINKING_EFFORT,
        "pontis_dir_name": _defaults.PONTIS_DIR_NAME,
        "meta_filename": _defaults.META_FILENAME,
        "sample_size": _defaults.SAMPLE_SIZE,
        "top_k": _defaults.TOP_K,
        "log_level": _defaults.LOG_LEVEL,
        "embedding_provider": _defaults.EMBEDDING_PROVIDER,
        "embedding_model": _defaults.EMBEDDING_MODEL,
        "embedding_api_key": _defaults.EMBEDDING_API_KEY,
        "embedding_dimensions": _defaults.EMBEDDING_DIMENSIONS,
        "embedding_batch_size": _defaults.EMBEDDING_BATCH_SIZE,
        "overlap_value_overlap_enabled": True,
        "overlap_name_overlap_enabled": True,
        "overlap_same_schema_only": False,
        "overlap_skip_same_table_group": True,
        "overlap_same_table_overlap_enabled": True,
        "overlap_same_table_group_representative_only": True,
        "overlap_domain_filter_enabled": True,
        "overlap_shape_filter_enabled": False,
        "overlap_key_like_only": False,
        "overlap_require_name_token_overlap": False,
        "overlap_name_token_overlap_first": False,
        "overlap_require_repeated_key_name": False,
        "overlap_top_k_per_column": 0,
        "overlap_generic_token_top_k": 5,
        "overlap_max_value_candidate_pairs": 5000,
        "overlap_value_match_method": "sql",
        "overlap_minhash_num_perm": 128,
        "overlap_minhash_min_matching_hashes": 1,
        "overlap_minhash_jaccard_threshold": 0.0,
        "overlap_minhash_max_sql_verify_pairs": 5000,
        "overlap_snowflake_minhash_column_batch_size": 128,
        "overlap_snowflake_minhash_value_partitions": 1,
        "overlap_snowflake_minhash_max_warehouse_running": 0,
        "overlap_snowflake_minhash_warehouse_poll_seconds": 30,
        "overlap_lazo_containment_threshold": 0.01,
        "overlap_lazo_confidence": 0.99,
        "overlap_sample_bloom_sample_size": 2048,
        "overlap_sample_bloom_false_positive_rate": 0.0001,
        "overlap_sample_bloom_initial_capacity": 8192,
        "overlap_sample_bloom_growth_factor": 4,
        "overlap_sample_bloom_min_hits": 1,
        "overlap_sample_bloom_sample_rows": 0,
        "overlap_sample_bloom_max_domain_members": 0,
        "overlap_adaptive_sample_initial_size": 256,
        "overlap_adaptive_sample_size": 1024,
        "overlap_adaptive_sample_max_size": 4096,
        "overlap_adaptive_sample_min_overlap": 0.01,
        "overlap_adaptive_sample_confidence": 0.99,
    }

    EXTRACTOR_YAML_MAPPING = {
        "extractor_provider": "provider",
        "extractor_model": "model",
        "extractor_api_key": "api_key",
        "extractor_temperature": "temperature",
        "pontis_dir_name": "pontis_dir_name",
        "meta_filename": "meta_filename",
        "sample_size": "sample_size",
        "top_k": "top_k",
        "log_level": "log_level",
        "embedding_provider": "embedding_provider",
        "embedding_model": "embedding_model",
        "embedding_api_key": "embedding_api_key",
        "embedding_dimensions": "embedding_dimensions",
        "embedding_batch_size": "embedding_batch_size",
        "overlap_value_overlap_enabled": "overlap_value_overlap_enabled",
        "overlap_name_overlap_enabled": "overlap_name_overlap_enabled",
        "overlap_same_schema_only": "overlap_same_schema_only",
        "overlap_skip_same_table_group": "overlap_skip_same_table_group",
        "overlap_same_table_overlap_enabled": "overlap_same_table_overlap_enabled",
        "overlap_same_table_group_representative_only": "overlap_same_table_group_representative_only",
        "overlap_domain_filter_enabled": "overlap_domain_filter_enabled",
        # Old project configs migrate to the wider domain filter.
        "overlap_type_filter_enabled": "overlap_domain_filter_enabled",
        "overlap_shape_filter_enabled": "overlap_shape_filter_enabled",
        "overlap_key_like_only": "overlap_key_like_only",
        "overlap_require_name_token_overlap": "overlap_require_name_token_overlap",
        "overlap_name_token_overlap_first": "overlap_name_token_overlap_first",
        "overlap_require_repeated_key_name": "overlap_require_repeated_key_name",
        "overlap_top_k_per_column": "overlap_top_k_per_column",
        "overlap_generic_token_top_k": "overlap_generic_token_top_k",
        "overlap_max_value_candidate_pairs": "overlap_max_value_candidate_pairs",
        "overlap_value_match_method": "overlap_value_match_method",
        "overlap_minhash_num_perm": "overlap_minhash_num_perm",
        "overlap_minhash_min_matching_hashes": "overlap_minhash_min_matching_hashes",
        "overlap_minhash_jaccard_threshold": "overlap_minhash_jaccard_threshold",
        "overlap_minhash_max_sql_verify_pairs": "overlap_minhash_max_sql_verify_pairs",
        "overlap_snowflake_minhash_column_batch_size": "overlap_snowflake_minhash_column_batch_size",
        "overlap_snowflake_minhash_value_partitions": "overlap_snowflake_minhash_value_partitions",
        "overlap_snowflake_minhash_max_warehouse_running": "overlap_snowflake_minhash_max_warehouse_running",
        "overlap_snowflake_minhash_warehouse_poll_seconds": "overlap_snowflake_minhash_warehouse_poll_seconds",
        "overlap_lazo_containment_threshold": "overlap_lazo_containment_threshold",
        "overlap_lazo_confidence": "overlap_lazo_confidence",
        "overlap_sample_bloom_sample_size": "overlap_sample_bloom_sample_size",
        "overlap_sample_bloom_false_positive_rate": "overlap_sample_bloom_false_positive_rate",
        "overlap_sample_bloom_initial_capacity": "overlap_sample_bloom_initial_capacity",
        "overlap_sample_bloom_growth_factor": "overlap_sample_bloom_growth_factor",
        "overlap_sample_bloom_min_hits": "overlap_sample_bloom_min_hits",
        "overlap_sample_bloom_sample_rows": "overlap_sample_bloom_sample_rows",
        "overlap_sample_bloom_max_domain_members": "overlap_sample_bloom_max_domain_members",
        "overlap_adaptive_sample_initial_size": "overlap_adaptive_sample_initial_size",
        "overlap_adaptive_sample_size": "overlap_adaptive_sample_size",
        "overlap_adaptive_sample_max_size": "overlap_adaptive_sample_max_size",
        "overlap_adaptive_sample_min_overlap": "overlap_adaptive_sample_min_overlap",
        "overlap_adaptive_sample_confidence": "overlap_adaptive_sample_confidence",
    }

    # 1. ~/.pontis/config.yml
    apply_yaml(cfg, os.path.expanduser("~/.pontis/config.yml"), EXTRACTOR_YAML_MAPPING)

    # 2. 项目级 pontis.yml
    if path:
        project_dir = os.path.dirname(path) if os.path.isfile(path) else path
        for filename in ["pontis.yml", "pontis.yaml"]:
            apply_yaml(cfg, os.path.join(project_dir, filename), EXTRACTOR_YAML_MAPPING)

    # 3. 环境变量覆盖
    model_url = os.environ.get("MODEL_API_URL", "")
    model_key = os.environ.get("MODEL_API_KEY", "")
    model_name = os.environ.get("MODEL_NAME", "")
    env_key = os.environ.get("OPENAI_API_KEY", "")
    env_url = os.environ.get("OPENAI_BASE_URL", "")
    env_model = os.environ.get("PONTIS_EXTRACTOR_MODEL", "")

    if model_url:
        cfg["provider"] = model_url
        cfg["thinking"] = False
    elif env_url and cfg["provider"] == "https://api.deepseek.com":
        cfg["provider"] = env_url

    if model_key:
        cfg["api_key"] = model_key
    elif not cfg["api_key"]:
        cfg["api_key"] = env_key

    if model_name:
        cfg["model"] = model_name
    elif env_model:
        cfg["model"] = env_model
    if not cfg["embedding_api_key"]:
        cfg["embedding_api_key"] = (
            os.environ.get("PONTIS_EMBEDDING_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
    if not cfg["embedding_provider"]:
        cfg["embedding_provider"] = (
            os.environ.get("PONTIS_EMBEDDING_PROVIDER")
            or os.environ.get("OPENAI_BASE_URL", "")
        )

    return Config(
        pontis_dir_name=cfg["pontis_dir_name"],
        meta_filename=cfg["meta_filename"],
        sample_size=cfg["sample_size"],
        top_k=cfg["top_k"],
        log_level=cfg["log_level"],
        llm_provider=cfg["provider"],
        llm_model=cfg["model"],
        llm_api_key=cfg["api_key"] or None,
        llm_enabled=bool(cfg["api_key"]),
        llm_thinking=cfg.get("thinking", True),
        llm_thinking_effort=cfg.get("thinking_effort", "high"),
        embedding_provider=cfg.get("embedding_provider", ""),
        embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
        embedding_api_key=cfg.get("embedding_api_key") or None,
        embedding_dimensions=int(cfg.get("embedding_dimensions") or 1536),
        embedding_batch_size=int(cfg.get("embedding_batch_size") or 64),
        overlap_value_overlap_enabled=_bool_cfg(cfg.get("overlap_value_overlap_enabled", True)),
        overlap_name_overlap_enabled=_bool_cfg(cfg.get("overlap_name_overlap_enabled", True)),
        overlap_same_schema_only=_bool_cfg(cfg.get("overlap_same_schema_only", False)),
        overlap_skip_same_table_group=_bool_cfg(cfg.get("overlap_skip_same_table_group", True)),
        overlap_same_table_overlap_enabled=_bool_cfg(cfg.get("overlap_same_table_overlap_enabled", True)),
        overlap_same_table_group_representative_only=_bool_cfg(cfg.get("overlap_same_table_group_representative_only", True)),
        overlap_domain_filter_enabled=_bool_cfg(cfg.get("overlap_domain_filter_enabled", True)),
        overlap_shape_filter_enabled=_bool_cfg(cfg.get("overlap_shape_filter_enabled", False)),
        overlap_key_like_only=_bool_cfg(cfg.get("overlap_key_like_only", False)),
        overlap_require_name_token_overlap=_bool_cfg(cfg.get("overlap_require_name_token_overlap", False)),
        overlap_name_token_overlap_first=_bool_cfg(cfg.get("overlap_name_token_overlap_first", False)),
        overlap_require_repeated_key_name=_bool_cfg(cfg.get("overlap_require_repeated_key_name", False)),
        overlap_top_k_per_column=int(cfg.get("overlap_top_k_per_column") or 0),
        overlap_generic_token_top_k=int(cfg.get("overlap_generic_token_top_k") or 5),
        overlap_max_value_candidate_pairs=int(cfg.get("overlap_max_value_candidate_pairs") or 5000),
        overlap_value_match_method=str(cfg.get("overlap_value_match_method") or "sql"),
        overlap_minhash_num_perm=int(cfg.get("overlap_minhash_num_perm") or 128),
        overlap_minhash_min_matching_hashes=int(cfg.get("overlap_minhash_min_matching_hashes") or 1),
        overlap_minhash_jaccard_threshold=float(cfg.get("overlap_minhash_jaccard_threshold") or 0.0),
        overlap_minhash_max_sql_verify_pairs=int(cfg.get("overlap_minhash_max_sql_verify_pairs") or 5000),
        overlap_snowflake_minhash_column_batch_size=int(
            cfg.get("overlap_snowflake_minhash_column_batch_size") or 128
        ),
        overlap_snowflake_minhash_value_partitions=int(
            cfg.get("overlap_snowflake_minhash_value_partitions") or 1
        ),
        overlap_snowflake_minhash_max_warehouse_running=int(
            cfg.get("overlap_snowflake_minhash_max_warehouse_running") or 0
        ),
        overlap_snowflake_minhash_warehouse_poll_seconds=int(
            cfg.get("overlap_snowflake_minhash_warehouse_poll_seconds") or 30
        ),
        overlap_lazo_containment_threshold=float(cfg.get("overlap_lazo_containment_threshold") or 0.01),
        overlap_lazo_confidence=float(cfg.get("overlap_lazo_confidence") or 0.99),
        overlap_sample_bloom_sample_size=int(cfg.get("overlap_sample_bloom_sample_size") or 2048),
        overlap_sample_bloom_false_positive_rate=float(
            cfg.get("overlap_sample_bloom_false_positive_rate") or 0.0001
        ),
        overlap_sample_bloom_initial_capacity=int(cfg.get("overlap_sample_bloom_initial_capacity") or 8192),
        overlap_sample_bloom_growth_factor=int(cfg.get("overlap_sample_bloom_growth_factor") or 4),
        overlap_sample_bloom_min_hits=int(cfg.get("overlap_sample_bloom_min_hits") or 1),
        overlap_sample_bloom_sample_rows=int(cfg.get("overlap_sample_bloom_sample_rows") or 0),
        overlap_sample_bloom_max_domain_members=int(cfg.get("overlap_sample_bloom_max_domain_members") or 0),
        overlap_adaptive_sample_initial_size=int(cfg.get("overlap_adaptive_sample_initial_size") or 256),
        overlap_adaptive_sample_size=int(cfg.get("overlap_adaptive_sample_size") or 1024),
        overlap_adaptive_sample_max_size=int(cfg.get("overlap_adaptive_sample_max_size") or 4096),
        overlap_adaptive_sample_min_overlap=float(cfg.get("overlap_adaptive_sample_min_overlap") or 0.01),
        overlap_adaptive_sample_confidence=float(cfg.get("overlap_adaptive_sample_confidence") or 0.99),
    )


def _bool_cfg(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value)
