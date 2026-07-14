"""Cypher 查询工具 prompt。"""

DESCRIPTION = "执行 Cypher 图查询语言，检索知识图谱中的实体和关系。"

DETAIL = """\
参数：
- query (必填): Cypher 查询语句
- project: 可选，指定目标 project database；一条 Cypher 只在一个 project 中执行
- offset: 起始偏移，默认 0
- limit: 最大返回条数，默认 100

标准语法：
1. 按标签查找：MATCH (n:table) RETURN n
2. 多标签 AND：MATCH (n:file:db) RETURN n
3. 属性匹配：MATCH (n {name: "loan"}) RETURN n
4. 1-hop 遍历：MATCH (d)--(t:table) RETURN d, t
5. 多跳遍历：MATCH (d)--(t:table)--(c:col) RETURN d, t, c
6. 可变长度路径：MATCH (a:table)-[*1..3]-(b:col) RETURN a, b
7. WHERE 条件：
   WHERE n.name = "loan"
   WHERE n.name STARTS WITH "client"
   WHERE n.name ENDS WITH "id"
   WHERE n.name CONTAINS "amount"
   WHERE n.name != "trans"
   WHERE n.row_count > 100
8. 通配匹配（扩展）：WHERE n.name =~ "*id"

写入语法：
9. 创建节点：CREATE (n:table {name: "loan"}) SET n.brief = "desc"
10. 设置属性：MATCH (n {name: "loan"}) SET n.brief = "desc"
11. 删除节点：MATCH (n {name: "loan"}) DELETE n
12. 创建边：MATCH (a {name: "x"}), (b {name: "y"}) CREATE EDGE (a)--(b)

每个实体都有以下标准属性：name, labels, 以及具体类型的业务属性。project 是 Cypher 执行路由；节点属性条件使用实体自身字段。
标签类型：file, db, csv, json, table, view, col, logical_col, fk, rel, column_domain, dir, knowledge, chunk, disambig
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
