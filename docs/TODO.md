







- harness进化
    - https://github.com/keli-wen/agentic-harness-patterns-skill

- 有待处理的格式
    - 开放表格格式 (OTF),"_delta_log/ (目录), metadata.json, .avro (Manifest)",表级元数据：ACID 版本、时间旅行快照、Schema 演变历史、数据分区键。
    - 列式分析文件,".parquet, .orc, .lance (AI 优化)",列级元数据：每列的 Min/Max 值、空值率、数据压缩比、向量维度（如果是 Lance）。
    - 行式/交换格式,".avro, .json, .jsonl, .csv, .tsv",结构元数据：字段偏移量、Schema 定义（Avro 必备）、编码格式。
    - 图像数据：JPG, PNG, WebP。
    - 对象存储：AWS S3, OSS。
    - SaaS 连接器：Notion Pages, Slack Channels, Jira Tickets。
    .proj,"dbt, Git Repo, Data Project","项目级操作（build, run, test）。"
    中心思想：文件名后缀的部分负责确定元数据schema，比如说INT.col和STR.col的元数据schema不一样

- AI enricher
    - 列的数据分布归纳
    - 人工注释
    - 语义层定义为一组易于解释且可重复使用的数据库视图（Views）
        - Towards Agentic Schema Refinement
    -  基于模式的表分组
        - ReFoRCE: A Text-to-SQL Agent with Self-Refinement, Consensus Enforcement, and Column Exploration
        - **案例**：在 GA360 数据库中，存在按日期命名的表（如 `GA_SESSIONS_20160801` 到 `GA_SESSIONS_20170801`），每张表的 DDL 文件超过 150KB 。通过这种压缩方式，DDL 总大小从超过 50MB 降至不足 2MB，在不丢失核心信息的情况下实现了 **96% 以上的压缩率** 。

    
- 潜在问题
    - 语义准确性：Enrichers 依赖 LLM 生成描述。如果数据采样（Sample size 100）不具备代表性，LLM 可能会对列名产生误解，生成错误的 brief 描述，从而误导 Agent 的后续决策。
    - 数据泄露风险：启用 LLM 富化意味着部分数据样本会被发送至第三方 API 供应方。在处理敏感行业数据时，这种设计需要严格的脱敏过滤机制。
    - 有些列名表名带有斜杠，会导致分析错误
    /nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)/.pontis/dev_databases/california_schools/california_schools.sqlite/frpm/Charter School (Y/N)




