# Pontis 重构计划

## 目标

这份文档用于统一当前代码库的重构方向，覆盖以下几个方面：

1. prompt 与图谱数据模型的对应关系
2. storage / cypher 的边界与实现
3. extractor / explorer 的职责划分
4. guardrail 的必要性与收口方向
5. 当前已知 bug 与设计缝
6. 一个可执行的分阶段重构顺序

本文整合了之前关于 `storage` 边界收紧的讨论，但范围不再只限于 storage。

---

## 一、当前架构判断

当前系统已经形成了一个清晰方向：

- `storage` 负责图谱持久化与图查询
- `tool` 负责 agent-facing 的操作面
- `extractor` 负责程序化抽取
- `explorer` 负责 agent-driven 的后处理与知识完善
- `bird` 作为跨库经验库 project

这个方向本身是成立的。

但当前代码库存在一个核心问题：

**目标架构已经明确，实际实现仍停留在过渡态。**

最明显的表现是：

- 对外宣称图操作应统一走 `cypher`
- 但 `storage` / `Workspace` 边界本身仍未完全收口
- prompt 已经开始推广路径 ref / README / `bird` 经验库
- 但图谱命名、tool 写接口、guardrail、explorer 还没有完全对齐

所以当前最重要的事情不是继续堆新功能，而是把边界和契约真正收紧。

---

## 二、Prompt 与图谱模型

### 2.1 当前优点

目前 prompt 体系已经比之前清楚得多：

- `bird::README` 承接经验性规则
- benchmark 脚本 prompt 只保留运行协议
- reflection prompt 已经明确只允许写 `bird::<name>:knowledge:<type>`
- guardrail 提示词开始按启用 guardrail 动态拼接

这是正确方向。

### 2.2 当前问题

#### 问题 A：模型仍需要同时记住多套引用心智

当前系统里至少同时存在这些表达：

- 裸名：`paper`
- 路径式 ref：`citeseer.sqlite/paper/paper_id`
- 点号式关系名：`paper.paper_id->cites.citing_paper_id`
- project 前缀：`bird::README`

这些表达并非都错误，但如果同时被当作“主要工作面”，模型负担会显著增加。

#### 问题 B：ontology / tool / sql prompt 之间还有轻度重叠

- `ontology` 应只负责“图谱中有什么”
- `tool` 应只负责“工具职责和纪律”
- `sql` 应只负责“SQL 任务决策流程”

当前这三层虽然已经比之前干净，但仍有边界上的拖尾。

#### 问题 C：prompt 里仍有“命名逻辑”和“agent-facing ref”混在一起的地方

例如：

- 图中某些实体内部叫 `table.col->table.col`
- 但 agent-facing 读写又更鼓励路径 ref

这会让 agent 在“看到什么”和“该怎么写”之间来回切换。

### 2.3 目标原则

应把 prompt 层收成三条原则：

1. **只教 agent 一套主要可操作引用体系**
   - 数据项目主引用：路径 ref
   - 知识项目主引用：`project::ref`

2. **实体内部命名不等于 agent 工作面**
   - 图里允许存在点号式 `fk/rel/overlap` 名称
   - 但 prompt 不应把这些内部命名当成 agent 的主操作语法

3. **经验性规则尽量收进 `bird::README`**
   - 系统 prompt 不重复展开
   - benchmark / reflection 只要求先读 `bird::README`

### 2.4 重构建议

- `ontology` 只保留：
  - 标签类型
  - 邻接关系
  - 命名逻辑
  - 项目级类型约束

- `tool` 只保留：
  - 工具职责
  - 使用纪律
  - 不含软性 schema 解释

- `sql` 只保留：
  - SQL 任务流程
  - JOIN / disambig / query 的任务级约束

- `bird::README` 承接：
  - 常见错误
  - 审题工作流
  - BIRD 跨库经验

---

## 三、Storage / Cypher 边界

## 3.1 目标边界

`storage` 应收紧为**纯图谱层**。

最终目标：

- `Workspace` 对外只保留图谱查询与图谱项目路由
- 所有图读写统一走 `workspace.cypher(...)`
- `Store` 彻底退回内部实现
- 数据源访问不再由 `storage` 暴露

### 应保留

- `workspace.cypher(query, params=None, project=None)`
- `workspace.active_projects`
- 必要的 project/config 路由

### 仍需移除

- `workspace.open_db(...)`
- `workspace.open_file(...)`
- `workspace.resolve_data_path(...)`
- `workspace.data_exists(...)`

### 仍应禁止外部直接使用

- `workspace._get_store(...)`
- `store._get_meta(...)`
- `store._set_meta(...)`
- `store._create_node(...)`
- `store._add_edges(...)`
- `store._delete_node(...)`
- `store._adjacent`

## 3.2 当前主要问题

### 问题 A：对外仍暴露 source-specific 访问口子

`Workspace` 现在仍然代理：

- `data_exists`
- `resolve_data_path`
- `open_db`
- `open_file`

这使 `storage` 既像图数据库，又像数据源适配层。

### 问题 B：tool 主流程已基本迁走，但仍有展示/兼容层残留

当前核心 tool 主流程（`meta / glob / create_entity / update_meta / add_edge / delete / resolve`）已经迁到 `workspace.cypher(...)`。  

剩余问题主要是：

- `Workspace` 仍暴露 source-specific IO 代理
- 其他层（例如 extractor / guardrail / helper）仍可能保留私有接口依赖
- `storage` 自身还没有真正收口为“只提供图谱 API”

### 问题 C：Cypher / storage 内部主键语义仍未完全收口

当前内部有三套主键概念：

- Store 内部 `_id`
- Cypher 内部 `_eid`

写工具侧的错误匹配已经修掉，但这套内部主键语义本身仍未清晰定型。

## 3.3 已完成修复

以下问题已完成修复，可不再作为后续计划项：

1. `add_edge` 错误依赖公开 `id` 字段
2. `update_meta` 错误依赖公开 `id` 字段
3. `create_entity` 仅按 `name` 判重，导致 `rel/overlap` 冲突
4. tool 主流程直接依赖 `Store` 私有读写原语

## 3.4 重构原则

### 原则 1：图读写统一走 Cypher

以下操作全部统一到 `workspace.cypher(...)`：

- 查节点
- 查邻居
- 更新属性
- 创建节点
- 创建边
- 删除节点

### 原则 2：Cypher 不足时优先补 Cypher

如果某类写操作不方便表达，不要回退去调用 `Store._xxx`，而应增强 Cypher。

### 原则 3：source-specific IO 从 storage 拆出

数据库读取、文件读取、路径解析，迁到：

- extractor 自己的 helper
- 或 tool 自己的 helper

而不是继续挂在 `Workspace` 上。

---

## 四、Extractor / Explorer 边界

## 4.1 当前方向

你现在把：

- `extractor`
- `explorer`

分开，这个方向是对的。

应继续坚持：

- `extractor`：程序化抽取
- `explorer`：agent-driven 补充探索

## 4.2 当前问题

### 问题 A：全量提取现在承担了太多事情

当前 full extract 默认包含：

- 静态抽取
- AI 列/表/库摘要
- `agent_analyze`
- `agent_join_detect`
- `agent_disambiguate`
- `agent_readme`

这已经不是简单 extraction，而是一整条知识构建流水线。

问题：

- 很慢
- 非确定性高
- benchmark 前置成本高
- 难以比较实验

### 问题 B：pipeline 现在还是按“功能堆叠”组织，不是按阶段组织

更好的分层应是：

1. graph build
2. semantic summary
3. relation enrichment
4. project documentation

而不是简单分成 static / ai / agent。

### 问题 C：README 虽然已经回归 explorer，但仍缺更清晰的阶段语义

README 是 explorer 产物，这条已经是对的。  
但还应明确：

- README 依赖哪些先验产物
- 它是否总是最后一步
- force extract 是否一定重建 README

当前答案实际上是“应当是”，但代码层契约还不够显式。

## 4.3 目标原则

### extractor

- 只负责确定性或准确定性的程序化构图
- 原始数据访问自己处理
- 图写回统一走 cypher

### explorer

- 基于已有图谱做 agent-driven enrich
- 包括：
  - analyze
  - join_detect
  - disambiguate
  - readme

### pipeline

应明确支持：

- 纯构图
- 构图 + AI 摘要
- 构图 + explorer enrich

而不是把全部步骤默认绑死。

---

## 五、Guardrail 的角色

## 5.1 当前判断

guardrail 目前是必要的。

尤其这些：

- `readme_check`
- `sql_check`
- `bridge_check`
- `disambig_check`

它们不是可有可无的装饰，而是在当前 prompt 和图谱契约还没完全收口前，防止 agent 直接走偏的最后防线。

## 5.2 当前问题

### 问题 A：guardrail 正在替架构缺口兜底

例如：

- README 访问顺序
- SQL 输出前必须确认实体
- JOIN 路径必须确认

这些理想情况下应更多由：

- 清晰 prompt
- 一致图谱结构
- 工具契约

来保证，而不是全压给 guardrail。

### 问题 B：README check 已经接近“项目协议调度器”

`READMEReadCheck` 现在不仅在检查“有没有先读”，还在替 benchmark 强制 `bird` 先读。

这是有效的，但说明 `bird` 作为共享知识库入口还没真正成为 agent 的自然工作流。

## 5.3 目标原则

guardrail 应保留，但应逐步变薄：

- 它负责拦明显错误
- 不负责承载主业务工作流

长期方向：

- `readme_check` 继续保留
- `sql_check` / `disambig_check` 保留
- `bridge_check` 视图谱稳定性决定是否继续保留
- `query_abuse` 保持轻量，不要过度调度

---

## 六、当前已知 bug / 设计缝

### P1：短期应修

1. `Workspace` 仍暴露 source-specific IO 代理
2. prompt 仍在教授多套并行引用心智
3. full extract 默认包含过重 explorer 阶段

### P2：中期优化

4. `common.py / run_bird_benchmark.py / extract.py` 仍偏胖
5. benchmark / reflection 的 case 结果还不够结构化
6. `bird` 经验库的写入门槛还可继续收紧

---

## 七、推荐重构顺序

## 阶段 1：统一 agent-facing ref

目标：

- prompt 只承认一套主工作面引用体系

建议：

- 数据项目：路径 ref
- 知识项目：`project::ref`
- 点号式 `table.col->table.col` 仅作为关系实体名解释，不作为主操作语法

## 阶段 2：拆出 source-specific IO

目标：

- extractor / tool 自己处理数据库和文件读取
- 不再依赖 `Workspace.open_db/open_file/...`

建议新增：

- `extractor/modules/utils/sqlite.py`
- `extractor/modules/utils/files.py`

## 阶段 3：重构 extraction pipeline

目标：

- 不再把 explorer enrich 与最小构图绑定成一个默认入口

建议拆成：

1. static graph build
2. ai summary
3. explorer enrich

并允许 benchmark 默认只依赖前两者，或只依赖已存在图谱。

## 阶段 4：减薄 guardrail

在前面几步完成后，再判断哪些 guardrail 仍然必要。

---

## 八、验收标准

重构完成后，应满足以下标准：

### Prompt

- agent 只需要记住一套主引用心智
- 经验性规则统一收进 `bird::README`
- 系统 prompt 不再重复大段经验性说明

### Storage

- `Workspace` 对图层只保留 `cypher`
- `Store` 私有接口不再被外层直接调用
- `open_db/open_file/resolve_data_path/data_exists` 不再是公开工作流依赖

### Extractor / Explorer

- README 是 explorer 模块的一部分
- 全量提取与 explorer enrich 的关系清楚
- benchmark 不再被迫每次全量跑 explorer

### Guardrail

- 能继续拦明显错误
- 但不再承担大量本应由模型契约解决的工作流调度

### Bug

- 写工具不再出现“显示成功但图未更新”的情况
- `rel` / `overlap` 命名冲突得到清晰解决
- README / reflection / bird knowledge 的写入规则稳定

---

## 九、结论

当前代码库最值得做的不是继续铺新能力，而是把已有方向彻底收紧：

- 图谱接口只留一套
- agent 工作面只教一套
- extraction / explorer / benchmark 的职责真正拆开
- guardrail 从“主流程支架”退回“错误保护层”

如果这个顺序做对，后面无论你继续做 benchmark 反思、bird 经验库积累，还是更激进的 agent loop / fork 机制，都会简单很多。
