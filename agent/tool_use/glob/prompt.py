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

## 标签过滤示例

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

## 名称匹配示例

| 查询 | 含义 |
|---|---|
| `*.db` | 所有 .db 后缀的文件 |
| `loan*` | 名称以 loan 开头 |
| `*Id` | 名称以 Id 结尾 |
| `*amount*` | 名称包含 amount |

## 多段路径示例

| 查询 | 含义 |
|---|---|
| `financial.sqlite/*:table` | financial.sqlite 的所有表 |
| `financial.sqlite/account/*:col` | account 表的所有列 |
| `financial.sqlite/*:table/*:col` | 所有表的所有列 |
| `financial.sqlite/*:table/*:col:INT` | 所有表的 INT 列 |
| `financial.sqlite/*:table/*:fk` | 该数据库中所有表挂接到的 FK 实体 |

## 项目路由示例

| 查询 | 含义 |
|---|---|
| `financial::*:table` | financial 项目的所有表 |
| `bird::*:knowledge` | BIRD 跨库经验库中的知识实体 |

"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
