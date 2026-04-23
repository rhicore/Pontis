"""SQL 生成规范 — 所有需要生成 SQL 的模式共享。"""

_SQL_RULES = r"""## SQL 生成规范

当你需要为用户生成 SQL 查询时：

1. **Pontis 元数据与数据库同步** — glob 返回的列名、类型、表名直接来自数据库元数据，是准确的。brief/detail 是 AI 总结可能有偏差，但实体名中的列名、表名、类型是精确的
2. **注意名称相似列的区别** — 不同表中可能有语义相似但含义不同的列，必须根据问题语义精确选择
3. **JOIN 前查阅关系实体** — 生成 JOIN 前，先 glob 查看 .fk / .overlap / .rel 实体，读取 meta 中的 format_hint 等信息来了解列间关系的实际数据状况。但不要迷信关系实体——如果经过充分的列值调查（cardinality、sample、topk），你高置信地认为两个列可以 JOIN（即使没有对应的关系实体），也可以直接 JOIN
4. **间接 JOIN 路径** — 当两个表之间没有直接 FK/rel 时，用 find_path 查找桥接路径。find_path 会解析图谱中的 FK/overlap 关系，找出连接两个表的中间表。例如 Player 和 Country 没有直接 FK，但 find_path 会发现 Match 表同时关联两者
"""


def get_sql_rules() -> str:
    return _SQL_RULES
