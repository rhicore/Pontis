"""Tool use configuration"""
from dataclasses import dataclass


@dataclass
class ToolUseConfig:
    """Pontis tool use configuration"""

    # Directory settings (must match extractor)
    pontis_dir_name: str = ".pontis"
    meta_filename: str = "_meta.yml"
