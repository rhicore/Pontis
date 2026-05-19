

写入范围：
- 只允许写 `convention`、`pattern`、`lesson`、`example`
- 新建知识时使用 `bird::<short_name>:knowledge:<type>`
- 若现有知识只是需要补充、修正或增加证据，优先更新，不要重复创建


禁止写入：
- 术语词典、字段释义、行业名词解释
- 仅适用于单个数据库的局部事实
- 一次题目偶然做对或做错但没有可迁移性的经验

## bird 知识的读取方式

1. 在输出最终 SQL 之前，至少浏览一次 `bird` 里的知识实体总表，看看有没有相关经验。
2. 推荐先用 `glob("bird::*:knowledge")`，但把它当索引页，不要靠翻很多页硬扫。
3. 如果总表候选很多，立即用 `search(ref="bird::*:knowledge", query="...")` 缩到 1-3 个最相关实体，再用 `meta` 深读。
4. 搜索词优先用题目里的核心名词、evidence 里的公式词、以及你怀疑的错误模式词。

### 抽象知识优先

优先读取以下抽象知识实体：
- `knowledge:convention`：规则 / 约定
- `knowledge:pattern`：通用解法模式
- `knowledge:lesson`：反面教训
- `knowledge:term`：术语或概念说明

`knowledge:example` 放在后面；只有当上述抽象知识仍不足以支持判断时，才把 example 当作解释型案例阅读。

如果先看到某个 example，也要回头优先查看它相连的抽象知识，再决定是否参考这个案例。

## Reflection 写入规则

1. 先查 `bird` 里最相关的已有知识，优先看抽象知识实体；不要一上来就新建。
2. 默认策略是：优先 `update`，谨慎 `create`。
3. 如果已有相似知识：
   - 内容相同：跳过，不重复创建
   - 内容互补：用 `update_meta` 补充 detail，并增加支持证据
   - 内容矛盾：只有在你能明确指出旧知识为何不成立时，才覆盖修正
4. 只有在确认没有合适的已有实体时，才 `create_entity`。
5. 如果某个已有实体只差补一句边界、补一个反例或补一个支持证据，就应该修改它，而不是再造一个新实体。
6. 如果已有知识的 `brief/detail` 是空、`-`、`...` 之类占位符，优先把它们改写成真实可读内容，而不是新增平行实体。
7. 如果最后没有足够强、足够硬的跨库经验，允许本轮什么都不写。

### Example 的要求

1. `knowledge:example` 不能孤立存在。
2. 只要保留或新建 example，就必须把它与对应的抽象知识实体连起来。
3. 这里的“对应抽象知识实体”指：`knowledge:convention`、`knowledge:pattern`、`knowledge:lesson`、`knowledge:term`。
4. 如果对应抽象知识还没有，就先补抽象知识，再连边。
5. `example` 的 `brief` 先写可迁移结论，再写题号 / 库名等案例信息；不要把 brief 写成原题复述。
6. `example` 的 `detail` 先给 `transfer_hint`、`mistake_summary`、`why_this_case_matters` 这类抽象内容，再附 question / evidence / golden_sql 等案例证据。