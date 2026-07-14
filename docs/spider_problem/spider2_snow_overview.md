# Spider 2.0-Snow 数据集总览

最后核对：2026-07-14。本文统计 Pontis 当前处理的 Spider2-Snow。

## 全量规模

Spider2-Snow 有 547 题、152 个 Snowflake 数据库。官方 DDL 与 JSON 合并后，Pontis
可建出 13,583 个 relation、1,248,030 个 column。

| 项目 | 数量 | 口径 |
|---|---:|---|
| Databases | 152 | 去重 database ID |
| Schemas | 272 | 官方 schema 目录 |
| DDL tables / unknown relations | 13,022 / 526 | DDL 结构面 |
| DDL columns | 1,247,599 | 仅解析 DDL |
| Official merged relations | 13,583 | DDL+JSON 建图面 |
| Official merged columns | 1,248,030 | DDL+JSON 建图面 |
| JSON detailed tables | 7,860 | 有逐表 metadata |
| JSON detailed columns | 522,268 | 有详细 metadata |
| Explicit foreign keys | 0 | 官方资源无可用 FK |
| External-knowledge cases | 107 | 题目显式指定文档 |
| Public gold SQL | 120 | 覆盖 53 个数据库 |

`Official merged` 是模型可见结构和 Pontis 建图的主口径；`JSON detailed` 是具有
description、类型和 sample rows 的详细子集。

## 每库统计

先按 database 聚合，再对 152 个 database 计算：

| 每库指标 | 总数 | 最小 | 平均 | 中位数 | P90 | P95 | 最大 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Schemas | 272 | 1 | 1.79 | 1 | 3.9 | 6 | 11 |
| Official relations | 13,583 | 1 | 89.36 | 16 | 142.7 | 286.35 | 5,173 |
| Official columns | 1,248,030 | 4 | 8,210.72 | 234 | 4,073.3 | 16,050.55 | 725,140 |
| JSON tables | 7,860 | 1 | 51.71 | 16 | 138.2 | 274.55 | 785 |
| JSON columns | 522,268 | 4 | 3,435.97 | 223 | 3,750.1 | 8,716.3 | 71,832 |

核心结论：

- 典型数据库约 1 个 schema、16 张表、223-234 列。
- Official 表数平均 89.36，但中位数只有 16，最大 5,173。
- Official 列数平均 8,210.72，但中位数只有 234，最大 725,140。
- JSON 详细面平均 51.71 张表、3,435.97 列，中位数为 16 张表、223 列。
- 平均值被少数分片库和宽表库显著抬高，不能代表普通数据库规模。

## 分布

| JSON tables per DB | 库数 | JSON columns per DB | 库数 |
|---|---:|---|---:|
| 1-10 | 58 | 1-100 | 54 |
| 11-25 | 42 | 101-500 | 49 |
| 26-50 | 20 | 501-1,000 | 10 |
| 51-100 | 11 | 1,001-5,000 | 26 |
| 101-250 | 12 | 5,001-10,000 | 5 |
| 251-500 | 8 | 10,001-50,000 | 3 |
| >500 | 1 | >50,000 | 5 |

120/152（78.9%）不超过 50 张详细表；132/152（86.8%）不超过 5,000 列。超过
50,000 列的 5 个库合计 349,157 列，占全部 JSON 列的 66.9%。

## 极值来源

| Database | Schemas | Official tables | Official columns | JSON tables | JSON columns |
|---|---:|---:|---:|---:|---:|
| `GITHUB_REPOS_DATE` | 4 | 5,173 | 725,140 | 38 | 322 |
| `EBI_CHEMBL` | 1 | 785 | 5,337 | 785 | 5,337 |
| `FEC` | 5 | 486 | 71,832 | 486 | 71,832 |
| `CENSUS_BUREAU_ACS_1` | 3 | 351 | 69,170 | 351 | 69,170 |
| `CENSUS_BUREAU_ACS_2` | 3 | 296 | 68,434 | 296 | 68,434 |
| `SDOH` | 8 | 294 | 68,863 | 294 | 68,863 |
| `COVID19_USA` | 3 | 285 | 70,858 | 285 | 70,858 |

`GITHUB_REPOS_DATE` 是 DDL 日期分片膨胀；`EBI_CHEMBL` 表多但窄；FEC、COVID19、
ACS 和 SDOH 同时包含大量分片表和宽表。

## Pontis 处理结论

- Schema truth 来自官方 `DDL.csv` 和 JSON；live Snowflake 只用于执行和取值验证。
- 表列规模主要来自物理分片、分析型宽表和多个业务数据产品。
- 当前 392 个 table group 覆盖 5,361/7,860 张 JSON 表，表级认知单位降至 2,891；
  142/152 个库压缩后不超过 50 个表级单位。
- 物理 `database -> schema -> table -> col` 始终保留；`table_group`、`logical_col`、
  `topic` 和 `column_domain` 是额外认知实体。
- Table group 不能压缩单张宽表内部的指标列，表内 refinement 仍是主要缺口。

## 关联文档

- [Schema Visibility](spider2_snow_schema_visibility_policy.md)
- [Table Group Audit](spider2_table_group_all_db_audit.md)
- [Gold Join Audit](spider2_snow_gold_join_overlap_metrics.md)
- [Evaluation Semantics](spider2-bird-evaluation-semantics.md)
- [Column Domain](../explorer_and_extractors/column_relation_discovery.md)
