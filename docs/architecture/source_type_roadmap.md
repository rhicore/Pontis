# 数据源类型扩展路线图

本文记录 Pontis 后续可以支持的数据源类型，以及按当前架构落地时的建模建议。

## 背景判断

Pontis 的价值不在于把所有原始数据搬进统一存储，而在于把不同数据源投影成可查询、可更新、可复用的图结构：

- source module 负责把外部数据源暴露成虚拟节点、虚拟边、虚属性和 `src` 原生访问端口。
- persistent graph 负责保存用户知识、抽取出的元数据、语义关系、人工修正和跨项目经验。
- extractor / tool / agent 在需要精确执行时再通过 `src` 回到原始数据。

因此，新数据源优先级应按以下标准排序：

1. 能否自然暴露 schema、field、table、record、object、lineage、owner、quality 等图结构。
2. 能否提供稳定的 `src` 端口，而不是要求 Pontis 复制全量数据。
3. 能否被 extractor 渐进式沉淀统计、样本、语义、join 关系和经验。
4. 能否显著帮助 agent 理解真实数据项目的上下文。

## 设计边界

新增数据源建议遵守以下边界：

- **先 metadata，后 query**：第一阶段只做发现、虚图投影和基础元数据；第二阶段再做 sampled read / query；第三阶段再做增量同步和复杂 extractor。
- **先 read-only，后 write-back**：默认只读接入，避免把 Pontis 做成外部系统的写入编排器。
- **结构关系用边表达**：依赖、字段映射、lineage、FK、dashboard 引用、task reads/writes 等都应建边，而不是只压平成属性。
- **source module 保持薄层**：模块负责虚实体、虚边、虚属性、`src` 和匹配规则；复杂 profiling、AI summary、质量检查等放在 extractor。
- **原始数据留在原处**：Pontis 保存的是可推理的中间层和可复用知识，不替代数据库、对象存储或 BI 平台。

## 优先级路线图

### P0：最贴近当前架构，最快出效果

#### 1. Parquet / Arrow / Feather

这是最值得优先支持的数据源之一。它们和 CSV/TSV 一样有列结构，但更接近真实数据湖、离线特征和分析项目。

建议图结构：

```text
(:file:parquet)-[:has_table]->(:table)
(:table)-[:has_column]->(:col)
(:col)-[:has_type]->(:type)
```

建议首批能力：

- 读取 schema、row group、列类型、nullable、压缩方式。
- 暴露 `file -> table -> column` 虚子图。
- 支持 column stats、sample、topK、null ratio、min/max、distinct sketch。
- 可选接入 DuckDB / PyArrow 作为查询端口。

#### 2. JSONL / NDJSON / nested JSON

JSONL 适合事件、日志、API dump 和半结构化数据。相比普通 JSON 文件，JSONL 更接近 record stream，适合抽取字段路径和事件类型。

建议图结构：

```text
(:file:jsonl)-[:has_record_schema]->(:json_schema)
(:json_schema)-[:has_field]->(:json_field)
(:json_field)-[:nested]->(:json_field)
(:json_field)-[:similar_to]->(:col)
```

建议首批能力：

- 采样推断字段路径、基础类型、nullable、数组元素结构。
- 识别稳定字段、稀疏字段和动态 key pattern。
- 将 nested path 与数据库列、CSV 列做 overlap / semantic join 推断。

#### 3. PostgreSQL / MySQL / MariaDB

主流 OLTP 数据库可以复用当前 SQLite schema module 的建模方式，重点暴露 catalog / schema / table / view / column / FK / index / constraint。

建议图结构：

```text
(:database)-[:has_schema]->(:schema)
(:schema)-[:has_table]->(:table)
(:schema)-[:has_view]->(:view)
(:table)-[:has_column]->(:col)
(:table)-[:has_index]->(:index)
(:table)-[:has_constraint]->(:constraint)
(:col)-[:fk_to]->(:col)
(:view)-[:depends_on]->(:table)
```

建议首批能力：

- 只读连接和 schema introspection。
- table / view / column / FK / index / constraint 虚节点。
- row_count 估算、column stats 抽样、PK/FK 验证。
- query history 后续可作为 join pattern 和 usage knowledge 的来源。

#### 4. dbt project

dbt 不只是另一种文件格式，而是数据项目的语义骨架，应作为较高优先级数据源。

建议图结构：

```text
(:dbt_project)-[:has_model]->(:model)
(:dbt_project)-[:has_source]->(:source)
(:model)-[:depends_on]->(:model)
(:model)-[:selects_from]->(:source)
(:model)-[:has_column]->(:col)
(:test)-[:validates]->(:model_or_col)
(:exposure)-[:depends_on]->(:model)
(:macro)-[:used_by]->(:model)
```

建议首批能力：

- 解析 `manifest.json`、`catalog.json` 和项目 YAML。
- 抽取 model/source/test/exposure/macro/metric。
- 抽取模型依赖和列级描述。
- 将 dbt model 与 warehouse table / local SQL 文件关联。

### P1：增强真实数据项目理解能力

#### 5. DuckDB

DuckDB 可以既是数据源，也可以是多格式本地执行端。它能帮助 Pontis 对 Parquet、CSV、JSON、SQLite、Arrow 等格式提供统一 SQL 查询能力。

建议能力：

- 暴露 DuckDB database / schema / table / view / column。
- 作为 Parquet/CSV/JSONL 的 query adapter。
- 保存 queryable_by 关系：`(:source)-[:queryable_by]->(:duckdb_connection)`。

#### 6. Excel workbook

Excel 常保存大量业务语义：sheet 名、公式、命名区域、隐藏 sheet、批注和手工表结构。

建议图结构：

```text
(:excel_file)-[:contains]->(:sheet)
(:sheet)-[:has_column]->(:col)
(:sheet)-[:has_formula]->(:formula)
(:formula)-[:references]->(:sheet_or_range)
(:sheet)-[:has_named_range]->(:range)
```

#### 7. Jupyter Notebook

Notebook 是数据分析项目中非常重要的上下文来源。

建议图结构：

```text
(:notebook)-[:has_cell]->(:cell)
(:cell:code)-[:reads]->(:data_source)
(:cell:code)-[:produces]->(:artifact)
(:cell:markdown)-[:documents]->(:entity)
```

首批可以只抽取 cell 顺序、markdown heading、code import、SQL 字符串、文件读取路径和输出 artifact。

#### 8. S3 / GCS / Azure Blob / MinIO

对象存储不是简单远程文件系统，关键在 bucket、prefix、object、version、partition 和 manifest。

建议图结构：

```text
(:bucket)-[:has_prefix]->(:prefix)
(:prefix)-[:contains]->(:object)
(:object)-[:has_version]->(:object_version)
(:prefix)-[:represents_partition]->(:partition)
```

首批能力：

- list bucket / prefix 的 metadata-only 模式。
- 识别 partition path，如 `dt=2026-05-12/country=US/`。
- 识别对象格式、大小、modified_at、etag。
- 和 Parquet / Delta / Iceberg 模块联动。

#### 9. Airflow / Dagster / Prefect

调度系统能补足数据 lineage、freshness 和失败原因。

建议图结构：

```text
(:dag)-[:has_task]->(:task)
(:task)-[:upstream_of]->(:task)
(:task)-[:reads]->(:dataset)
(:task)-[:writes]->(:dataset)
(:run)-[:materialized]->(:asset)
(:run)-[:failed_on]->(:error)
```

### P2：平台级和企业级扩展

#### 10. BigQuery / Snowflake / Redshift / ClickHouse

分析型仓库的价值不只在 schema，还在 query history、usage、cost、partition、policy tag 和 lineage。

建议能力：

- database / schema / dataset / table / view / column。
- partition / cluster key / materialized view。
- query history、常见 join、常用字段、下游 dashboard。
- cost profile 和 freshness metadata。

#### 11. Delta Lake / Iceberg / Hudi

这些表格式天然适合图建模，因为它们包含 snapshot、manifest、schema evolution 和 data files。

建议图结构：

```text
(:lake_table)-[:has_snapshot]->(:snapshot)
(:snapshot)-[:uses_manifest]->(:manifest)
(:manifest)-[:contains_file]->(:data_file)
(:lake_table)-[:has_schema_version]->(:schema)
(:schema)-[:has_column]->(:col)
(:schema)-[:evolves_to]->(:schema)
```

#### 12. BI / Semantic Layer

Looker、Tableau、Power BI、Superset、Metabase、Cube、MetricFlow 等系统保存的是人类确认过的业务语义。

建议图结构：

```text
(:dashboard)-[:contains]->(:chart)
(:chart)-[:queries]->(:table)
(:chart)-[:uses_metric]->(:metric)
(:metric)-[:defined_by]->(:sql_expression)
(:metric)-[:has_dimension]->(:dimension)
(:dashboard)-[:owned_by]->(:user_or_team)
```

#### 13. Data catalog / governance

DataHub、OpenMetadata、Amundsen、Collibra 等系统本身就接近知识图谱，适合接入 Pontis。

建议图结构：

```text
(:dataset)-[:owned_by]->(:owner)
(:dataset)-[:tagged_as]->(:tag)
(:dataset)-[:has_glossary_term]->(:glossary_term)
(:dataset)-[:upstream_of]->(:dataset)
```

#### 14. Data quality

Great Expectations、Soda、Deequ 等质量系统可以让 agent 知道某个字段是否可信。

建议图结构：

```text
(:expectation_suite)-[:has_expectation]->(:expectation)
(:expectation)-[:validates]->(:col)
(:validation_run)-[:failed]->(:expectation)
(:quality_result)-[:observed_on]->(:dataset)
```

## 更发散的数据源清单

| 家族 | 数据源例子 | 核心图节点 |
| --- | --- | --- |
| 文件表格 | CSV, TSV, Parquet, Arrow, Feather, Excel | file, sheet/table, column |
| 半结构化 | JSON, JSONL, XML, YAML, Avro, Protobuf | schema, field, nested path |
| 本地 DB | SQLite, DuckDB, LiteFS | db, table, view, col, fk |
| OLTP DB | PostgreSQL, MySQL, SQL Server, Oracle | catalog, schema, table, col, constraint |
| OLAP/Warehouse | BigQuery, Snowflake, Redshift, ClickHouse | dataset, table, partition, query, lineage |
| Data lake | S3, GCS, Delta, Iceberg, Hudi | bucket, object, table, snapshot, manifest |
| Transformation | dbt, SQLMesh, SQL files | model, source, macro, dependency |
| Orchestration | Airflow, Dagster, Prefect, Argo | dag, task, run, asset |
| BI/Semantic | Looker, Tableau, Power BI, Superset | dashboard, chart, metric, dimension |
| Catalog/Governance | DataHub, OpenMetadata, Collibra | dataset, owner, tag, glossary, lineage |
| Quality | Great Expectations, Soda, Deequ | expectation, validation, result |
| Streaming | Kafka, Pulsar, Kinesis, Schema Registry | topic, schema, producer, consumer |
| Observability | logs, traces, metrics, Prometheus, Datadog | service, event, span, metric |
| SaaS | Salesforce, Stripe, HubSpot, Jira | object, field, sync, relationship |
| ML/AI | MLflow, W&B, model registry, vector DB | run, model, artifact, embedding, chunk |
| Code/project | Git, notebooks, SQL files, docs | file, commit, cell, query, section |

## 建议的落地顺序

如果目标是尽快让 Pontis 展示出更强的数据项目理解能力，建议顺序是：

1. **Parquet / Arrow schema module**：复用 CSV 的列建模思路，成本低、收益高。
2. **dbt project module**：直接接入语义、lineage、test 和字段描述。
3. **PostgreSQL / MySQL schema module**：证明 Pontis 可连接真实业务数据库。
4. **JSONL / nested JSON schema module**：补足事件、日志和半结构化数据。
5. **S3 + Parquet / Delta / Iceberg metadata module**：进入数据湖场景。
6. **BI / Metric layer module**：让 agent 能回答业务语义问题，而不只是 schema 问题。
7. **Airflow / Dagster module**：补齐任务 lineage、freshness 和失败上下文。

总结：Pontis 最值得支持的不是“更多文件格式”，而是那些能贡献 **schema、lineage、semantic、usage、quality、ownership、provenance** 的数据源。
