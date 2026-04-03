# 各种类型数据的基本信息提取
- 数据库
    - 表格
        - 提取 DDL
    - 列
- CSV、TSV：基本都没完成
- md、txt：长文本的分割
    - 正则锚点？
- 序列化格式：JSON, YAML, XML, TOML, HCL
- PDF
- 图像数据：JPG, PNG, WebP。
- 对象存储：AWS S3, OSS。
- SaaS 连接器：Notion Pages, Slack Channels, Jira Tickets。

提取重点：最后活跃时间、参与人列表、任务状态。

# 将所有的类型表征放到文件名后缀？
- 列 INT.col

中心思想：文件名后缀的部分负责确定元数据schema，比如说INT.col和STR.col的元数据schema不一样


# ls列，就是显示这个列的所有distinct值






# 针对各种类型的ai summary
- [] 



- 针对summary的语义检索



- 一个可快速演示的命令行
- 完成所有命令
- ls
    path: 目标路径。
    --offset: 偏移量（起始位置），默认 0。
    --limit: 单词返回的最大数量，建议默认 100（超过则截断）。
    -R: 递归模式（注意：递归时也应有全局 Limit 保护）。
    -a: 显示内部隐藏元数据节点（如 .pontis/ 内部文件）。
- meta
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



- ls 的长度限制，不加 -a 就是100个，加-a就是全部

meta的信息显示
一部分信息是默认隐藏的比如修改时间



# 路径转换器
所有Pontis Agent推理都是用虚拟文件路径表达，在返还给基础大模型和工具调用的时候要转换回物理路径


# enricher
- join列发现器
- 列的数据分布归纳
- 人工注释