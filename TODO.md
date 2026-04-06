# 准则和指令

所有的元数据yml不包含嵌套的字典、列表
如果出现这种情况，都要转变成文件系统下的多文件
保证元属性绝对的扁平化，即每个文件的每个属性都是单一键值对

所有带有可变数量子对象的内容都应该写成文件夹形式

每个文件夹下面都有一个_meta文件夹，对于序列化类型（json，sample，topK）这些，也要写成文件夹，不过文件夹下面可能存储一个_bin，用于在没有指向路径的时候直接读取

文件夹后缀名也就是实体类型，决定了文件夹带有的__meta.yml模板

chunk分块需要后续的enricher，先不用生成

序列化文件读取逻辑主要体现在tool use里面，extractor部分先不用改

[关键！]extactors的数据流向: 原本的extactor文件夹把不同模块分成两个大部分，但这个是没有必要的，因为不同模块之间的数据是有复杂的相互依赖关系的，最终可能会形成一个DAG数据依赖流向用于处理增量更新（但这部分我后续再增加）但是现在你的任务是尽量把所有模块解耦，比如说可以简要分为这三个类别的模块
第一层：骨架同步（Skeleton Sync）把数据库的 Table/Col 变成文件夹，这部分仅识别文件类型，添加文件后缀，生成最基本的每种文件都有的元数据信息
第二层：局部富化（Local Enrichment）依照后缀名类别给单节点的_meta生成统计信息、采样、Top-K等特性：节点内隔离，可并行。处理 Table_A 时不需要知道 Table_B。
第三层：全局语义（Global Semantic），比较对应之前的enricher

这个地方*并不是必须把这三种模块组织到三个文件夹里面* ，而尽量解耦和数据流入流出清晰化，要明确表明每个模块读取时模式匹配了哪些节点，写入了哪些信息，用于后续设计增量更新。


# 数据类型和VFS
```
.pontis/
├── raw_data/ # 不带后缀是指目录，可以包含实际文件存储中目录包含的类型
├── [数据库名].db/                  # 数据库目录 (.db)
│   ├── [表名].table/                 # 表目录 (.table)
│   │   ├── [列名].[数据类型].col                # 例如 money.INT.col
│   │   │   ├── .sample               
│   │   │   ├── .topK               # 
│   │   ├── [列名]__to__[表名].[列名].fk   # 物理外键
│   ├── [视图名].view             # 视图文件
│   │   ├── [列名]__to__[表名].[列名].flow   # 血缘关系
│   ├── [表名].[列名]__to__[表名].[列名].rel   # 逻辑关系或AI推断的逻辑关系
│   
├── [文档名].md/.txt/.pdf                       # Markdown/文本长文档
│   ├── section_1.chunk               # 文本分片
│   └── section_2.chunk
│
├── [表名].csv/tsv                       # csv.tsv 表格文件
│   ├── id.INT.col                # 列文件：[名称].[类型].col
│   ├── amount.CHAR.col
│
├── [文件名].json/.yaml/.xml/.toml/.hcl                  # 序列化格式
│   └── 进入序列化文件的VFS逻辑
```
## sample,topK,json,yaml等序列化文件的VFS逻辑

这些类型的文件元数据中要有一个source，指向系统中存储该文件的位置，然后如果ls该文件则是直接打开该文件

对于sample这种需要缓存的，则在.sample文件夹下面生成一个_bin文件，直接读取

当ls进入到json等序列化文件时则进入到一个不同的逻辑，显示以下几个字段
`[HasSub] | [Key] |  [Info]`
- **对于容器 (DICT/LIST)**：展示内部元素的数量（如 `5 pairs` 或 `12 items`）。
- **对于数值 (STR/INT/BOOL)**：直接展示其真实取值（若字符串过长则进行截断）。
- **对于空值 (NULL)**：直接显示 `null`。
[HasSub] | [name]              | [Info]
---------|---------------------|------------------
[+]      | metadata.DICT       | 5 pairs
[+]      | users.LIST          | 120 items
[ ]      | version.STR         | "1.2.4-stable"
[ ]      | is_active.BOOL      | true
[ ]      | timeout.INT         | 3000
[ ]      | legacy_config.NULL  | null
  

如果一个Key 对应的值是一个巨大的文本块（比如 1000 字的简介），在 `ls` 的 Value 处进行强制截断（例如前 20 字符）
为了防止json key名里空格符号等影响执行，利用URL 编码 (URL Encoding)


## View和数据血缘

ls active_users.view/ 的展示逻辑当你进入一个视图目录，除了看到它的“列”，还能看到一个特殊的虚拟目录或链接：
[HasSub][name][Info]
[+]_sources/LINK: 2 tables
[ ]user_id.INTCOL: Dist: 500
[ ]login_count.INTCOL: Dist: 50

ls active_users.view/_sources/ 的返回






## 有待处理的格式
- 开放表格格式 (OTF),"_delta_log/ (目录), metadata.json, .avro (Manifest)",表级元数据：ACID 版本、时间旅行快照、Schema 演变历史、数据分区键。
- 列式分析文件,".parquet, .orc, .lance (AI 优化)",列级元数据：每列的 Min/Max 值、空值率、数据压缩比、向量维度（如果是 Lance）。
- 行式/交换格式,".avro, .json, .jsonl, .csv, .tsv",结构元数据：字段偏移量、Schema 定义（Avro 必备）、编码格式。
- 图像数据：JPG, PNG, WebP。
- 对象存储：AWS S3, OSS。
- SaaS 连接器：Notion Pages, Slack Channels, Jira Tickets。
.proj,"dbt, Git Repo, Data Project","项目级操作（build, run, test）。"
中心思想：文件名后缀的部分负责确定元数据schema，比如说INT.col和STR.col的元数据schema不一样


ls列，就是显示这个列的所有distinct值



# 不同类型数据元数据

## 基本元数据提取


提取重点：最后活跃时间、参与人列表、任务状态。







## AI enricher
- join列发现器
- 列的数据分布归纳
- 人工注释



# 工具调用


所有按照文件名列出的命令比如ls，glob，find，search
应该按照这个展示逻辑,有以下几个字段
- 是否有子目录
- 路径/名称 # 包含后缀名，所有类型表征都用后缀名，所以没有type字段
- stats: 统计信息，表: 1000 rows, 5 cols- DB: 3 tables, 0 views- 列: Distinct: 100- 目录: 5 children
- brief: ai生成的简短描述








- 一个可快速演示的命令行
- ls
    path: 目标路径。
    --offset: 偏移量（起始位置），默认 0。
    --limit: 单词返回的最大数量，建议默认 100（超过则截断）。
    -R: 递归模式（注意：递归时也应有全局 Limit 保护）。
    -a: 显示内部隐藏元数据节点（如 .pontis/ 内部文件）。
- meta
    - a 显示所有元信息，默认meta一个目标只会展示特定一些重要的元信息，相对不重要的就不展示
    path: 目标节点。
    --fields: （核心新增） 允许 Agent 仅读取特定字段（如 joins 或 samples）。
- Grep
  │     参数     │  类型   │        默认值        │                       说明                       │
  │ pattern      │ string  │ 必需                 │ 正则表达式搜索模式                               │
  │ path         │ string  │ 当前工作目录         │ 搜索的文件或目录路径                             │
  │ output_mode  │ enum    │ "files_with_matches" │ 输出模式："content"/"files_with_matches"/"count" │
  │ glob         │ string  │ -                    │ 文件过滤模式，如 "*.js"、"**/*.tsx"              │
  │ type         │ string  │ -                    │ 文件类型，如 "js"、"py"、"rust"（rg --type）     │
  │ -i           │ boolean │ false                │ 忽略大小写（case insensitive）                   │
  │ -n           │ boolean │ true                 │ 显示行号（仅 content 模式）                      │
  │ -C / context │ number  │ -                    │ 显示匹配前后 N 行                                │
  │ -B           │ number  │ -                    │ 显示匹配前 N 行                                  │
  │ -A           │ number  │ -                    │ 显示匹配后 N 行                                  │
  │ multiline    │ boolean │ false                │ 多行模式（. 匹配换行符）                         │
  │ head_limit   │ number  │ 250                  │ 限制返回结果数量（0=无限制）                     │
  │ offset       │ number  │ 0                    │ 跳过前 N 个结果                                  │
- Glob
    仅负责文件名匹配
  │ pattern │ string │ 必需         │ Glob 匹配模式，如 "**/*.js" │
  │ path    │ string │ 当前工作目录 │ 搜索的目录                  │
- find
    用于复杂的元数据匹配
    有待商榷
- search 语义检索
    仅用于针对brief和detail的关键词和语义检索
- read

结果过长时的截断
- tool use层面截断，让大模型利用 ls --offset主动翻页
- [Output truncated... total 5000 files]
- Use --offset to see more

- 自动触发更新（Trigger）、增量同步机制(pontis sync)



# 潜在问题
- 语义准确性：Enrichers 依赖 LLM 生成描述。如果数据采样（Sample size 100）不具备代表性，LLM 可能会对列名产生误解，生成错误的 brief 描述，从而误导 Agent 的后续决策。

- 数据泄露风险：启用 LLM 富化意味着部分数据样本会被发送至第三方 API 供应方。在处理敏感行业数据时，这种设计需要严格的脱敏过滤机制。


- 有些列名表名带有斜杠，会导致分析错误
/nfsdat2/home/bcchenslm/Projects/Pontis/example_data/bird(new we don't use)/.pontis/dev_databases/california_schools/california_schools.sqlite/frpm/Charter School (Y/N)




# 路径转换器
所有Pontis Agent推理都是用虚拟文件路径表达，在返还给基础大模型和工具调用的时候要转换回物理路径


# prompt
你是一个资深 **数据架构师（Data Architect Agent）**。你的任务是通过探索一个“多模态知识索引层”来理解复杂的数据库和文件结构，并基于此完成数据查询、分析或项目重构任务。
  

你面对的不是原始的二进制文件，而是一个**树状虚拟文件系统 (Virtual File System)**，它将物理数据抽象为富含语义的元数据节点。


这个系统将原本需要特殊工具调用才能操作的数据文件（如DB，Markdown，CSV，Json等等）统一转换成操作系统下的文件操作命令就能探查的树状结构，请利用你的文件系统操作知识，完成数据分析任务。


# 数据去重逻辑

记录已经给大模型提供过哪些表结构了，如果

  

**案例**：在 GA360 数据库中，存在按日期命名的表（如 `GA_SESSIONS_20160801` 到 `GA_SESSIONS_20170801`），每张表的 DDL 文件超过 150KB 。通过这种压缩方式，DDL 总大小从超过 50MB 降至不足 2MB，在不丢失核心信息的情况下实现了 **96% 以上的压缩率** 。



# 每种数据类型显示的Info
- json/xml/md等序列化或文本文件
    - 文本行数
- DICT 
    - 5 pairs
- ARRAY
    - xx items
- STR
    - 直接展示字符串，如果超出某个长度就显示字符串长度
- BOOL
    - 直接展示 true false
- INT
    - 直接展示整数值，如果超出就隐藏
- NULL
    - 不用展示

- col
    - INT.col
        
    - ...

- table
    - 展示行数和列数
- view
    - 展示列数

- db
    - 展示多少张表

- sample/topk
    - 采样/取top数量