现在extractor模块已经将数据库转化成了文件树状结构

你现在要写的就是读取文件树状结构，提供给大模型用的命令接口，本章优先关注ls命令






3. ls 输出规范（四字段模型）
无论进入哪种目录，ls 的输出必须严格对齐以下四个字段：
[HasSub] | [Name] | [Info] | [Brief]

字段填充逻辑：
[HasSub]: 存在子节点（如目录、列表、字典）显示 [+]，叶子节点显示 [ ]。

[Name]: 节点名称。注意： 必须包含完整的后缀名以标识类型（如 users.table, id.INT.col, data.json）。

[Info]: 类型相关的统计摘要。

.db: 显示包含的表/视图数量（如 3 tables, 1 view）。

.table: 显示行数和列数（如 1000 rows, 5 cols）。

.col: 显示唯一值计数或分布（如 Dist: 100）。

.chunk: 显示字符数或 Token 数（如 500 chars）。

序列化文件 (JSON/YAML等): 遵循之前的逻辑（如 5 pairs 或 12 items）。

[Brief]: 从该节点关联的元数据文件中读取 AI 生成的简短描述。如果没有，则留空，显示一个横杠-就行

4. 节点分发逻辑 (The Dispatcher)
实现一个 NodeFactory，根据文件后缀名自动分发到对应的处理器：

目录（无后缀）: 处理为 RawDataNode。

数据库组件: .db, .table, .col, .view, .fk, .rel, .flow。

文档组件: .md, .txt, .pdf 映射为容器，内部为 .chunk。

序列化组件: .json, .yaml, .xml, .toml, .hcl 映射为虚拟目录，内部递归应用 ROOT 逻辑。

5. 开发要求
解耦渲染与数据获取：ls 命令应调用 VFSNode 接口获取数据，然后使用 Tabulate 或类似的库渲染表格，确保 glob 引擎可以只调用 list_children 而不触发表格渲染。

路径转换：支持将虚拟路径转换为 .pontis 影子目录中的物理 YAML/二进制文件路径。

分页保护：在 list_children 中强制实现 offset 和 limit 参数。

复用空间：代码结构应允许 grep 模块通过递归调用 list_children 并执行 get_content().contains(pattern) 来轻松实现。

示例输入/输出参考：
输入: ls .pontis/dev.db/
输出:

Plaintext
[HasSub] | [Name]           | [Info]             | [Brief]
---------|------------------|--------------------|----------------------------
[+]      | users.table      | 10,000 rows, 8 cols| 用户核心基本信息表
[+]      | orders.table     | 50,000 rows, 4 cols| 2025年度所有交易流水记录
[ ]      | active_v.view    | 2 sources          | 活跃用户筛选视图
[ ]      | user_fk.fk       | users -> auth      | 物理外键关联
请先生成核心 BaseNode 类和 NodeFactory 的代码结构。