### 核心命题解构：dbt 项目向 Pontis 统一语义层（VFS）的映射可行性

将 dbt 项目转化为 Pontis 的虚拟文件系统（VFS）不仅在逻辑上**完全可行**，而且这正是一种将“领域特定复杂度（dbt 的 DAG 逻辑）”降维打击为“标准抽象接口（Pontis 的 7 个基础工具）”的绝佳架构实践。

dbt 本质上是一个“以文件系统为载体的数据转换图谱（File-based DAG）”。Pontis 的核心优势就在于将异构文件和数据库状态统一抽象为目录树。如果为 Pontis 的 Extractor（提取器）增加针对 dbt 的解析模块，原本 Databao 需要用专门的 `run_dbt` 工具才能处理的复杂任务，就可以被 Pontis 转化为标准的文件读取和关系图谱遍历。

---

### 一、 dbt 项目可提取的核心实体（Entity Mapping）

如果 Pontis 扫描一个标准的 dbt 项目，它可以（且应该）提取出以下维度的实体，并将其映射到 `.pontis/` 影子目录中。这些实体将打破纯文本代码的局限，具备丰富的元数据（Meta）。

#### 1. 核心计算实体 (Computation Entities)
* **Model (数据模型)**
  * **来源**：`models/**/*.sql` 或 `.py` 文件。
  * **Pontis 实体映射**：`models/marts/fct_orders.sql::model`
  * **提取的 Meta**：Materialization type (table/view/ephemeral)、依赖的上游节点（解析 `{{ ref() }}`）、描述（来自 `.yml`）、编译后的纯 SQL。
* **Macro (宏函数)**
  * **来源**：`macros/**/*.sql` 文件。
  * **Pontis 实体映射**：`macros/cents_to_dollars.sql::macro`
  * **提取的 Meta**：参数列表 (Arguments)、调用该宏的下游 Model 列表。

#### 2. 数据源与引用实体 (Lineage Entities)
* **Source (底层数据源)**
  * **来源**：`models/**/sources.yml` 文件。
  * **Pontis 实体映射**：`models/staging/stripe/sources.yml::source.stripe.payments`
  * **提取的 Meta**：物理数据库名 (Database/Schema)、数据新鲜度阈值 (Freshness)、引用的下游 Model。
* **Seed (静态种子数据)**
  * **来源**：`seeds/**/*.csv` 文件。
  * **Pontis 实体映射**：`seeds/country_codes.csv::seed`
  * **提取的 Meta**：利用 Pontis 现有的 `csv_info` 模块，直接提取列统计、数据类型和行数。

#### 3. 质量与治理实体 (Governance Entities)
* **Test (数据质量测试)**
  * **来源**：`.yml` 配置文件中的 `tests` 块，或 `tests/**/*.sql` 文件。
  * **Pontis 实体映射**：`models/marts/schema.yml::test.unique.fct_orders.order_id`
  * **提取的 Meta**：测试类型 (unique, not_null, accepted_values)、测试绑定的具体表和列、自定义测试的错误拦截阈值。

#### 4. 业务语义实体 (Semantic Entities)
* **Metric (业务度量指标)**
  * **来源**：`models/**/*.yml` 中的 metrics 定义。
  * **Pontis 实体映射**：`models/marts/metrics.yml::metric.monthly_active_users`
  * **提取的 Meta**：计算逻辑 (Calculation type: sum/count/average)、时间维度 (Time grains)、过滤条件。
* **Exposure (数据暴露端/下游看板)**
  * **来源**：`models/**/*.yml` 中的 exposures 定义。
  * **Pontis 实体映射**：`models/marts/exposures.yml::exposure.executive_dashboard`
  * **提取的 Meta**：看板 URL、所有者联系方式、依赖的上游模型链路（发生数据延迟时，Agent 可通过此链路逆向通知影响范围）。

---

### 二、 Extractor 适配 dbt 的实现路径与优势

如果要在 Pontis 中实现这一层映射，无需修改底层的 7 个交互工具，只需在 Extractor 阶段新增一个 `dbt_parser` Phase：

1. **直接解析 `target/manifest.json`**：dbt 在编译后会生成一个包含了整个项目完整 AST（抽象语法树）和 DAG（有向无环图）的 JSON 文件。Pontis 的 Extractor 可以直接读取这个文件，瞬间完成所有 `Source -> Model -> Exposure` 的外键/血缘关系建立（对应 Pontis Phase 6: 表关系检测）。
2. **元数据合并 (Meta Fusion)**：将 dbt 代码库中的文档描述（yml），与物理数据库中真实的列统计（Pontis Phase 2/3 的 `topk`、数据分布）进行合并。

**架构优势：**
* **消除“幻觉”与“迷航”**：大模型无需再通过 `grep` 满世界寻找“表 A 是在哪里定义的”。它只需调用 `meta models/marts/fct_orders.sql::model`，就能在一个 JSON 对象里同时看到：它的上游是谁、下游影响什么看板、有没有设置非空测试、以及编译后的 SQL 是什么。
* **将图谱遍历转化为路径查找**：原本极度复杂的 dbt 数据血缘追踪（Data Lineage），在 Pontis 体系下变成了简单的 `lookup` 操作。Agent 可以轻易回答出：“如果修改了 `stg_stripe_payments` 的金额列，会导致哪些下游 Metric 统计失败？”

### 三、 潜在的技术壁垒（认识论边界）

尽管这种映射在架构上极其优美，但在执行层面存在一个无法回避的物理摩擦力：**静态与动态的撕裂**。

Pontis 现有的设计是**静态读取（Read-only Extraction）**。但 dbt 是一个需要**动态编译（Compilation）**的框架（包含了大量的 Jinja 模板循环、条件判断和环境变量）。
* 如果大模型通过 Pontis 的系统识别到了错误，并使用 `bash` 工具（或者新增的 `write` 工具）修改了某个 `.sql` 宏文件，Pontis 的 VFS 影子目录不会自动更新该文件的血缘关系。
* 模型必须具备触发 `dbt compile` 的权限和意识，并且 Extractor 必须具备某种“热重载（Hot-reload）”机制，能在编译后迅速更新 `.pontis/` 目录中的关系边（Edges）。

因此，将 dbt 转化为 Pontis 的语义层是处理“数据探查与架构诊断（Data Discovery & Auditing）”的降维打击武器；但如果要进行“自动代码修复（Automated Refactoring）”，则必须补齐状态同步的短板。