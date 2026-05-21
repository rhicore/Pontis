"""Agent JSON Pattern Summary — 用 jd 和 pattern 节点总结 JSON 文件。

独立执行:
    python -m explorer.json_pattern_summary ./my_data --file context/json/posts.json
"""
import argparse
import logging

from storage.workspace import Workspace
from tool.utils.workspace_access import OpenFileSource, resolve_file_sources

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是利用 jd 和指定 JSON 文件下的 pattern 节点，为 JSON 文件本身以及每个 pattern 实体写中文 brief/detail。

## 目标 JSON

- JSON 文件：`__JSON_PATH__`
- 是否强制重写已有高质量摘要：`__FORCE__`

## 工作目标

1. 给 JSON 文件本身写一个总 summary：
   - brief：最多 50 字，概括这个 JSON 文件是什么数据。
   - detail：说明顶层结构、重要字段/数组/map、数据语义、和下游分析时需要注意的结构线索。

2. 给这个 JSON 文件下的每个 `pattern` 实体写 summary：
   - brief：最多 50 字，概括该 JSON path 对应的结构片段。
   - detail：说明该 JSON path 的结构、字段含义、数组/map 形态、关键约束、与父子结构的关系，以及后续解题时怎样使用它。

## 必须使用 jd

- 必须先调用 `jd({"ref": "__JSON_PATH__"})` 查看顶层结构。
- 对重要子路径继续调用 `jd`，例如 `__JSON_PATH__#/records`、`__JSON_PATH__#/data`、`__JSON_PATH__#/0`。
- `jd` 只展示一层；不要试图一次展开整个 JSON。
- 大数组只抽看前几项、schema keys 和必要分页，不要全量遍历。

## pattern 读取方式

1. 用源 JSON 文件的图边找 pattern：

```text
find({"ref": "__JSON_PATH__/*:pattern", "limit": 500})
```

找到该 JSON 文件下的 pattern 节点。

2. 对每个 pattern 用 `meta` 读取：

```text
meta({"ref": "<pattern ref>", "property": ["json_path", "type", "pattern", "brief", "detail"]})
```

3. 如果 `force=false` 且 JSON 文件或 pattern 已经有高质量 brief/detail，可以保留；否则更新。

## 写入规则

- 写 JSON 文件总 summary：

```text
update_meta({"ref": "__JSON_PATH__", "fields": {"brief": "...", "detail": "..."}})
```

- 写 pattern summary：

```text
update_meta({"ref": "<pattern ref>", "fields": {"brief": "...", "detail": "..."}})
```

工具参数必须是合法 JSON。`brief/detail` 文本里不要裸写英文双引号 `"`；字段值示例优先用中文引号、单引号或反引号，避免 `update_meta` 参数解析失败。

## summary 质量要求

- 用中文。
- 基于 `jd` 和 pattern 原始结构，不要虚构业务含义。
- 不要做全图搜索来查询 JSON 行级条件；需要看 JSON 内容时使用 `jd` 做结构和样例探查。
- 字段名能直接说明语义时可以解释；字段名模糊时要明确“不确定”。
- 不要写精确数组长度、精确 key 数、精确行数；这些会变。
- pattern detail 必须包含 JSON path，例如 `$`、`$.records` 或 `$.items.[n]`。
- 包装层 pattern 要说明它是包装结构；数据主体 pattern 要说明字段和数组/map 语义。

## 子智能体策略

如果 pattern 数量很多，可以启动子智能体分批处理 pattern。

子智能体只负责阅读 `jd/meta` 并返回建议的 brief/detail JSON；最终 `update_meta` 由你统一执行。

## 完成条件

- JSON 文件本身已有 brief/detail。
- `__JSON_PATH__/*:pattern` 下的每个 pattern 都已有 brief/detail。
- 完成后直接停止，不要输出额外解释。
"""


def _load_json_sources(workspace: Workspace, file: str | None = None) -> list[OpenFileSource]:
    if file:
        sources = resolve_file_sources(workspace, file, labels=("json",), allow_directory=False)
        if sources:
            return sources
        fallback = resolve_file_sources(workspace, file, allow_directory=False)
        return [src for src in fallback if "json" in src.labels or src.path.lower().endswith(".json")]

    sources = resolve_file_sources(workspace, ".", labels=("json",), allow_directory=True)
    if sources:
        return sources
    fallback = resolve_file_sources(workspace, ".", allow_directory=True)
    return [src for src in fallback if "json" in src.labels or src.path.lower().endswith(".json")]


def _run_agent_for_json(workspace: Workspace, source: OpenFileSource, *, force: bool) -> None:
    from agent.config import create_agent
    from explorer.utils.agent_spec import explorer_writer_spec

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "jd", "meta", "agent",
            "update_meta",
        ],
        effort="high",
        max_rounds=120,
    )
    agent = create_agent(workspace.project_path, spec)

    prompt = (
        PROMPT
        .replace("__JSON_PATH__", source.path)
        .replace("__FORCE__", str(force).lower())
    )
    agent.chat(prompt)


def generate(
    workspace: Workspace,
    file: str | None = None,
    *,
    force: bool = False,
) -> None:
    """启动 writer agent，为 JSON 文件和 pattern 节点写 summary。"""
    from agent.utils import load_agent_config

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping JSON pattern summary")
        return

    logger.info("=== Agent JSON Pattern Summary ===")
    sources = _load_json_sources(workspace, file=file)
    if not sources:
        logger.info("No JSON sources found")
        return

    for source in sources:
        logger.info("JSON pattern summary agent processing: %s", source.path)
        _run_agent_for_json(workspace, source, force=force)

    logger.info("=== Agent JSON Pattern Summary done: %s files ===", len(sources))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ask an agent to summarize JSON and pattern nodes.")
    parser.add_argument("target", help="Project/source directory")
    parser.add_argument("--file", help="Project-relative JSON file path")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    workspace = Workspace(project_path=os.path.abspath(args.target))
    generate(workspace, file=args.file, force=args.force)


if __name__ == "__main__":
    _main()
