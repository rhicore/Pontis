"""Agent Summary — 为项目中所有实体生成 brief 和 detail。

协调者先建立全局认知（识别枢纽表和依赖关系），再按依赖顺序分配子智能体。
"""
import logging

from storage import Store

logger = logging.getLogger(__name__)

PROMPT = """\
你的任务是为项目中所有实体生成 brief 和 detail。很多实体的 summary 从未生成过，\
你需要覆盖所有实体，不只是补全缺失的。

## 核心策略：依赖感知的协调者

你是协调者。关键原则：**理解有先后顺序**。某些表的含义依赖于枢纽表（被大量引用的核心表）\
，必须先理解枢纽表，才能正确理解依赖它的表。

## 工作流程

### 第一步：发现项目结构
用 glob 发现所有数据库文件和其他文件。

### 第二步：对每个数据库建立全局认知
1. glob 查看所有表和视图
2. meta 查看每张表的基本信息（行数、列数、主键）
3. 查看 .fk、.overlap、.rel 实体，了解表间关系
4. **识别枢纽表**：被外键引用最多的、被 overlap 关联最多的表。\
   这些表是理解整个数据库的钥匙，必须先处理。

### 第三步：按依赖顺序分配子智能体

确定处理顺序：
1. 先处理枢纽表（被依赖最多的核心表）
2. 再处理依赖枢纽表的表
3. 最后处理独立的表

对每张表/视图启动子智能体：
- task 中必须包含：
  a. 数据库名称和你对整体结构的理解
  b. 这张表的基本信息（从 meta 获取）
  c. 与其他表的关系（外键、overlap）
  d. **如果这张表依赖枢纽表，告诉子智能体枢纽表的 summary（你从之前的结果获取）**
  e. 写 summary 的要求
- max_rounds: 8-10
- description: 填表名

### 第四步：写数据库级 summary
所有表的 summary 完成后，基于你对所有表的理解，用 update_meta 为数据库文件写 brief 和 detail。

### 第五步：处理其他文件
JSON、文本等文件不需要子智能体，你直接读取内容并写 brief/detail。

## 质量要求
- brief ≤50字，精炼概括用途和业务含义
- detail 完整描述：结构、内容、业务含义、与其他实体的关系
- 不要用具体数字（行数会过时），用定性描述
- 已有的 brief/detail 如果质量不错，不要覆盖
- 用中文
- 不要提及 Pontis、知识图谱、.pontis 等内部概念
"""


def generate(store: Store, *, debug: bool = False) -> None:
    """为所有实体生成/优化 AI 总结。"""
    from agent.agent import PontisAgent
    from agent.tools import build_writer_registry, enable_debug
    from agent.prompt import build_prompt
    from agent.config import load_agent_config

    config = load_agent_config(store.project_path)
    if not config["api_key"]:
        logger.warning("Agent not configured (no API key), skipping agent summary")
        return

    logger.info("=== Agent Summary ===")

    registry = build_writer_registry()
    if debug:
        enable_debug(registry)

    agent = PontisAgent(
        store.project_path,
        tools=registry,
        system_prompt=build_prompt("writer", store.project_path, debug=debug),
    )

    agent.chat(PROMPT)
    logger.info("=== Agent Summary done ===")
