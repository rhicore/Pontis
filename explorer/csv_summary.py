"""Agent CSV Summary — summarize CSV files and column profile nodes.

独立执行:
    python -m explorer.csv_summary ./my_data --file data/table.csv
"""
from __future__ import annotations

import argparse
import logging

from extractor.modules import csv_column_stats
from storage.workspace import Workspace
from tool.utils.workspace_access import OpenFileSource, resolve_file_sources

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是为指定 CSV/TSV 文件和它的列节点写中文 brief/detail。

## 目标文件

- 文件路径：`__CSV_PATH__`
- 是否强制重写已有高质量摘要：`__FORCE__`

## 工作目标

1. 给 CSV/TSV 文件本身写 summary：
   - brief：最多 50 字，概括这个表格文件是什么数据。
   - detail：说明行列规模、主要列、可能的主键/时间/类别/数值字段、数据粒度、明显缺失或质量线索。

2. 给每个列节点写 summary：
   - brief：最多 50 字，概括该列含义。
   - detail：说明列名、推断类型、唯一值规模、缺失率、样本/top-k、数值范围或文本长度等线索。

## 必须优先使用列 profile

不要直接读取完整 CSV。大文件可能有几百 MB。

先执行：

```text
meta({"ref": "__CSV_PATH__", "property": ["row_count", "column_count", "delimiter", "line_count", "brief", "detail"]})
meta({"ref": "__CSV_PATH__", "neighbor_label": "col"})
find({"ref": "__CSV_PATH__/*:col", "limit": 500})
```

然后对每个列节点执行：

```text
meta({
  "ref": "<col ref>",
  "property": [
    "source_column", "ordinal", "col_type",
    "cardinality", "null_count", "null_percentage",
    "sample", "topk",
    "min_value", "max_value", "mean_value",
    "min_length", "max_length", "avg_length",
    "brief", "detail"
  ]
})
```

列节点必须使用带 CSV 文件路径的 ref，例如 `__CSV_PATH__/column_name`，不要使用裸列名、`column_name:col` 或 `column_name:col:TYPE`。

## 原文读取限制

- 允许用 `read({"ref": "__CSV_PATH__", "start_line": 1, "end_line": 20})` 查看 header 和少量样例行。
- 允许用 `grep` 查特定列名或少量关键词。
- 禁止要求读取全文件；不要用 `read` 分页扫完整 CSV。

## 写入规则

只能用 `update_meta` 写 `brief/detail`。

写文件 summary：

```text
update_meta({"ref": "__CSV_PATH__", "fields": {"brief": "...", "detail": "..."}})
```

写列 summary：

```text
update_meta({"ref": "<col ref>", "fields": {"brief": "...", "detail": "..."}})
```

如果 `force=false` 且已有高质量 brief/detail，可以保留；否则更新。
工具参数必须是合法 JSON。`brief/detail` 文本里不要裸写英文双引号 `"`；字段值示例优先用中文引号、单引号或反引号，避免 `update_meta` 参数解析失败。

## summary 质量要求

- 用中文。
- 基于列名、profile、少量样例行总结，不要虚构业务含义。
- 字段名清楚时可以解释；字段名模糊时明确“不确定”。
- 不要把 approximate cardinality/top-k 当成精确统计；可写“约”“高基数”“低基数”。
- 文件 detail 要列出关键字段组，例如标识字段、时间字段、类别字段、数值指标字段。
- 列 detail 必须包含该列的核心数据线索：类型、缺失、唯一值规模、典型值或范围。

## 子智能体策略

如果列很多，可以启动子智能体分批阅读列 profile。

子智能体只负责返回建议的 brief/detail JSON；最终 `update_meta` 由你统一执行。

## 完成条件

- CSV/TSV 文件本身已有 brief/detail。
- 该文件下每个 `col` 节点都有 brief/detail；低信息列也要写简洁 summary。
- 没有全量读取大 CSV。
- 完成后直接停止，不要输出额外解释。
"""


def _load_csv_sources(workspace: Workspace, file: str | None = None) -> list[OpenFileSource]:
    if file:
        sources = []
        sources.extend(resolve_file_sources(workspace, file, labels=("csv",), allow_directory=False))
        sources.extend(resolve_file_sources(workspace, file, labels=("tsv",), allow_directory=False))
        return _dedupe_sources(sources)

    sources = []
    sources.extend(resolve_file_sources(workspace, ".", labels=("csv",), allow_directory=True))
    sources.extend(resolve_file_sources(workspace, ".", labels=("tsv",), allow_directory=True))
    return _dedupe_sources(sources)


def _dedupe_sources(sources: list[OpenFileSource]) -> list[OpenFileSource]:
    seen = set()
    out = []
    for source in sorted(sources, key=lambda item: item.path):
        if source.path in seen:
            continue
        seen.add(source.path)
        out.append(source)
    return out


def _run_agent_for_csv(workspace: Workspace, source: OpenFileSource, *, force: bool) -> None:
    from agent.config import create_agent
    from explorer.utils.agent_spec import explorer_writer_spec

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "grep", "read", "meta", "agent",
            "update_meta",
        ],
        effort="high",
        max_rounds=120,
    )
    agent = create_agent(workspace.project_path, spec)

    prompt = (
        PROMPT
        .replace("__CSV_PATH__", source.path)
        .replace("__FORCE__", str(force).lower())
    )
    agent.chat(prompt)


def generate(
    workspace: Workspace,
    file: str | None = None,
    *,
    force: bool = False,
    profile: bool = True,
) -> None:
    """启动 writer agent，为 CSV/TSV 文件和列节点写 summary。"""
    from agent.utils import load_agent_config

    if profile:
        csv_column_stats.generate(workspace, file=file)

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping CSV summary")
        return

    logger.info("=== Agent CSV Summary ===")
    sources = _load_csv_sources(workspace, file=file)
    if not sources:
        logger.info("No CSV/TSV sources found")
        return

    for source in sources:
        logger.info("CSV summary agent processing: %s", source.path)
        _run_agent_for_csv(workspace, source, force=force)

    logger.info("=== Agent CSV Summary done: %s files ===", len(sources))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ask an agent to summarize CSV/TSV files and columns.")
    parser.add_argument("target", help="Project/source directory")
    parser.add_argument("--file", help="Project-relative CSV/TSV file path")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-profile", action="store_true", help="Do not run csv_column_stats before the agent")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    workspace = Workspace(project_path=os.path.abspath(args.target))
    generate(workspace, file=args.file, force=args.force, profile=not args.no_profile)


if __name__ == "__main__":
    _main()
