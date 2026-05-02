# 知识层寻址方案对比分析

## 背景

当前 Pontis 知识图谱的所有实体都以文件为锚点，通过 `::` 分隔符寻址：

```
formula_1.db::drivers.table        ← 文件 :: 实体
formula_1.db::drivers.points.REAL.col
```

现在需要引入**跨文件的知识实体**（convention、few_shot、pattern 等），这些知识没有自然的文件锚点。
核心问题：**知识实体应该怎么寻址？**

---

## 现状：`::` 的深度绑定

`::` 不是表面的显示约定，而是贯穿全系统的寻址原语：

| 层 | 使用位置 | 作用 |
|---|---|---|
| **Store** | `resolve_ref()` / `_ref_from_parts()` | 拆分 path + entity_name |
| **Store** | `find_nodes()` | 第一段匹配文件，后续段沿边遍历 |
| **Store** | `_build_index()` | 判断节点类型（文件 vs 实体） |
| **Storage** | `_meta.yml` 的 `_entity_name` 字段 | 存储 `::` 右侧部分 |
| **Tool** | glob / meta / read / grep / search | 所有 ref 参数都依赖 `::` |
| **Tool** | `path_parser.py` | 整个文件基于 `::` 拆分 |
| **Tool** | `formatters.py` | 检测 `::` 决定显示格式 |
| **Extractor** | ~20 个提取器模块 | `ref.split("::", 1)` 构建实体 |
| **Guardrail** | sql_check / sql_join_check / sql_disambig_check | 解析 ref 中的表名/列名 |
| **Prompt** | `_base.py` / `_entities.py` | 教 LLM 使用 `::` 语法 |

全链路约 50+ 处硬编码 `::` 分割逻辑。

---

## 方案 1：取消 `::`，统一 `/` 路径

### 设计

```
formula_1.db/tables/drivers                    ← 表
formula_1.db/tables/drivers/columns/speed      ← 列
knowledge/conventions/no_concat_names           ← 知识
```

所有实体用文件系统风格的层级路径寻址，不再区分"文件"和"实体"。

### 对 agent 的影响

**优势：**
- **路径是 LLM 最熟悉的命名空间**。所有训练数据中都有大量文件路径、URL、Python import 路径。LLM 不需要学习自定义的 `::` 语法
- **glob 语义更直觉**：`formula_1.db/tables/*/columns/*` vs `formula_1.db::*.table::*.*.*.col`。前者是标准 glob，LLM 天然会写；后者是自定义语法，需要 prompt 教
- **知识节点的可发现性最好**：`knowledge/` 目录和 `formula_1.db/` 平级，agent 做 `glob "*"` 自然就能看到，不需要知道特殊命名空间
- **与主流 agent 框架一致**：Claude Code、Cursor、OpenAI file_search 都用路径寻址

**劣势：**
- `::` 的两阶段遍历语义丢失。当前 `glob "data.db::*.table"` 是"先找文件，再沿边遍历"，换成 `/` 后需要重新设计遍历引擎
- Store 内部的 `_entity_name` / `_files` 字段结构需要重写
- 50+ 处代码需要同步修改，风险高

### 改动评估

| 模块 | 改动程度 |
|---|---|
| Store (store.py) | **重写** — resolve_ref、find_nodes、_build_index 全改 |
| path_parser.py | **重写** — 整个文件逻辑翻转 |
| formatters.py | **中** — 显示逻辑调整 |
| 10+ tool 文件 | **中** — ref 参数解析 |
| ~20 extractor 模块 | **中** — ref 构建方式 |
| Prompt | **小** — 反而更简单（不需要教 `::`） |
| Guardrail | **小** — 路径解析调整 |

**结论：终极形态，但当前阶段改动量和风险都太大。**

---

## 方案 2：虚拟命名空间

### 设计

```
formula_1.db::drivers.table                          ← 数据实体（不变）
_knowledge_::no_concat_names.convention              ← 知识实体（虚拟锚点）
_knowledge_::bird_train_q123.few_shot
```

`_knowledge_` 不是真实文件，而是一个虚拟命名空间。`::` 含义不变，只是"文件"部分可以是虚拟的。

### 对 agent 的影响

**优势：**
- **改动最小**。`::` 语义完全不变，Store 核心逻辑不动
- 数据实体的寻址和遍历方式完全不受影响
- 知识实体有独立的命名空间，语义清晰

**劣势：**
- **可发现性差**。agent 做 `glob "*"` 只会看到真实文件，看不到 `_knowledge_`。需要 agent 知道去 `glob "_knowledge_::*"` — 这是一个隐式约定
- agent 需要学习两套规则："找数据用 `*.db::`，找知识用 `_knowledge_::`"。认知负担反而增加
- `_knowledge_` 是 Pontis 专有的魔法值，LLM 训练数据中没有任何参考，完全依赖 prompt 指令
- **违反最小惊讶原则**：agent 看到 `_knowledge_::foo.convention`，第一反应是去找 `_knowledge_` 文件，但找不到

### 改动评估

| 模块 | 改动程度 |
|---|---|
| Store (store.py) | **小** — `resolve_ref` 允许 `_knowledge_` 不对应真实文件 |
| find_nodes | **小** — 支持 `_knowledge_::*` 模式 |
| Tool / Extractor | **无** — `::` 语义不变 |
| Prompt | **小** — 增加一段 `_knowledge_` 的说明 |
| 新增：知识实体创建器 | **小** — 新 extractor 或脚本 |

**结论：最快能跑起来的方案，但 agent 的可发现性和认知负担是硬伤。**

---

## 方案 3：知识文件

### 设计

```
knowledge/conventions.yaml::no_concat_names.convention
knowledge/few_shots.yaml::bird_train_q123.few_shot
```

知识实体照常以 `文件::实体` 寻址，只是"文件"是人工或系统创建的知识容器文件。

### 对 agent 的影响

**优势：**
- **零改动**。现有 Store、工具、Extractor、Prompt 全部不动
- **可发现性最好**：agent 做 `glob "*"` 自然看到 `knowledge/` 目录和其中的 yaml 文件，与发现 `.db` 文件的体验完全一致
- **agent 零认知负担**：不需要学习新规则，对知识实体的操作（glob / meta / read / search）与数据实体完全相同
- **人可读可编辑**：yaml 文件本身就是文档，开发者可以直接阅读和修改知识
- **与现有 agent 框架一致**：大多数 agent 框架（Claude Code 的 CLAUDE.md、Cursor 的 .cursorrules）都把配置和知识放在项目目录下的特殊文件中

**劣势：**
- "一个知识节点也要创建一个文件"感觉多余
- 知识文件和元数据文件（`.pontis/`）的边界模糊 — 为什么不直接放 `.pontis/` 里？

### 改动评估

| 模块 | 改动程度 |
|---|---|
| Store | **无** |
| Tool / Extractor | **无**（或增加一个 yaml 提取器） |
| Prompt | **无**（或增加 yaml 实体类型说明） |

**结论：最 boring 的方案，但对 agent 来说一致性最好。**

---

## Agent 认知负担对比

LLM 的认知负担取决于两个因素：**需要学多少自定义规则** 和 **操作是否能复用已有心智模型**。

| 维度 | 方案 1 (统一 `/`) | 方案 2 (虚拟命名空间) | 方案 3 (知识文件) |
|---|---|---|---|
| 寻址规则数量 | 1 套（路径） | 2 套（文件 `::` + 虚拟 `::`） | 1 套（`文件::实体`） |
| LLM 对规则的熟悉度 | 高（路径是通用概念） | 低（`_knowledge_` 是魔法值） | 中（`::` 需要学，但规则统一） |
| 可发现性 | 高（`glob "*"` 看到一切） | 低（需要知道 `_knowledge_`） | 高（`glob "*"` 看到一切） |
| 操作一致性 | 完全统一 | 数据和知识操作相同但入口不同 | 完全统一 |
| prompt 复杂度 | 低（路径语法自解释） | 中（需要解释虚拟命名空间） | 低（复用现有 prompt） |

---

## 主流 Agent 框架参考

| 框架 | 知识/配置寻址方式 | 与哪个方案对应 |
|---|---|---|
| **Claude Code** | `CLAUDE.md` 文件放在项目根目录，agent 自动发现 | 方案 3 |
| **Cursor** | `.cursorrules` 文件 + `.cursor/` 目录 | 方案 3 |
| **OpenAI File Search** | 文件上传后用路径引用，无虚拟命名空间 | 方案 3 |
| **Windsurf** | `.windsurfrules` 文件 | 方案 3 |
| **Devin** | 项目目录下的文件，agent 用标准路径访问 | 方案 3 |
| **SWE-Agent** | 文件系统路径，无自定义寻址 | 方案 1 |

**主流框架几乎全部用"项目目录下的特殊文件"承载知识和配置**，而非虚拟命名空间。
它们的共同特点：知识就是文件，agent 用相同的方式发现和访问。

---

## 推荐方案

### 阶段一（当前）：方案 3 — 知识文件

理由：
1. **零改动验证**。不需要改任何现有代码，立刻能验证知识层对 benchmark 的效果
2. **agent 一致性最好**。LLM 不需要学任何新东西
3. **与主流框架一致**。`knowledge/` 目录下的 yaml 文件就是项目知识

具体做法：
```
example_data/bird/
├── formula_1.db
├── card_games.db
├── california_schools.db
├── knowledge/
│   ├── conventions.yaml    ← 跨库约定（不拼接列、不 ROUND 等）
│   ├── few_shots.yaml      ← 从 train set 提取的 SQL 示例
│   └── patterns.yaml       ← 常见 SQL 模式统计
```

知识 yaml 的提取器和数据 db 的提取器并行运行，知识实体同样进 Store。

### 阶段二（长期）：视需要决定是否迁移到方案 1

如果未来 Pontis 从 SQLite 专用的 Text-to-SQL 工具演化为通用数据分析 agent：
- 数据源不只有文件（API、数据库连接、流式数据）
- 实体不再有自然的文件锚点
- `::` 的"文件优先"语义成为限制

此时再做方案 1 的统一路径重构，那时有更多实际使用场景支撑设计决策。

---

## 总结

| | 方案 1 (统一 `/`) | 方案 2 (虚拟 NS) | 方案 3 (知识文件) |
|---|---|---|---|
| 改动量 | **大**（50+ 文件） | **小**（3-5 文件） | **无** |
| Agent 认知负担 | **最低** | **最高** | **低** |
| 可发现性 | **高** | **低** | **高** |
| 与主流框架一致 | 部分 | **不一致** | **一致** |
| 当前风险 | **高** | **低** | **无** |
| 长期架构纯度 | **最高** | **中** | **中** |

**现在选方案 3 验证效果，架构重构留给方案 1 的时机。**
