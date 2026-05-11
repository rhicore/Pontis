# TODO

## 待支持的格式

| 类型 | 后缀 | 关键元数据 |
|---|---|---|
| 开放表格格式 | `_delta_log/`, `.avro` (Manifest) | ACID 版本、时间旅行快照、Schema 演变、分区键 |
| 列式文件 | `.parquet`, `.orc`, `.lance` | 列级 Min/Max、空值率、压缩比、向量维度 |
| 行式/交换格式 | `.avro`, `.jsonl` | Schema 定义、字段偏移、编码格式 |
| 图像 | `.jpg`, `.png`, `.webp` | 尺寸、色彩空间、EXIF |
| 对象存储 | S3, OSS | 桶策略、分区前缀 |
| SaaS | Notion, Slack, Jira | 页面/频道/工单结构 |
| 项目 | dbt, Git Repo | build/run/test 操作 |

中心思想：文件名后缀决定元数据 schema，不同类型的元数据结构不同。

## AI Enricher

- 人工注释机制（允许用户对实体添加/编辑注释，区分 AI 生成与人工标注）
- 语义层定义：将一组可复用的 Views 作为语义层（[Towards Agentic Schema Refinement]）
- 基于模式的表分组（[ReFoRCE]：按命名模式压缩同构表，如 GA_SESSIONS_20160801..GA_SESSIONS_20170801 → 96%+ 压缩率）

## 已知问题

- **语义准确性**：AI 摘要依赖采样数据，若采样不具备代表性，可能误导 Agent
- **数据泄露风险**：LLM 调用时样本值直接发送至 API，处理敏感数据时需脱敏机制

## 代码质量与架构债

当前代码库已经形成了比较清楚的四层骨架：`agent`、`tool`、`storage`、`extractor`。整体不是脚本堆叠，核心抽象也基本成立，例如 Agent 主循环、ToolRegistry、Guardrail、Store/Backend、Extractor pipeline 都是独立概念。

但现阶段更接近快速演进中的研究型系统，抽象边界还不够硬。长期看，主要风险集中在核心类偏厚、字符串约定偏多、异常语义偏软、测试体系不够标准化。

### 总体评估

| 维度 | 评分 | 说明 |
|---|---:|---|
| 代码优美度 | 6/10 | 有分层和注释，但大文件、大类、硬编码注册和宽泛异常较多 |
| 架构清晰度 | 7/10 | 四大模块边界清楚，核心概念完整 |
| 模块权责明确度 | 6/10 | 顶层模块职责明确，但 `FSStore`、工具层、Extractor registry 内部职责偏重 |
| 日后可拓展性 | 6.5/10 | 可扩展方向存在，但新增数据源、工具、提取模块时同步点较多 |

### P0：优先处理

- **修复文件/数据库 source 查询中的伪 OR**
  - 现状：`tool/DB_query/tool.py` 和 `extractor/modules/utils/src.py` 使用 `WHERE f.name = $file OR f.path = $file`，但当前 Cypher 子集不支持 `OR`，实际会退化成错误语义。
  - 风险：嵌套目录中的数据库文件和普通文件会出现“明明存在但查不到”的失败。
  - 建议：不要依赖 `OR`；先按 `path` 精确匹配，再回退到 `name` 匹配，必要时显式区分“未找到”和“不唯一”。

- **统一通过 `src` 端口访问数据库**
  - 现状：`DB_query` 和部分 source helper 拿到 `f.src` 后仍优先取 `path` 并自己 `sqlite3.connect()`，没有把 `db_connect` 当成主入口。
  - 风险：一旦底层 source 不再是本地 SQLite 文件，而是 DuckDB、远程 DB 或其他 provider，tool 会重新写死访问逻辑。
  - 建议：优先使用 `db_connect`，`path` 只作为回退。

- **去掉 storage 综合测试对仓库 fixture 的硬依赖**
  - 现状：`scripts/storage/test_store.py` 末尾仍依赖 `example_data/context`。
  - 风险：测试不是自包含的，CI 或新环境中容易直接失败。
  - 建议：测试运行时自己构造临时目录结构和最小 fixture；若必须依赖外部数据，至少显式 skip。

- **CSV extractor 改成 scoped ref**
  - 现状：CSV 列节点和连边仍大量依赖裸列名。
  - 风险：多个 CSV 都有 `id`、`name` 等常见列时，容易错连、重用错节点或覆盖错误元数据。
  - 建议：统一使用 canonical ref / scoped ref，不再按裸 `name` 定位列节点。

- **拆分 `FSStore` 过重职责**
  - 现状：`storage/stores/fs.py` 同时承担文件系统扫描、虚实体、名称索引、inode 索引、路径解析、meta fallback、虚属性 enrichment、跨项目边等职责。
  - 风险：后续增加新数据源、调整引用解析、修改虚实体策略时，容易引发大范围回归。
  - 建议：拆出 `NameResolver`、`VirtualEntityProvider`、`FileSystemScanner`、`MetaEnricherAdapter` 等小组件，`FSStore` 只负责编排。

- **统一工具契约**
  - 现状：工具 schema 集中在 `agent/tools.py`，prompt 在 `agent/tool_use/*/prompt.py`，执行逻辑在 `tool/*/tool.py`，三者靠字符串名称对齐。
  - 风险：新增或修改工具时容易出现 schema、prompt、executor 不一致。
  - 建议：每个工具目录导出统一 `ToolSpec`，包含 `name`、`schema`、`description`、`executor`，ToolRegistry 只负责加载和组合。

- **让失败显式化**
  - 现状：`ToolRegistry.execute()` 将所有异常转成字符串；Extractor pipeline 捕获模块异常后只 warning 并继续。
  - 风险：调用方无法可靠区分“正常无结果”和“内部失败”，benchmark 或 Agent 可能基于错误文本继续推理。
  - 建议：引入结构化结果，例如 `ToolResult(ok, content, error)`；Extractor 支持 `fail_fast`、`continue_on_error`、错误汇总报告。

### P1：中期优化

- **给 Cypher 执行器抽 GraphView 协议**
  - 现状：`CypherExecutor` 直接访问 `store._id_index`、`store._adjacent`、`store._get_meta()`、`store._create_node()` 等私有结构。
  - 风险：`MergedStoreView` 被迫伪装成 Store，内部字段耦合强，后续很难替换 view、缓存层或远程图视图。
  - 建议：抽最小 `GraphView` 接口，让持久 Store 和 merged view 都通过稳定协议被执行器消费。

- **为 MergedStoreView 增加缓存或懒加载**
  - 现状：读查询会频繁重建 merged view，并触发模块全量虚图枚举。
  - 风险：随着 source 规模增大，查询成本会迅速失控。
  - 建议：至少引入版本缓存；进一步支持按 selector 懒加载虚节点。

- **重构 Extractor 模块注册**
  - 现状：`extractor/engine.py` 手写导入所有模块，`CONFIG_MODULES` 靠模块名特判是否传 config。
  - 风险：新增模块需要改多个地方，可选模块 ImportError 被静默吞掉，不利于发现依赖问题。
  - 建议：每个 extractor 模块暴露 `ModuleSpec(name, requires_config, phase, dependencies)`，engine 自动发现或集中声明 spec。

- **减少字符串拼 Cypher**
  - 现状：部分 extractor 模块通过 f-string 拼 Cypher，参数化只在部分位置使用。
  - 风险：遇到特殊字符、重名实体、引号、路径分隔符时容易产生隐性 bug。
  - 建议：统一使用参数化 `workspace.cypher(..., params=...)`，或者提供 Store 层 typed API，例如 `create_node()`、`add_edge()`、`set_props()`。

- **把 Cypher 子集边界文档化并补测试**
  - 现状：`storage/cypher.py` 自研了 Cypher 子集，已有 AST 和执行器，但能力边界主要靠代码注释和单个脚本测试体现。
  - 风险：调用方容易写出“看起来像 Cypher 但不支持”的查询。
  - 建议：补充 `docs/architecture/cypher_subset.md`，明确支持语法、禁止语法、转义规则、参数化规则；把现有脚本测试迁移到 pytest。

- **收敛命名与兼容层**
  - 现状：`PontusAgent` 与项目名 Pontis 不一致，`agent.agent.__getattr__` 保留向后兼容重导出。
  - 风险：长期会增加认知成本，并让外部 API 边界模糊。
  - 建议：确定公开 API 名称，保留一个 release 周期兼容层后移除。

### P2：工程化增强

- **建立标准测试布局**
  - 现状：已有 `scripts/storage/test_store.py`，覆盖 Store/Cypher 较完整，当前运行结果为 `91/91 passed`，但不是标准测试入口。
  - 建议：迁移到 `tests/` + pytest，至少覆盖 storage、tool、extractor smoke、agent guardrail 单元测试。

- **区分实验脚本与产品代码**
  - 现状：`scripts/BIRD/`、`scripts/reference/`、`rubbish/` 与核心代码同仓存在，容易干扰代码搜索和质量判断。
  - 建议：将实验、benchmark、参考实现、废弃代码分区管理；`rubbish/` 若仍有价值，改名为 `experiments/` 或归档到文档。

- **补充类型与 lint**
  - 现状：类型标注有使用，但没有看到统一 lint/typecheck 配置。
  - 建议：引入 `ruff`、`mypy` 或 `pyright` 的轻量配置，先只检查核心目录：`agent/`、`storage/`、`tool/`、`extractor/`。

- **抽象数据源能力矩阵**
  - 现状：`Store` 基类暴露 `open_db`、`open_file`、`resolve_data_path` 等能力，非文件型数据源会通过 `NotImplementedError` 表达不支持。
  - 风险：接入 S3、SaaS、dbt、Git 等数据源时，工具层需要不断判断能力是否存在。
  - 建议：增加 `StoreCapabilities`，显式声明 `file_access`、`sql_access`、`cache_access`、`write_graph`、`virtual_entities` 等能力。

### 建议执行顺序

1. 先标准化测试入口，把 `scripts/storage/test_store.py` 迁移到 pytest，确保后续重构有回归网。
2. 拆 `ToolSpec`，统一工具 schema、prompt、executor 注册方式。
3. 拆 `FSStore`，优先分离名称解析与虚实体发现。
4. 重构 Extractor registry，引入 `ModuleSpec` 和失败汇总。
5. 收敛脚本目录与废弃目录，降低仓库噪音。

## 参考

- Agentic harness patterns: https://github.com/keli-wen/agentic-harness-patterns-skill
