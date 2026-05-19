"""Query tool prompt — SQL 执行工具。"""

DESCRIPTION = "在 DB/CSV/TSV/JSON 文件 ref 或当前 workspace 上执行只读 SQL 查询，返回结果或错误信息。"

DETAIL = """\
参数：
- sql (必填): SQL 查询语句，只允许 SELECT
- ref (必填): DB/CSV/TSV/JSON 文件图谱 ref，或 `.` 表示当前 workspace
  - DB: `data.db:file:db`
  - CSV/TSV: `data.csv:file:csv:text`
  - JSON records: `data.json:file:json`
  - 当前 task/workspace: `.`
- limit: 返回最大行数，默认 100

## 硬约束

- 只执行 SELECT / WITH ... SELECT / PRAGMA 只读语句。
- CREATE、DROP、ALTER、INSERT、UPDATE、DELETE 等写操作由工具拒绝。
- ref 必须指向 DB/CSV/TSV/JSON records 文件，或使用 `.` 注册当前 workspace 的结构化数据源。

## 表映射

- SQLite DB 使用数据库原表名
- CSV/TSV 会投影为临时只读表，表名为 `this`，同时提供一个基于文件名的别名，如 `orders.csv` 可用 `orders`
- JSON 会自动识别顶层 list[dict] 或 `records/data/items/rows/results` 中的 list[dict]，表名同样为 `this`
- `ref="."` 会把当前 workspace 中的 DB/CSV/TSV/JSON records 注册到同一个临时 SQLite；无冲突时可直接用文件名/表名别名，冲突时使用错误信息里的完整表名

## 返回

- 执行成功时返回结果行（表格格式），最多返回 limit 行
- 执行失败时返回错误信息，你可以根据错误修正 SQL 后重试
- 大结果集会被截断，末尾提示总行数

## 示例

```json
{"ref":"context/csv/orders.csv:file:csv:text","sql":"SELECT status, COUNT(*) AS n FROM this GROUP BY status"}
```

```json
{"ref":".","sql":"SELECT c.name, COUNT(*) AS n FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.name"}
```

```json
{"ref":"context/json/posts.json:file:json","sql":"SELECT OwnerUserId, COUNT(*) AS n FROM this GROUP BY OwnerUserId HAVING n > 10"}
```

SQL 细节：
- 列名有空格或特殊字符时用双引号包裹，如 SELECT "Player Name" FROM ...
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
