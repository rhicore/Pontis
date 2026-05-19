"""Agent Text Chunk — 让 writer agent 为长文本创建 chunk 实体。

独立执行:
    python -m explorer.text_chunk ./my_data --file context/doc/report.md
"""
import argparse
import logging
import os

from storage.workspace import Workspace
from tool.utils.workspace_access import OpenFileSource, resolve_file_sources

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHARS = 20_000

PROMPT = """\
你的任务是阅读指定文本文件，完成两件事：

1. 先把文件中的关键语义段落创建为若干 `chunk` 实体，作为可检索索引写入当前项目图谱。
2. 再基于整篇文件理解，为源文本文件写一份面向后续数据分析的中文 brief/detail。

## 目标文件

- 文件路径：`__FILE_PATH__`
- 是否强制重建：`__FORCE__`

## 你要自己完成的事

你现在是 coordinator。

1. 用 `meta` 查看目标文件已有信息，确认它是文本文件，并查看是否已有 chunk。
2. 如果已有 chunk：
   - `force=false` 时不要重复创建，确认已有 chunk 可读后停止。
   - `force=true` 时先删除旧的 chunk，再重建。
3. 用 `read` 读取目标文件，先理解整篇文档的主题、章节、字段、规则、歧义说明和分析用途。
4. 对长文件必须使用 `agent` 子智能体：
   - 你负责分配行号范围、汇总结果、创建 chunk 实体和连边，并在最后形成整篇文档 summary。
   - 子智能体负责读取自己负责的行号范围，并返回该范围内的内容摘要和建议 chunk 列表。
   - 子智能体不要直接写图，避免并发写入冲突。
5. 用 `create_entity` 创建每个 chunk，并用 `add_edge` 或 `create_entity(edges=...)` 把源文件连接到 chunk。
6. chunk 创建完成后，用 `update_meta` 给源文件写文档内容 summary。

## chunk 设计原则

- chunk 是“语义聚拢的文本片段实体”，不是必须覆盖全文的机械分片。
- chunk 边界优先按语义划分：标题、章节、段落、列表、表格说明、连续主题。
- 如果某些段落只是噪声、重复模板、页眉页脚、无信息目录，可以不创建 chunk。
- 如果一个重要主题跨越不连续位置，可以优先创建多个相邻或相关主题 chunk，不要强行把中间无关内容塞进去。
- 如果文本结构不清晰，再按稳定行号窗口切分，但仍然只保留有检索价值的片段。
- 单个 chunk 不要太大；优先控制在一个子智能体可以读完和总结的范围内。
- 每个 chunk 必须能通过 `read(ref, start_line, end_line)` 回查原文。
- chunk 集合应覆盖目标文件的关键内容，但不要求覆盖每一行。
- 空白、目录、页眉页脚、格式噪声可以合并到附近内容，或生成很短的格式性 chunk。

## 实体规范

每个 chunk 创建为：

```text
create_entity({
  "ref": "0001:chunk",
  "meta": {
    "chunk_index": 1,
    "start_line": 1,
    "end_line": 120,
    "brief": "...",
    "detail": "..."
  },
  "edges": [
    {"a": "__FILE_PATH__", "b": "0001:chunk"}
  ]
})
```

注意：chunk 名只使用 `0001`、`0002` 这种四位局部编号，不要带 `chunk-` 前缀，也不要把文件名、目录名、hash 或完整路径拼进 chunk 名。
注意：chunk 之间不要互相连接，只需要源文件连接到每个 chunk。
注意：最终回复里引用 chunk 时，也使用 `0001`、`0002`，不要写 `chunk1`、`chunk-0001`。

## meta 字段要求

每个 chunk 的 meta 必须包含：

- `chunk_index`: 从 1 开始
- `start_line`: chunk 起始行号
- `end_line`: chunk 结束行号
- `brief`: 中文，最多 50 字，概括该 chunk 主题
- `detail`: 中文，说明主题、关键对象、关键指标/时间/名称、重要约束、与前后文相关的线索

源文件处理完后，用 `brief/detail` 概括源文件本身：

```text
update_meta({
  "ref": "__FILE_PATH__",
  "fields": {
    "brief": "<50字以内概括这个源文本文件是什么文档、服务什么分析>",
    "detail": "<总结整篇文档的核心主题、章节结构、字段/概念定义、规则约束、歧义说明和使用注意事项>"
  }
})
```

源文件 summary 的读者是后续解题 agent：读完它应该知道这篇文档讲什么、哪些字段和规则重要、遇到歧义时该按什么口径解释。

## 子智能体任务模板

给子智能体的 task 应该包含：

- 目标文件路径：`__FILE_PATH__`
- 负责行号范围：例如 L1-L500
- 要求它用 `read` 读取该范围
- 要求它返回 JSON 数组，不要写图
- JSON 每项包含：`chunk_index_hint`、`start_line`、`end_line`、`brief`、`detail`

## 完成条件

- 已为目标文件创建 `chunk` 实体
- 每个 chunk 有准确行号和中文 brief/detail
- chunk 是有检索价值的语义片段，不要求完整覆盖全文每一行
- 源文件连接到所有 chunk
- chunk 之间没有互相连接
- 源文件 meta 已更新 `brief/detail`，内容是一份整篇文档 summary
- 完成后直接停止，不要输出额外解释
"""


def _load_text_sources(workspace: Workspace, file: str | None = None) -> list[OpenFileSource]:
    if file:
        return resolve_file_sources(workspace, file, labels=("text",), allow_directory=False)
    return resolve_file_sources(workspace, ".", labels=("text",), allow_directory=True)


def _char_count_hint(source: OpenFileSource) -> int | None:
    try:
        return int(source.char_count) if source.char_count is not None else None
    except (TypeError, ValueError):
        return None


def _should_process(source: OpenFileSource, *, file: str | None, min_chars: int) -> bool:
    if file:
        return True
    char_count = _char_count_hint(source)
    if char_count is not None:
        return char_count >= min_chars
    return bool(source.file_size is not None and int(source.file_size) >= min_chars)


def _run_agent_for_file(workspace: Workspace, source: OpenFileSource, *, force: bool) -> None:
    from agent.config import create_agent, AgentSpec
    from agent.guardrail import build_guardrails

    spec = AgentSpec(mode="writer", effort="high", max_rounds=120)
    spec.tools = [
        "find", "grep", "read", "meta", "agent",
        "create_entity", "update_meta", "add_edge", "delete",
    ]
    project_name = os.path.basename(os.path.abspath(workspace.project_path))
    spec.projects = [project_name]
    spec.guardrails = build_guardrails(spec, ["round_limit"])
    agent = create_agent(workspace.project_path, spec)

    prompt = (
        PROMPT
        .replace("__FILE_PATH__", source.path)
        .replace("__FORCE__", str(force).lower())
    )
    agent.chat(prompt)


def generate(
    workspace: Workspace,
    file: str | None = None,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    force: bool = False,
    **_: object,
) -> None:
    """启动 writer agent，为文本文件生成 chunk 实体。"""
    from agent.utils import load_agent_config

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping text chunk explorer")
        return

    logger.info("=== Agent Text Chunk Explorer ===")
    sources = _load_text_sources(workspace, file=file)
    if not sources:
        logger.info("No text sources found")
        return

    processed = 0
    for source in sources:
        if not _should_process(source, file=file, min_chars=min_chars):
            continue
        logger.info("Text chunk agent processing: %s", source.path)
        _run_agent_for_file(workspace, source, force=force)
        processed += 1

    logger.info("=== Agent Text Chunk Explorer done: %s files ===", processed)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ask an agent to create text chunk entities.")
    parser.add_argument("target", help="Project/source directory")
    parser.add_argument("--file", help="Project-relative text file path")
    parser.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    workspace = Workspace(project_path=os.path.abspath(args.target))
    generate(
        workspace,
        file=args.file,
        min_chars=args.min_chars,
        force=args.force,
    )


if __name__ == "__main__":
    _main()
