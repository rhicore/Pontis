# AI 增量更新策略

## 核心矛盾

AI生成内容（semantic_summary）有两种更新触发方式：
1. **自动触发** - 源数据变化时，AI总结应该重新生成
2. **手动触发** - 用户想换模型/重新生成，不应强制重算统计数据

## 依赖层级图

```
skeleton (文件结构)
    ↓
stats (统计/sample/topk)  ← 原始数据
    ↓
semantic (AI总结)         ← 依赖 stats + sample + 列名/表名
```

**规则：下层变化 → 触发上层重新生成**

## 方案一：标记依赖版本（推荐）

### 元数据结构

```yaml
# .col 节点
created_at: '2026-04-08T05:07:56'

# 第1层：原始数据
source_table: 'users'
cardinality: 1000
null_count: 10
min_value: 1
max_value: 999

# 第2层：采样数据
sample: [1, 2, 3, 4, 5]
topk:
  - value: 1
    count: 100
    percentage: 10.0

# 第3层：AI生成（带依赖版本）
semantic_summary: '用户唯一标识符'
ai_metadata:                          # 新增
  model: 'deepseek-chat'              # 使用的模型
  prompt_version: '1.0'               # prompt版本
  generated_at: '2026-04-08T05:08:00'
  depends_on:                         # 关键：记录依赖
    stats_hash: 'a1b2c3d4'            # stats+sample+topk的hash
    source_version: 'v123'            # 源数据版本
```

### 增量检测逻辑

```python
def needs_ai_regenerate(node: NodeRef, storage: VFSStorage, config: Config) -> bool:
    """判断是否需要重新生成AI总结"""
    meta = storage.read_meta(node)
    ai_meta = meta.get('ai_metadata', {})
    
    # 1. 从未生成过AI
    if not ai_meta:
        return True
    
    # 2. 模型变化
    if ai_meta.get('model') != config.llm_model:
        return True
    
    # 3. prompt版本变化
    if ai_meta.get('prompt_version') != PROMPT_VERSION:
        return True
    
    # 4. 依赖数据变化（核心）
    current_stats_hash = _calc_stats_hash(meta)  # 计算当前stats+sample+topk的hash
    if ai_meta.get('depends_on', {}).get('stats_hash') != current_stats_hash:
        return True
    
    return False


def _calc_stats_hash(meta: dict) -> str:
    """计算统计数据的hash，用于检测变化"""
    # 只关注影响AI判断的字段
    key_fields = [
        meta.get('cardinality'),
        meta.get('sample', []),
        meta.get('topk', []),
        meta.get('min_value'),
        meta.get('max_value'),
        meta.get('semantic_summary'),  # 嵌套依赖：列的summary影响表的summary
    ]
    return hashlib.md5(str(key_fields).encode()).hexdigest()[:8]
```

### 分层更新流程

```python
def update_node(node: NodeRef, storage: VFSStorage, config: Config):
    """单个节点的增量更新"""
    meta = storage.read_meta(node)
    
    # Step 1: 检查原始数据是否需要更新（stats/sample/topk）
    if needs_stats_update(node, meta):
        update_stats(node, storage)
        # stats变化，标记sample/topk需要更新
        update_sample(node, storage)
        update_topk(node, storage)
        stats_changed = True
    else:
        stats_changed = False
    
    # Step 2: 检查AI是否需要更新
    # 情况A：stats变化了 → 自动触发AI更新
    # 情况B：用户强制指定 --regenerate-ai → 强制更新
    # 情况C：模型/prompt变化 → 触发更新
    if stats_changed or config.force_regenerate_ai or needs_ai_regenerate(node, storage, config):
        generate_semantic(node, storage, config)
```

## 方案二：分级触发策略

### 命令行接口设计

```bash
# 默认：只更新stats，AI只在依赖变化时更新
python -m extractor.sync ./my_data

# --regenerate-ai：强制重新生成所有AI总结
python -m extractor.sync ./my_data --regenerate-ai

# --regenerate-ai "*.db/users.table"：只重新生成特定节点的AI
python -m extractor.sync ./my_data --regenerate-ai "*.db/users.table"

# --level 1：只更新stats（跳过AI）
python -m extractor.sync ./my_data --level 1

# --level 2：更新stats+AI
python -m extractor.sync ./my_data --level 2
```

### 内部实现

```python
class UpdateLevel:
    SKELETON = 0    # 只检查文件结构
    STATS = 1       # + 统计/sample/topk
    SEMANTIC = 2    # + AI总结

class SyncConfig:
    level: UpdateLevel = UpdateLevel.SEMANTIC
    force_regenerate_ai: bool = False
    force_regenerate_ai_pattern: str = None  # 正则匹配需要强制更新的节点

def should_update_ai(node: NodeRef, meta: dict, config: SyncConfig) -> bool:
    """判断是否应该更新AI总结"""
    
    # 等级不够，跳过AI
    if config.level < UpdateLevel.SEMANTIC:
        return False
    
    # 强制重新生成模式
    if config.force_regenerate_ai:
        return True
    
    # 特定节点强制重新生成
    if config.force_regenerate_ai_pattern:
        import fnmatch
        if fnmatch.fnmatch(node.rel_path, config.force_regenerate_ai_pattern):
            return True
    
    # 依赖检测（见方案一）
    return needs_ai_regenerate(node, meta)
```

## 方案三：精确依赖追踪（复杂场景）

### 依赖图

```python
# 显式声明依赖关系
dependencies = {
    'db_table_semantic': {
        'depends_on': ['table_info', 'column_semantic'],  # 表AI依赖列AI
        'fields': ['row_count', 'column_count', 'columns.semantic_summary']
    },
    'db_column_semantic': {
        'depends_on': ['column_stats', 'column_sample'],
        'fields': ['cardinality', 'sample', 'topk', 'min_value', 'max_value']
    },
    'csv_semantic': {
        'depends_on': ['csv_info', 'column_semantic'],
        'fields': ['row_count', 'column_count', 'columns.semantic_summary']
    }
}
```

### 依赖变化检测

```python
class DependencyTracker:
    def __init__(self, storage: VFSStorage):
        self.storage = storage
        self._cache = {}
    
    def get_dep_hash(self, node: NodeRef, dep_fields: List[str]) -> str:
        """计算指定字段的hash"""
        meta = self.storage.read_meta(node)
        values = []
        for field in dep_fields:
            if field.startswith('columns.'):
                # 特殊处理子节点依赖
                sub_field = field.split('.')[1]
                for col_node in self.get_child_columns(node):
                    col_meta = self.storage.read_meta(col_node)
                    values.append(col_meta.get(sub_field))
            else:
                values.append(meta.get(field))
        return hashlib.md5(str(values).encode()).hexdigest()[:8]
    
    def check_dep_changed(self, node: NodeRef, ai_metadata: dict) -> bool:
        """检查依赖是否变化"""
        deps = ai_metadata.get('depends_on', {})
        for dep_name, dep_hash in deps.items():
            # 解析依赖定义
            dep_def = dependencies.get(dep_name)
            if dep_def:
                current_hash = self.get_dep_hash(node, dep_def['fields'])
                if current_hash != dep_hash:
                    return True
        return False
```

## 方案四：最小可行方案（MVP）

如果不需要复杂依赖追踪，用最简单的方式：

```python
def update_ai_if_needed(node: NodeRef, storage: VFSStorage, config: Config):
    """最简单的AI更新逻辑"""
    meta = storage.read_meta(node)
    
    # 条件1：用户强制指定 --regenerate-ai
    if config.force_regenerate_ai:
        generate_semantic(node, storage)
        return
    
    # 条件2：从未生成过AI
    if 'semantic_summary' not in meta:
        generate_semantic(node, storage)
        return
    
    # 条件3：AI生成时间早于stats更新时间
    ai_time = meta.get('ai_generated_at', '')
    stats_time = meta.get('stats_updated_at', '')
    if stats_time and ai_time < stats_time:
        generate_semantic(node, storage)
        return
```

## 推荐实现路径

### Phase 1（本周）
采用 **方案四 MVP** + **方案一的核心字段**

```yaml
# 每个AI节点增加
semantic_summary: 'xxx'
ai_metadata:
  model: 'deepseek-chat'
  generated_at: '2026-04-08T05:08:00'
  # 简单依赖：记录stats更新时间
  stats_timestamp: '2026-04-08T05:07:56'
```

更新逻辑：
```
if stats_updated_at > ai_stats_timestamp:
    regenerate_ai()
```

### Phase 2（后续）
增加 **方案二的分级控制** + **命令行参数**

```bash
python -m extractor.sync ./my_data --regenerate-ai "*.db/*.table"
```

### Phase 3（如有需要）
实现 **方案三的精确依赖图**

## 关键决策

| 场景 | 决策 |
|------|------|
| 用户只改了一个表的数据 | 只更新该表的stats+AI，不碰其他表 |
| 用户删了一列 | 删除该列节点，更新父表的AI（因为列变了） |
| 用户换AI模型 | 用 `--regenerate-ai` 强制重跑，或检测 model 字段变化 |
| 列的sample变化但cardinality没变 | 简单方案：不触发AI更新；精确方案：sample加入依赖hash |
| 1000个表同时更新 | 并行处理，但AI调用要限流（API限制） |

## 代码位置建议

```
extractor/
├── sync.py                    # 主入口
├── changeset.py               # 变化检测
├── version.py                 # 版本计算
├── updater/
│   ├── __init__.py
│   ├── base.py               # 基础更新器
│   ├── stats_updater.py      # stats/sample/topk更新
│   └── semantic_updater.py   # AI更新（带依赖检测）
```
