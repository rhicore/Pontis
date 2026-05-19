# Derived Entity Modeling Principles

本文档记录 extractor / explorer 生成派生实体时的图建模原则，避免为了实现方便把关系重复塞进节点属性。

## 核心原则

能用图边表达的关系，不要再写成节点属性。

例如文本 chunk：

```text
(file)-[:RELATED_TO]->(chunk)
```

这条边已经表达了 chunk 来源于哪个文件，因此 chunk 节点不应再写：

```text
source_path
file_path
parent_path
```

chunk 节点只保留描述自身的属性：

```text
chunk_index
start_line
end_line
brief
detail
```

## 判断标准

一个字段如果回答的是“这个实体属于谁、来自哪里、连接到谁”，优先建边。

一个字段如果回答的是“这个实体自身是什么、有什么局部事实”，才放在节点属性里。

常见例子：

```text
来源文件        用边表达: (file)-->(chunk)
表属于数据库    用边表达: (db)-->(table)
列属于表        用边表达: (table)-->(col)
pattern 属于 JSON 文件  优先用边表达
chunk 行号范围    用属性表达: start_line/end_line
摘要文本        用属性表达: brief/detail
```

## 为什么不能重复写

重复写关系属性会带来几个问题：

- 图里已经有边，属性里再写一份会产生两个事实来源。
- 重命名、移动、清理节点时容易漏改属性。
- agent 会把属性当成主要事实，弱化图结构。
- extractor / explorer 之间容易各自发明 `source_path`、`parent`、`owner` 等重复字段。

## 实现要求

派生实体创建时应显式连边：

```text
create_entity({
  "ref": "0001:chunk",
  "meta": {
    "chunk_index": 1,
    "start_line": 1,
    "end_line": 120,
    "brief": "...",
    "detail": "..."
  },
  "edges": [
    {"a": "context/knowledge.md", "b": "0001:chunk"}
  ]
})
```

读取或清理派生实体时，也应沿边查询：

```cypher
MATCH (f:file)--(c:chunk)
WHERE f.path = $path
RETURN c
```

不要写成：

```cypher
MATCH (c:chunk)
WHERE c.source_path = $path
RETURN c
```

## 允许的例外

只有在以下情况可以考虑保留关系型属性：

- 外部系统要求一个稳定、不可通过图遍历恢复的 ID。
- 该属性是原始数据本身的一部分，而不是 Pontis 推导出来的关系。
- 性能瓶颈已经被确认，且文档明确说明它是缓存字段，不是事实来源。

即使出现例外，也应优先使用 `_cache_*` 或 `_external_*` 这类明确前缀，避免 agent 把它误认为业务事实。

## 后续检查点

之后检查 extractor / explorer 时，重点看这些字段：

```text
source_path
file_path
parent_path
db_path
table_path
owner
belongs_to
```

如果这些字段能由相邻边或短路径恢复，应迁移到边查询。
