"""BIRD official-description handling shared by explorer modules."""

from pathlib import Path


def has_bird_official_descriptions(project_path: str) -> bool:
    """Return whether this project contains BIRD's description CSV directory."""
    root = Path(project_path)
    if root.is_file():
        root = root.parent
    description_dir = root / "database_description"
    return description_dir.is_dir() and any(description_dir.glob("*.csv"))


def explorer_tools(project_path: str, tools: list[str]) -> list[str]:
    """Hide raw text readers after BIRD CSVs have been imported to columns."""
    if not has_bird_official_descriptions(project_path):
        return tools
    return [name for name in tools if name not in {"grep", "read"}]


def official_metadata_note(project_path: str) -> str:
    if not has_bird_official_descriptions(project_path):
        return ""
    return (
        "\n\n## BIRD 官方说明约束\n\n"
        "`database_description/*.csv` 已由专用 extractor 导入到物理数据库列的 "
        "`official_column_description` 和 `official_value_description`。"
        "不要再次读取或查询这些 CSV；直接使用列实体上的 official 字段。"
    )
