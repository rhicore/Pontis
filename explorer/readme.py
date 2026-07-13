"""Agent README Writer — 基于已有 AI/agent 标注为项目生成库级 README 节点。

唯一职责：读取当前项目已经生成的数据库/表/列/fk 摘要，补充必要探索，
然后将结果写入当前项目的 `README` 节点。
"""
import logging

from storage.workspace import Workspace

logger = logging.getLogger(__name__)

PROMPT = """\
你是新来的数据分析师。当前 Pontis 图谱里已经有数据库文件、表、列、外键、说明文件和已有 brief/detail。
你的任务是为当前项目生成一个库级 `README` 节点，让后来打开这个项目的人先读 README 就能知道数据库里有什么。

## 目标

基于当前项目中已经存在的元数据（列上的 official 字段、数据库文件、表、列、外键、说明文件的 brief/detail），
必要时补充少量探索，然后写出一个供后续使用者阅读的项目 README。

README 的目标是：
- 快速说明这个数据库/项目是什么
- 概括主要数据对象和关系
- 记录关键数据质量事实与命名差异
- 列出核心表、关键关系、说明文件和容易混淆的字段

## 写作原则

- **复用现有摘要**：先读已经存在的 official 字段和 brief/detail
- **官方字段优先**：`official_column_description`、`official_value_description` 是人工/官方标注，高于 AI/agent 写入的 `brief/detail`；README 中的列含义、代码值和值域说明必须优先采用 official 字段
- **按需补充阅读**：已有信息覆盖 README 内容时，直接基于现有信息写作；需要说明文件正文时用 `read`
- **读取已有元数据**：用 `meta` 读取已有官方字段和摘要
- **说明文件读取方式**：CSV、Markdown、文本数据字典等说明文件先用 `meta` 理解用途和内容，需要正文时用 `read` 读取
- **用中文写 README**
- **面向数据库使用者写作**：使用数据库、表、列、说明文件、关系等用户可理解的概念
- **关系表达保守**：`fk` 写入关键外键/主从关系；普通 `rel` 写作“已验证的行级匹配关系”
- **相似字段说明**：易混淆字段写清来源表、每行代表什么、覆盖范围和值域差异
- **README 内容范围**：说明数据库里有什么，不围绕某一道题写解法
- **数据质量表达**：格式不一致、外键违规、空值等写成已观察事实
- **路径式引用**：读表/列时使用路径 ref，例如 `financial.sqlite/account`、`financial.sqlite/account/account_id`
- **写入 README 节点**：如果项目里已经有物理 `README` / `README.md` 文件节点，就更新它；没有物理文件时创建 `README:knowledge`
- **写完自检**：写入后用 `meta({"ref": "README", "property": ["detail"]})` 确认正文完整可读

## 读取顺序

1. 找到数据库文件（`*.sqlite` / `*:file:db`）
2. 读取数据库文件本身的 meta
3. 读取主要表的 meta
4. 读取 fk / rel / disambig（如果有）
5. 前面信息需要补充时，查看数据字典、字段说明、schema notes 或其他说明文件的 meta；需要正文时用 `read`

## README 应包含的结构

使用下面这套结构，必要时可略微调整标题：

1. `# <项目名>`
2. `## 概览`
   - 数据库/项目用途
   - 核心对象
   - 数据覆盖范围
3. `## 主要数据对象`
   - 每个核心表 1 小段，说明其职责
4. `## 关系结构`
   - 关键外键 / 主从关系 / 枢纽表；普通 rel 不能和外键混列
5. `## 数据质量事实`
   - 空值、同名列、尾部空格、代码列、描述文件缺口等事实
6. `## 核心对象`
   - 核心表、关键关系、关键说明文件

## 内容要求

- 使用稳定的定性描述，如“数千条记录”“少量空值”“高基数名称字段”
- 如果存在明显易混淆列或字段语义差异，写清它们分别来自哪里、代表什么、值域有什么不同
- 如果有数据库说明 CSV，吸收其中对核心对象和相似字段区别的说明
- `核心对象` 只列当前数据库中已有的核心表、关键外键、易混淆字段和说明文件。不要把普通 rel 写成关键外键。

## 写入方式

当你已经整理好 README 内容后：

1. 先检查是否已存在 `README` 或 `README.md` 文件节点
2. 若存在，直接 `update_meta` 该文件节点
3. 项目里尚未有 README 节点时，创建 `README:knowledge`，并通过 edges 连接到当前数据库节点

写法示例：

```text
update_meta({"ref": "README", "fields": {"brief": "...", "detail": "..."}})
```

如果 `README` 已存在，直接更新 `brief/detail`。

需要创建 README 文件节点时，必须同时连接到当前数据库或核心表节点，例如：

```text
create_entity({"ref": "README:knowledge", "meta": {"brief": "...", "detail": "..."}, "edges": [{"ref": "<db>:db"}]})
```

写完后再用：

```text
meta({"ref": "README", "property": ["detail"]})
```

确认 `detail` 已写入。

## 完成条件

- `README` 节点已成功写入
- 内容结构完整
- 已验证 `meta("README")` 可读
- 完成后回复 `DONE`
"""


def generate(workspace: Workspace) -> None:
    """调用 agent 生成项目 README 节点。"""
    from agent.config import create_agent
    from agent.utils import load_agent_config
    from explorer.utils.bird_metadata import explorer_tools, official_metadata_note
    from explorer.utils.agent_spec import explorer_writer_spec

    config = load_agent_config(workspace.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping README generation")
        return

    logger.info("=== Agent README Writer ===")

    spec = explorer_writer_spec(
        workspace,
        tools=explorer_tools(workspace.project_path, [
            "find", "meta", "read",
            "create_entity", "update_meta",
        ]),
        include_readme=True,
    )
    agent = create_agent(workspace.project_path, spec)

    agent.chat(PROMPT + official_metadata_note(workspace.project_path))
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
