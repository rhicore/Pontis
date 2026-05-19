"""Create entity tool prompt — 创建实体节点。"""

DESCRIPTION = "在知识图谱中创建新的实体节点。"

DETAIL = """\
参数：
- ref (必填): 实体引用，格式 [project::]name[:tag1[:tag2]]
  - name 是实体名称（精确名称，不允许通配符）
  - :tag 为实体打标签（可多个），类型通过标签区分
- meta: 初始元数据（可选），常用字段为 brief 和 detail
- edges: 关系边列表（可选），每条边为 {"a": "...", "b": "..."}

## 示例

### 关系实体
ref: 'context/db/app.db/account/account_id->context/db/app.db/district/district_id:fk'
meta: {brief: "外键关系", detail: "account.account_id 引用 district.district_id"}
edges: [{a: "context/db/app.db/account/account_id:col:INTEGER", b: "context/db/app.db/district/district_id:col:INTEGER"}]

### 语义消歧实体
ref: 'points:disambig'
meta: {brief: "points 列歧义", detail: "results.points 是单场得分，driverStandings.points 是赛季总分"}
edges: [{a: "context/db/f1.db/results/points:col:INTEGER", b: "points:disambig"}]

### 文本 chunk
ref: '0001:chunk'
meta: {brief: "章节主题", detail: "该段文本定义了字段语义、约束或例外情况"}
edges: [{a: "context/knowledge.md", b: "0001:chunk"}]

### JSON pattern
ref: 'records:pattern'
meta: {brief: "records 数组结构", detail: "每条记录包含 id、name、status 等字段"}
edges: [{a: "context/json/data.json", b: "records:pattern"}]

## 硬约束
- ref 使用精确名称；通配匹配属于 find
- 来源关系通过 edges 表达
- 派生实体的 meta 写实体自身语义；来源文件、路径、父节点由边表达
- edges 两端 ref 直接复制 find/meta 返回的完整 ref
"""


def get_description() -> str:
    return f"{DESCRIPTION}\n\n{DETAIL}"
