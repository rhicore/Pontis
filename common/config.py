"""Configuration management"""
import os
from dataclasses import dataclass, field
from typing import Optional, List
import yaml


@dataclass
class Config:
    """Pontis configuration"""

    # Directory settings
    pontis_dir_name: str = ".pontis"
    meta_filename: str = "_meta.yml"

    # Supported file extensions
    db_extensions: List[str] = field(default_factory=lambda: [".db", ".sqlite", ".sqlite3", ".duckdb"])
    csv_extensions: List[str] = field(default_factory=lambda: [".csv"])
    json_extensions: List[str] = field(default_factory=lambda: [".json", ".jsonl"])
    md_extensions: List[str] = field(default_factory=lambda: [".md", ".markdown"])

    # Extraction settings
    sample_size: int = 100  # Number of rows to sample for JSON analysis
    top_k: int = 5  # Number of top values to collect
    max_json_depth: int = 10  # Maximum depth for JSON flattening

    # LLM settings
    llm_provider: str = "https://api.deepseek.com"  # or "openai"
    llm_model: str = "deepseek-chat"
    llm_api_key: Optional[str] = 'sk-3c10dd45fab045228ca025f88eeb85bb'
    llm_enabled: bool = True

    # Semantic enrichment settings
    brief_max_words: int = 20  # Max words for column brief (for ls display)

    # Caching
    cache_enabled: bool = True
    skip_unchanged_files: bool = True

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = None

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load config from YAML file"""
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def to_file(self, path: str):
        """Save config to YAML file"""
        with open(path, 'w') as f:
            yaml.dump(self.__dict__, f, default_flow_style=False)


def load_config(path: Optional[str] = None) -> Config:
    """Load configuration from file or return defaults"""
    if path and os.path.exists(path):
        return Config.from_file(path)

    # Check for config in common locations
    config_paths = [
        "pontis.yml",
        "pontis.yaml",
        os.path.expanduser("~/.pontis/config.yml"),
    ]

    for p in config_paths:
        if os.path.exists(p):
            return Config.from_file(p)

    return Config()
