# json_pattern.py 测试文档

## 输出格式

```
$path | DICT | each pair patterns "key_pat": value_type     # map 类型
$path | DICT | each pair patterns {field: TYPE, ...}        # record 类型
$path | ARRAY | each item patterns {field: TYPE, ...}       # 数组
$path | TYPE                                                # 原始类型
```

**核心规则：**
- 每个条目只向下探查一层，更深层结构出现在另一个条目
- 路径前缀为 `$`，数组索引为 `[n]`，map 取值为 `[v]`
- key 模式中变化部分用 `...` 替代（如 `user_..._id_...`）
- 不统计 item/pair 数量
- **ARRAY 需要 2+ 元素、DICT 需要 2+ key 才构成重复模式并生成条目**（不足则跳过条目，继续递归子项）

---

## 1. 深层嵌套 (ARRAY of DICT of ARRAY)

**输入数据：**
```json
[
  {"user": "alice", "sessions": [
    {"id": 1, "events": [{"type": "click", "ts": 100}, {"type": "scroll"}]},
    {"id": 2, "events": [{"type": "click", "ts": 200}]}
  ]},
  {"user": "bob", "sessions": [
    {"id": 1, "events": [{"type": "click", "ts": 300}, {"type": "keydown", "ts": 301}]},
    {"id": 2, "events": []}
  ]}
]
```

**输出：**
```
$1_deep_nest.json | ARRAY | each item patterns {user: STR, sessions: ARRAY}
$1_deep_nest.json.[n].sessions | ARRAY | each item patterns {id: INT, events: ARRAY}
$1_deep_nest.json.[n].sessions.[n].events | ARRAY | each item patterns {ts: INT | NULL, type: STR}
```

**验证点：**
- 每层只展示一层深度（sessions 标记为 ARRAY，不展开）
- ts 字段因部分 item 缺失而显示 `INT | NULL`
- 路径使用 `.[n]` 表示数组索引

---

## 2. 嵌套数组 (ARRAY of ARRAY)

**输入数据：**
```json
[[1, 2, 3], [4, 5], [6]]
```

**输出：**
```
$2_arr_of_arr.json | ARRAY | each item patterns ARRAY
$2_arr_of_arr.json.[n] | ARRAY | each item patterns INT
```

**验证点：**
- 外层数组每个 item 是 ARRAY（只展示一层）
- 递归条目展示内层数组的 item 模式 (INT)

---

## 3. 联合类型与可选字段

**输入数据：**
```json
[
  {"name": "a", "value": 10, "tag": "x"},
  {"name": "b", "value": "hello", "tag": "y"},
  {"name": "c"},
  {"name": "d", "value": 3.14}
]
```

**输出：**
```
$3_union_optional.json | ARRAY | each item patterns {name: STR, tag: NULL | STR, value: FLOAT | INT | NULL | STR}
```

**验证点：**
- value 字段类型合并为 `FLOAT | INT | NULL | STR`
- tag 字段因缺失而加入 `NULL`
- 只有 name 在所有 item 中一致存在

---

## 4. Map + 列表值

**输入数据：**
```json
{"row_01": [10, 20, 30], "row_02": [40, 50], "row_10": [60]}
```

**输出：**
```
$4_map_list_val.json | DICT | each pair patterns "row_...": ARRAY
$4_map_list_val.json.[v] | ARRAY | each item patterns INT
```

**验证点：**
- key 模式检测：`row_01`, `row_02`, `row_10` → `row_...`
- value 类型为 ARRAY，不展开（一层深度）
- 递归条目展示 `[v]` 的 item 模式

---

## 5. Record 混合类型

**输入数据：**
```json
{
  "title": "Report", "version": 2, "active": true,
  "metadata": {"author": "admin", "created": "2024-01-01"},
  "tags": ["finance", "q1"],
  "data": [[1, 2], [3, 4]]
}
```

**输出：**
```
$5_record.json | DICT | each pair patterns {title: STR, version: INT, active: BOOL, metadata: DICT, tags: ARRAY, data: ARRAY}
$5_record.json.metadata | DICT | each pair patterns {author: STR, created: STR}
$5_record.json.tags | ARRAY | each item patterns STR
$5_record.json.data | ARRAY | each item patterns ARRAY
$5_record.json.data.[n] | ARRAY | each item patterns INT
```

**验证点：**
- Record 类型展示所有 field 的类型
- metadata 标记为 DICT，单独条目展开
- data 是嵌套数组，两层分别有各自条目

---

## 6. 嵌套 Map

**输入数据：**
```json
{
  "dept_01": {"emp_01": {"name": "Alice", "role": "eng"}, "emp_02": {"name": "Bob", "role": "eng"}},
  "dept_02": {"emp_10": {"name": "Carol", "role": "mgr"}}
}
```

**输出：**
```
$6_nested_map.json | DICT | each pair patterns "dept_...": DICT
$6_nested_map.json.[v] | DICT | each pair patterns "emp_...": {name: STR, role: STR}
$6_nested_map.json.[v].[v] | DICT | each pair patterns {name: STR, role: STR}
```

**验证点：**
- 外层 map：key 模式 `dept_...`，value 为 DICT（因内部 key 集合不一致，不内联展开）
- 内层 map：key 模式 `emp_...`，value 结构一致 → 内联展示 `{name: STR, role: STR}`
- 最内层为 record，展示具体字段

---

## 7. 非单调数字 key

**输入数据：**
```json
{"col_3": {"val": 1}, "col_1": {"val": 2}, "col_2": {"val": 3}}
```

**输出：**
```
$7_non_monotonic.json | DICT | each pair patterns "col_...": {val: INT}
```

**验证点：**
- key 不需要单调递增，只要按数字边界 split 后结构一致即可检测模式
- `col_3`, `col_1`, `col_2` → split `[col_, N]` → 模式 `col_...`
- `[v]` 的 value `{"val": INT}` 只有 1 个 key，不构成重复模式，不生成 `[v]` 条目（父条目已内联展示 `{val: INT}`）

---

## 8. 单 key dict（不足 2 key）

**输入数据：**
```json
{"only_one": {"a": 1, "b": 2}}
```

**输出：**
```
$8_single_key.json.only_one | DICT | each pair patterns {a: INT, b: INT}
```

**验证点：**
- 顶层 dict 只有 1 个 key，不构成重复模式，不生成条目
- 直接递归到 `only_one` 的值 `{"a": 1, "b": 2}`（2 key record），生成条目

---

## 9. 空数组

**输入数据：**
```json
[]
```

**输出：**
```
（无输出）
```

**验证点：**
- 空数组无元素，不构成重复模式，不生成条目

---

## 10. 空 dict

**输入数据：**
```json
{}
```

**输出：**
```
（无输出）
```

**验证点：**
- 空 dict 无 key，不构成重复模式，不生成条目

---

## 11. 原始类型

**输入数据：**
```json
"just a string"
```

**输出：**
```
$11_primitive.json | STR
```

**验证点：**
- 非容器类型直接显示基础类型（原始类型无需重复模式即可生成条目）

---

## 12. 大数组 (采样)

**输入数据：**
```json
[
  {"id": 0, "status": "ok", "payload": {"size": 0}},
  ... (200 items) ...
  {"id": 200, "status": "error", "payload": {"size": 0, "error": "timeout"}}
]
```

**输出：**
```
$12_large_array.json | ARRAY | each item patterns {payload: DICT, id: INT, status: STR}
$12_large_array.json.[n].payload | DICT | each pair patterns {size: INT}
```

**验证点：**
- >100 元素自动采样合并 schema
- payload 作为 DICT 子项单独生成条目

---

## 13. 混合子节点 (record + map + record)

**输入数据：**
```json
{
  "config": {"debug": true, "port": 8080},
  "users": {"user_01": {"role": "admin"}, "user_02": {"role": "viewer"}, "user_03": {"role": "editor"}},
  "metadata": {"count": 3}
}
```

**输出：**
```
$13_mixed_children.json | DICT | each pair patterns {config: DICT, users: DICT, metadata: DICT}
$13_mixed_children.json.config | DICT | each pair patterns {debug: BOOL, port: INT}
$13_mixed_children.json.users | DICT | each pair patterns "user_...": {role: STR}
```

**验证点：**
- 顶层为 record（value 类型不一致：config/users/metadata 结构各异）
- users 子项被正确识别为 map（key 模式 `user_...`，value 一致）
- `config` 有 2 key → 生成 record 条目
- `metadata` 只有 1 key（`count`）→ 不生成条目
- `users.[v]` 的 value `{"role": STR}` 只有 1 key → 不生成条目（父条目已内联展示）

---

## 14. 三位数字 key

**输入数据：**
```json
{"doc_001": {"title": "a"}, "doc_002": {"title": "b"}, "doc_100": {"title": "c"}}
```

**输出：**
```
$14_triple_digit.json | DICT | each pair patterns "doc_...": {title: STR}
```

**验证点：**
- `doc_001`, `doc_002`, `doc_100` → split `[doc_, NNN]` → 模式 `doc_...`
- 不再使用 `{NNN}` 固定宽度占位符，统一用 `...`
- `[v]` 的 value 只有 1 key → 不生成条目

---

## 15. 不一致 value 类型

**输入数据：**
```json
{"x": {"name": "a"}, "y": [1, 2, 3], "z": {"name": "b"}}
```

**输出：**
```
$15_inconsistent_val.json | DICT | each pair patterns {x: DICT, y: ARRAY, z: DICT}
$15_inconsistent_val.json.y | ARRAY | each item patterns INT
```

**验证点：**
- value 类型不一致（dict, list, dict）→ detect_map 返回 None → 作为 record 处理
- `x` 和 `z` 的 value 只有 1 key（`name`）→ 不生成条目
- `y` 的 value 有 3 元素 → 生成 ARRAY 条目

---

## 额外边界测试

### A. 多段数字 key 模式

**输入：** `{"user_0_id_121": {"val": 1}, "user_1_id_323": {"val": 2}, "user_2_id_456": {"val": 3}}`

**输出：**
```
$A_multi_seg.json | DICT | each pair patterns "user_..._id_...": {val: INT}
```

**验证点：** 多段变化部分正确用 `...` 替代 → `user_..._id_...`

### B. 单元素数组（不足 2 元素）

**输入：** `[{"x": 1}]`

**输出：**
```
（无输出）
```

**验证点：** 1 个元素不构成重复模式。单元素 array 递归进入该元素（路径不变），但 `{"x": 1}` 也只有 1 key → 不生成条目

### C. 深层单链（全部不足 2）

**输入：** `[{"a": {"b": {"c": 1}}}]`

**输出：**
```
（无输出）
```

**验证点：** 每一层容器都只有 1 个子项，无法构成任何重复模式

### D. 恰好 2 元素

**输入：** `[{"x": 1}, {"x": 2}]`

**输出：**
```
$D_two_item.json | ARRAY | each item patterns {x: INT}
```

**验证点：** 恰好 2 元素即可构成重复模式

### E. 嵌套 record + array 混合

**输入：** `{"items": [{"name": "a", "prices": [10, 20]}, {"name": "b", "prices": [30]}]}`

**输出：**
```
$D_nested_mixed.json | DICT | each pair patterns {items: ARRAY}
$D_nested_mixed.json.items | ARRAY | each item patterns {name: STR, prices: ARRAY}
$D_nested_mixed.json.items.[n].prices | ARRAY | each item patterns INT
```

### F. bool/null 混合

**输入：** `[{"flag": true, "data": null}, {"flag": false, "data": "hello"}, {"flag": true, "data": 42}]`

**输出：**
```
$E_bool_null.json | ARRAY | each item patterns {flag: BOOL, data: INT | NULL | STR}
```

### G. 空数组的数组

**输入：** `[[], [], []]`

**输出：**
```
$F_array_of_empty.json | ARRAY | each item patterns ARRAY
$F_array_of_empty.json.[n] | ARRAY
```

### H. 不等长 token 的 key

**输入：** `{"a_1_b": 1, "a_22_cc": 2, "a_333_ddd": 3}`

**输出：**
```
$G_uneven_keys.json | DICT | each pair patterns {a_1_b: INT, a_22_cc: INT, a_333_ddd: INT}
```

**验证点：** token 结构不一致（不同长度的分割结果）→ 无法检测 key 模式 → record

### I. 整数 key

**输入：** `{0: {"name": "a"}, 1: {"name": "b"}, 2: {"name": "c"}}` (JSON 序列化后 key 为字符串)

**输出：**
```
$I_int_keys.json | DICT | each pair patterns STR: {name: STR}
```

**验证点：** JSON key 全为字符串，纯数字 key 无公共前缀/后缀 → key_pattern=None，key_type=STR

### J. 三层嵌套数组

**输入：** `[[[1, 2], [3]], [[4], [5, 6]]]`

**输出：**
```
$J_deep_array.json | ARRAY | each item patterns ARRAY
$J_deep_array.json.[n] | ARRAY | each item patterns ARRAY
$J_deep_array.json.[n].[n] | ARRAY | each item patterns INT
```

**验证点：** 每层递归正确生成独立条目

### K. 2 key record 作为 array 子项

**输入：** `[{"x": 1, "y": {"a": 1, "b": 2}}, {"x": 3, "y": {"a": 3, "b": 4}}]`

**输出：**
```
$test_2plus.json | ARRAY | each item patterns {x: INT, y: DICT}
$test_2plus.json.[n].y | DICT | each pair patterns {a: INT, b: INT}
```

**验证点：** array 子项中 2+ key 的 dict 正常生成 record 条目
