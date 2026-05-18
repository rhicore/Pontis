# 局部数据库知识抽取改进方案

## 背景

当前系统已经能够为数据库项目提取大量底层信息：

- 表、列、类型、外键
- 实体 detail
- 局部关系边
- 一部分消歧实体

这些信息并不是没用，但从 dev 集错误来看，问题已经不主要是“信息没抽出来”，而是：

1. 抽出来的知识大多是原子级知识，缺少面向查询决策的组织层。
2. agent 在生成 SQL 时，仍然容易先按常识猜，再把局部知识当作参考。
3. 一些本库特有错误并不是单个列 detail 或单个 disambig 可以稳定解决的。

以 `california_schools` 为例，当前痛点更像是：

- `schools / satscores / frpm` 各自负责什么问题域
- `satscores.cds` 和 `schools.CDSCode` 的 join 口径
- `rtype='S'` 在 benchmark 中的局部语义
- `GSoffered / GSserved`、`School Type / SOCType` 这类高风险候选如何选择

这些内容需要比“列说明”更高一层的库级知识。

## 目标

本方案的目标不是让 extractor “提前猜出 golden SQL”，而是：

- 产出高价值、低幻觉、可验证的局部数据库知识
- 缩小 agent 的候选空间
- 降低错误 join、错误字段选择、错误表路由
- 让 agent 在没看到 golden SQL 时，也更容易理解当前库的局部结构和常见坑点

## 先说边界

### 能可靠抽取的

- 表职责摘要
- 高频 join
- key 格式差异
- 高风险近义字段对
- 地址、电话、日期、比例分子分母等字段角色
- 哪些表更像事实表，哪些更像维表
- 哪类问题通常落在哪张表或哪组表

### 只能半可靠抽取的

- 某个自然语言问法更偏向哪个字段
- 某类 rate 题常见分子分母候选
- 某个歧义词在该库里更常对应哪个列

这类知识适合作为候选约束，不适合作为硬规则。

### 很难在不看 golden SQL 的前提下稳定抽取的

- benchmark 最终偏好哪种 SQL 写法
- 是否必须 `COUNT(DISTINCT ...)`
- 某道怪题到底按 evidence 还是按数据常识走
- 某些偏 benchmark 的局部口径

这些问题更适合由：

- train 历史 SQL
- bird global
- benchmark-aware guardrail

来补，而不是要求 extractor 直接解决。

## 核心判断

当前问题不在于“再多加几个列 detail 就够了”，而在于缺一层面向查询任务的库级知识组织。

因此，下一步不应只继续堆：

- `detail`
- `disambig`
- 列级解释

而应该新增一层更贴近查询决策的知识实体。

## 建议新增的知识实体

建议在现有知识图之上补充以下库级实体类型。

### 1. `table_role`

描述一张表在该库中的职责边界。

示例：

- `table_role:schools`
  - 学校主数据
  - 常见字段：学校名、地址、电话、类型
- `table_role:satscores`
  - SAT 相关统计
  - 常见字段：考试人数、分数段、学校级记录
- `table_role:frpm`
  - 免费午餐、入学人数、比例类指标

### 2. `routing`

描述某类问题默认优先落到哪张表或哪组表。

示例：

- `routing:school_contact_and_address`
  - 问电话、地址、城市、邮编时优先走 `schools`
- `routing:sat_school_metrics`
  - 问 SAT 参与人数、高分人数、高分率时优先走 `satscores`
- `routing:meal_and_enrollment_rates`
  - 问免费午餐数、入学人数、相关 rate 时优先走 `frpm`

### 3. `join_rule`

描述高频 join、脏 join、格式风险、局部偏好。

示例：

- `join_rule:schools_satscores_cds`
  - `satscores.cds <-> schools.CDSCode`
  - 说明长度、前导零、benchmark 下不要擅自补零或修字符串

### 4. `field_disambig`

比普通 `disambig` 更具体，直接面向查询决策。

示例：

- `field_disambig:gsoffered_vs_gsserved`
- `field_disambig:school_type_vs_soctype`
- `field_disambig:county_vs_cname`

每个实体至少回答三件事：

- 这几个字段分别是什么意思
- 什么时候优先用哪个
- 常见误用是什么

### 5. `query_convention`

记录只在该库里反复出现的局部习惯，不必上升为全局知识。

示例：

- `query_convention:rtype_s_school_level`
- `query_convention:address_fields_live_in_schools`

## 不建议做的事情

### 不建议把 extractor 设计成答案预测器

错误目标：

- 让 extractor 提前猜某类题最终该写什么 SQL
- 让 extractor 学会 benchmark 的所有怪口径

原因：

- 幻觉高
- 难验证
- 容易把错误的“局部经验”固化到图里

### 不建议只靠 LLM prompt 补齐全部局部知识

有些最有价值的局部知识根本不应该靠 LLM 猜，而应该脚本化抽取。

例如：

- 高频 join 图
- 列名相似度冲突
- 值域重叠
- key 长度分布和前导零差异
- 像地址/电话/时间/比例分子分母的字段角色

## 抽取实现建议

### A. 增加规则型 extractor

适合脚本直接提取的内容：

- join 频率与 join 候选
- 列名相似度与冲突对
- key 格式差异
- 列值域 overlap
- 地址、电话、日期、计数、比例相关列角色

这部分应该优先靠自动化脚本实现，而不是交给 prompt。

### B. 修改 extractor prompt

prompt 的职责应该是组织和总结，而不是凭空猜答案。

建议让 prompt 主要输出：

- 表职责
- 高频问题路由
- 高风险 join
- 高风险近义字段
- 局部约定说明

不建议让 prompt 输出：

- “这个题大概率该怎么写”
- “golden 更偏哪种 SQL”

### C. 补充 README，但只做高层导航

README 适合承载：

- 这个库的核心表
- 各自负责的业务面
- 已确认的局部坑点
- 高频 join 说明

README 不适合承载过碎的列级规则，也不应替代图内知识实体。

## agent 侧需要同步调整的地方

即使 extractor 抽出这些新知识，如果 agent 侧不改，收益也会被削弱。

至少需要同步做两件事：

### 1. prompt 先读库级知识，再读列级 detail

优先顺序应当变成：

1. `routing / join_rule / field_disambig / table_role`
2. 相关表列 detail
3. 全局 bird knowledge

否则 agent 还是容易先按常识猜。

### 2. 把库级知识从“参考信息”提升为更强约束

例如：

- 路由知识优先于通用常识
- 高风险字段消歧命中后，应主动缩小候选
- 明确禁止 agent 在无充分证据时自行修补 join

## 推荐的最小落地方案

如果只做一轮最小实现，建议按下面顺序推进：

1. 新增 `table_role`
2. 新增 `routing`
3. 新增 `join_rule`
4. 新增 `field_disambig`
5. 修改 agent prompt，要求优先读取这些实体

第一轮不要追求覆盖所有库，优先做：

- `california_schools`
- `formula_1`
- `thrombosis_prediction`

因为这几个库最能体现“局部库知识不足”带来的错误。

## 预期收益

这套方案不能保证在没看 golden SQL 的前提下直接得到“正确答案级元信息”。

但它可以较稳定地做到：

- 降低错误表路由
- 降低错误 join
- 降低高风险字段误选
- 降低 agent 自作主张修数据

更准确地说：

- 不能提前提取“答案”
- 但可以提前提取“让错误更难发生的局部知识结构”

这正是 extractor 层更现实、也更有工程价值的目标。
