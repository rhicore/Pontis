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

## 参考

- Agentic harness patterns: https://github.com/keli-wen/agentic-harness-patterns-skill
