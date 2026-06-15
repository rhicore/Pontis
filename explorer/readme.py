"""Agent README Writer — 基于已有 AI/agent 标注为项目生成库级 README 节点。

唯一职责：读取当前项目已经生成的数据库/表/列/fk 摘要，补充必要探索，
然后将结果写入项目图谱中的 `README` 节点。
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是为当前项目生成一个高质量的库级 `README` 节点。

## 目标

基于当前项目中已经存在的元数据（列上的 official 字段、数据库文件、表、列、外键、说明文件的 brief/detail），
必要时补充少量探索，然后写出一个供后续使用者阅读的项目 README。

README 的目标不是营销文案，而是：
- 快速说明这个数据库/项目是什么
- 概括主要数据对象和关系
- 记录关键数据质量事实与命名差异
- 列出核心对象和关键事实边界

## 必须遵守

- **复用现有摘要**：先读已经存在的 official 字段和 brief/detail，不要重复劳动
- **官方字段优先**：`official_column_description`、`official_value_description` 是人工/官方标注，高于 AI/agent 写入的 `brief/detail`；README 中的列含义、代码值和值域说明必须优先采用 official 字段
- **只在必要时补充探索**：如果已有信息足够，就不要过度查询
- **读取已有元数据**：用 `meta` 读取已有官方字段和摘要；不要为了 README 再去 `cat README` 或 `cat *.csv`
- **不要把说明文件当数据库去 query**：CSV、Markdown、文本数据字典等说明文件不是业务数据表
- **用中文写 README**
- **不要提及内部实现概念**：不要出现 Pontis、知识图谱、.pontis、实体节点、tool contract 等内部术语
- **路径式引用**：读表/列时使用路径 ref，例如 `financial.sqlite/account`、`financial.sqlite/account/account_id`
- **最终必须写入现有 README 文件节点**：如果项目里已经有 `README` / `README.md` 这类文件节点，就把内容写回这个文件节点的 `brief/detail`
- **不要把 README 另建成知识实体**：已有 README 文件节点时，禁止创建 `README:knowledge`、`README:doc` 等新节点
- **写完要自检**：写入后用 `meta({"ref": "README", "property": ["detail"]})` 确认正文完整可读

## 读取顺序

1. 找到数据库文件（`*.sqlite` / `*:file:db`）
2. 读取数据库文件本身的 meta
3. 读取主要表的 meta
4. 读取 fk / rel / disambig（如果有）
5. 只在前面信息不足时，再查看数据字典、字段说明、schema notes 或其他说明文件的 meta

## README 应包含的结构

使用下面这套结构，必要时可略微调整标题，但不要太花：

1. `# <项目名>`
2. `## 概览`
   - 数据库/项目用途
   - 核心对象
   - 适合回答的问题类型
3. `## 主要数据对象`
   - 每个核心表 1 小段，说明其职责
4. `## 关系结构`
   - 关键外键 / 主从关系 / 枢纽表
5. `## 数据质量事实`
   - 空值、同名列、尾部空格、代码列、描述文件缺口等事实
6. `## 核心对象`
   - 核心表、关键关系、关键说明文件

## 内容要求

- 不要写精确行数、精确列数、精确基数，避免 README 很快过时
- 可以写稳定的定性描述，如“数千条记录”“少量空值”“高基数名称字段”
- 如果存在明显易混淆列或字段语义差异，要明确写出来
- 如果有数据库说明 CSV，可以吸收其价值，但不要逐列抄表
- `核心对象` 只列当前数据库中已有的核心表、关键外键、易混淆字段和说明文件。

## 写入方式

当你已经整理好 README 内容后：

1. 先检查是否已存在 `README` 或 `README.md` 文件节点
2. 若存在，直接 `update_meta` 该文件节点
3. 只有在项目里完全不存在 README 文件节点时，才允许 `create_entity`

写法示例：

```text
update_meta({"ref": "README", "fields": {"brief": "...", "detail": "..."}})
```

如果 `README` 已存在，就不要重复创建，只更新 `brief/detail`。

只有不存在 README 文件节点时，才可以创建一个最简单的 README 节点，例如：

```text
create_entity({"ref": "README"})
update_meta({"ref": "README", "fields": {"brief": "...", "detail": "..."}})
```

写完后再用：

```text
meta({"ref": "README", "property": ["detail"]})
```

确认 `detail` 已写入。

## 完成条件

- `README` 节点已成功写入项目图谱
- 内容结构完整
- 已验证 `meta("README")` 可读
- 完成后直接停止，不要输出额外总结
"""


def generate(workspace: Workspace) -> None:
    """调用 agent 生成项目 README 节点。"""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping README generation")
        return

    logger.info("=== Agent README Writer ===")

    spec = explorer_writer_spec(
        workspace,
        tools=[
            "find", "meta", "query",
            "create_entity", "update_meta", "add_edge", "delete",
        ],
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT)
    logger.info("=== Agent README Writer done ===")
    return _preprocess_metrics(agent)


def _preprocess_metrics(agent) -> dict:
    if not hasattr(agent, "llm_metrics"):
        return {}
    metrics = agent.llm_metrics()
    return {
        "preprocess_llm_calls": int(metrics.get("llm_rounds", 0) or 0),
        "preprocess_llm_input_tokens": int(metrics.get("input_tokens", 0) or 0),
        "preprocess_llm_cached_input_tokens": int(metrics.get("cached_input_tokens", 0) or 0),
        "preprocess_llm_uncached_input_tokens": int(metrics.get("uncached_input_tokens", 0) or 0),
        "preprocess_llm_output_tokens": int(metrics.get("output_tokens", 0) or 0),
        "preprocess_llm_total_tokens": int(metrics.get("total_tokens", 0) or 0),
    }
