"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的实体节点。"

DETAIL = """\
参数：
- ref (必填): 实体引用，格式 [project::]name[:tag1[:tag2]]
  - name 是实体名称（精确名称，不允许通配符）
  - :tag 为实体打标签（可多个），类型通过标签区分
- meta: 初始元数据（可选），建议包含 brief 和 detail
- edges: 关系边列表（可选），每条边为 {"a": "...", "b": "..."}

## 示例

### 关系实体
ref: 'account.account_id->district.district_id:fk'
meta: {brief: "外键关系", detail: "account.account_id 引用 district.district_id"}
edges: [{a: "account", b: "district.district_id:fk"}]

### 语义消歧实体
ref: 'points:disambig'
meta: {brief: "points 列歧义", detail: "results.points 是单场得分，driverStandings.points 是赛季总分"}
edges: [{a: "results.points", b: "points:disambig"}]

### 知识实体
ref: 'no_concat:convention'
meta: {brief: "避免字符串拼接", detail: "不要用 || 拼接..."}
ref: 'count_with_group_by:pattern'
meta: {brief: "分组计数模式", detail: "..."}

## 注意
- 如果实体已存在会报错，如需更新请使用 update_meta
- name 必须是精确名称，不允许通配符
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
