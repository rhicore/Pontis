# 隐含多列映射关系 — 桥接路径发现缺口

## 问题

在 european_football_2 中，要查"来自比利时的球员"，需要：

```
Player ── player_api_id ──→ Match.home_player_X ── country_id ──→ Country
```

Match 表有 `home_player_1..11` 和 `away_player_1..11` 共 22 列，都引用 `Player.player_api_id`。
这是一个**一对多映射**：一个表的多列引用另一表的同一列，且 Match 表同时作为连接 Player 和 Country 的**桥接表**。

Benchmark 中 6 题（Q1119, Q1120, Q1121, Q1126, Q1127, Q1131）因此错误。

## 现状：FK 实际上已经发现了

系统中**已经存在** 22 个 FK 实体：

```
Match.home_player_1__to__Player.player_api_id.fk
Match.home_player_2__to__Player.player_api_id.fk
...
Match.away_player_11__to__Player.player_api_id.fk
```

同时也有 `Match.country_id__to__Country.id.fk`。

也就是说，所有二元关系**已经在知识图谱中**。问题不是"发现不了"，而是：

### 1. Agent 不会利用 FK 做桥接推理

Agent 的 JOIN 推理流程是：
1. 看 question 涉及哪些表（Player、Country）
2. 检查这两个表之间有没有直接 FK/rel
3. 没有 → 不知道怎么 JOIN

缺少的步骤是：**检查是否有中间表同时连接两个目标表**。这需要两跳推理：
- Player ← FK ← Match → FK → Country
- 即：找到一个表，它既有列指向 Player，又有列指向 Country

### 2. 22 个 FK 实体太分散

Agent 用 glob 查看 FK 时看到的是 22 个独立实体，没有"这 22 个 FK 指向同一个目标"的聚合语义。
Agent 需要的是一条简明的信息："Match 表通过 22 个 player 列关联 Player 表"。

### 3. Benchmark prompt 的 JOIN 规则不够

当前 SQL 规则第 6 条："JOIN 前查阅 .fk / .overlap / .rel 实体"。
但 agent 只查看了 Player 和 Country 之间的直接关系，没有去找桥接表。

## 根因总结

| 层面 | 问题 |
|---|---|
| 数据层面 | ✅ FK 关系已完整存在于图谱中 |
| 聚合层面 | ❌ 22 个 FK 是分散的，没有"桥接表"的聚合语义 |
| Prompt 层面 | ❌ 没有引导 agent 做"中间表桥接"推理 |
| Agent 行为 | ❌ agent 看到没有直接 FK 就放弃了，不会主动寻找桥接路径 |

## 解决方案

### 方案 A：静态脚本 — 生成桥接关系摘要

在提取阶段增加一步，扫描所有 FK/overlap，找到"桥接表"：

**检测逻辑**：
1. 对每个数据库，收集所有 FK 实体
2. 按表分组：对每个表，看它有哪些列指向哪些目标表
3. 如果一个表 T 的列分别指向表 A 和表 B，则 T 是 A 和 B 之间的桥接表
4. 为这种桥接关系创建一个聚合实体，或在表级 meta 中记录桥接信息

**具体到 european_football_2**：

```
Match 表的 FK 指向:
  → Player (via home_player_1..11, away_player_1..11, 共22列)
  → Team (via home_team_api_id, away_team_api_id, 共2列)
  → Country (via country_id)
  → League (via league_id)

因此 Match 是以下表对的桥接表:
  Player ↔ Country
  Player ↔ Team
  Player ↔ League
  Team ↔ Country
  ...
```

**存储方式**：在 Match.table 的 meta.detail 中追加桥接信息：

```
Match 表是以下表之间的桥接表：
- Player ↔ Country: Match 通过 home/away_player_X 列关联 Player，通过 country_id 关联 Country
- Player ↔ Team: Match 通过 home/away_player_X 关联 Player，通过 home/away_team_api_id 关联 Team
- ...
```

**优点**：agent 读 Match.table 的 meta 时就能看到桥接信息
**缺点**：需要新增提取步骤；桥接信息需要动态生成，不是通用的

### 方案 B：Prompt 层面 — 引导 agent 做桥接推理

在 SQL 规则或 benchmark prompt 中加入：

```
当你需要 JOIN 两个没有直接关系的表时：
1. 先检查是否有中间表同时与两个目标表有 FK 关系
2. 用 glob 查看 *.fk 实体，找同时连接两个表的表
3. 例如要连接 Player 和 Country，检查哪个表既有指向 Player 的 FK，又有指向 Country 的 FK
```

**优点**：不需要新脚本，利用现有 FK 数据
**缺点**：依赖模型的推理能力，不一定稳定

### 方案 C：组合 — 桥接信息写入 meta + prompt 引导

1. 静态脚本生成桥接摘要，写入桥接表的 meta.detail
2. Prompt 中引导 agent 读 meta.detail 关注桥接信息

**推荐**：方案 A 或 C。因为方案 B 依赖模型主动做两跳推理，在 low effort (30 轮) 下很难稳定完成。

## 实现参考 — 静态脚本伪代码

```python
def discover_bridge_tables(store, db_ref: str):
    """找出数据库中的桥接表，写入 meta。"""

    # 1. 收集所有 FK，按源表分组
    fks = store.find_nodes(f"{db_ref}::*.*.fk")
    table_targets = defaultdict(set)  # {source_table: set(target_table)}

    for fk_ref in fks:
        # 解析: Match.home_player_1__to__Player.player_api_id.fk
        fk_name = fk_ref.split("::")[-1]
        source_col = fk_name.split("__to__")[0]  # Match.home_player_1
        target_col = fk_name.split("__to__")[1].replace(".fk", "")  # Player.player_api_id

        source_table = source_col.split(".")[0]  # Match
        target_table = target_col.split(".")[0]  # Player

        table_targets[source_table].add(target_table)

    # 2. 找桥接表：一个表指向 >= 2 个不同的目标表
    bridges = {}
    for table, targets in table_targets.items():
        if len(targets) >= 2:
            # 这个表是任意两个目标表之间的桥接
            for t1 in targets:
                for t2 in targets:
                    if t1 != t2:
                        bridges.setdefault(table, []).append((t1, t2))

    # 3. 生成桥接摘要，写入表级 meta.detail
    for table, pairs in bridges.items():
        # 读取当前 detail
        table_ref = f"{db_ref}::{table}.table"
        current_detail = store.get_meta(table_ref).get("detail", "")

        bridge_lines = ["\n\n**桥接关系**：该表是以下表之间的连接桥梁："]
        for t1, t2 in pairs:
            # 找具体的连接列
            t1_cols = [fk for fk in fks if f".{t1.lower()}." in fk.lower()]
            t2_cols = [fk for fk in fks if f".{t2.lower()}." in fk.lower()]
            bridge_lines.append(f"- {t1} ↔ {t2}")

        new_detail = current_detail + "\n".join(bridge_lines)
        # 更新 meta
        # store.update_meta(table_ref, {"detail": new_detail})

    return bridges
```

## 影响评估

修复桥接路径发现后，预计修复 european_football_2 的 6 题（Q1119, Q1120, Q1121, Q1126, Q1127, Q1131），准确率从 91/129 (70.5%) 提升到约 97/129 (75.2%)。

这个模式在其他数据库中也可能出现——任何有"关联表"（junction table）的 schema 都会遇到同样问题。
