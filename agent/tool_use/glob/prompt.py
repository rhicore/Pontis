"""Glob 工具 prompt — URN 语法技术参考。"""

DESCRIPTION = "基于 URN 语法的图谱检索工具，支持名称 glob、标签过滤和多跳遍历。"

DETAIL = """\
参数：
- ref (必填): URN 查询模式
- offset: 起始位置（默认 0）
- limit: 每页最大条数（默认 100，最大 500）

## URN 语法

格式：`[project::] segment [ / segment ... ]`

每个 segment：`name_pattern[:tag1[:tag2[|tag3]]]`

组成部分：
- project:: — 项目前缀，省略时搜索当前项目
- name_pattern — 名称 glob 模式（* ? []），无标签时匹配文件名
- :tag — 标签过滤，`:` 分隔 AND，`|` 分隔 OR
- / segment — 多段用 `/` 连接，每段代表一跳遍历

遍历规则：
- 多段遍历返回路径末端段匹配的实体
- 中间段仅用于过滤路径，不体现在结果中
- 不支持 ()--() 语法，遍历一律用 / 分隔段

## 按场景查表

### 按标签查实体
| 查询 | 含义 |
|---|---|
| `*:table` | 所有表 |
| `*:col` | 所有列 |
| `*:col:INT` | 所有 INT 列 |
| `*:col:TEXT` | 所有 TEXT 列 |
| `*:file` | 所有文件 |
| `*:file:db` | 所有数据库文件 |
| `*:fk` | 所有外键关系 |
| `*:file|knowledge` | file 或 knowledge（OR） |
| `*:disambig` | 所有消歧实体 |

### 按名称匹配
| 查询 | 含义 |
|---|---|
| `*.db` | 所有 .db 后缀的文件 |
| `loan*` | 名称以 loan 开头 |
| `*Id` | 名称以 Id 结尾 |
| `*amount*` | 名称包含 amount |

### 指定文件/表查下属
| 查询 | 含义 |
|---|---|
| `financial.sqlite/*:table` | financial.sqlite 的所有表 |
| `financial.sqlite/account/*:col` | account 表的所有列 |
| `financial.sqlite/*:table/*:col` | 所有表的所有列 |
| `financial.sqlite/*:table/*:col:INT` | 所有表的 INT 列 |

### 多跳遍历
| 查询 | 含义 |
|---|---|
| `*:file:db/*:table` | 数据库文件下的表 |
| `*:file:db/*:table/*:col` | 数据库文件→表→列 |
| `*:table/*:col/*:fk` | 表→列→外键（三跳） |
| `*:fk/*:table` | 外键指向的表（反向遍历） |
| `*:col/*:col` | 有 overlap/关联关系的列对 |

### 项目路由
| 查询 | 含义 |
|---|---|
| `financial::*:table` | financial 项目的所有表 |
| `global::*:knowledge` | 全局知识库的知识实体 |

### 目录结构
| 查询 | 含义 |
|---|---|
| `./*:file` | 根目录下的文件 |
| `./*:dir` | 根目录下的子目录 |
| `data/*:file:csv` | data 目录下的 CSV 文件 |

## 完整标签列表

file, db, csv, json, table, view, col, fk, rel, overlap, dir, knowledge, chunk, disambig

## 注意
- 结果截断时末尾提示总数，用 offset 翻页
- 纯 glob（无 : 标签且无 / 遍历）按文件名匹配
- 名称 pattern 支持 *、?、[] 三种 glob 通配符
- 不要用 `glob("*")` 做起手式全图枚举；优先使用 `*.sqlite`、`*:file:db/*:table`、`<db>/*:table`、`<table>/*:col` 这类定向查询
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
