# Pontis VFS

Pontis VFS（Virtual File System）是一个多模态数据源的元数据提取和虚拟文件系统，将复杂的数据结构（数据库、CSV、JSON、Markdown 等）转换为统一的树状虚拟文件系统，使 LLM Agent 能够使用熟悉的文件操作命令（`ls`、`meta`）来探索和理解数据。

## 核心概念

传统数据探索需要针对不同数据源学习不同工具：
- 数据库需要 SQL 客户端
- JSON 需要解析器
- CSV 需要表格工具

**Pontis 的创新**：将所有数据源统一为虚拟文件系统，用标准文件操作命令即可探索任何数据。

## 架构

```
Pontis/
├── extractor/          # 元数据提取引擎（两阶段设计）
│   ├── extractors/     # Phase 1: 单节点提取器
│   └── enrichers/      # Phase 2: 跨节点富化器
├── tool_use/           # LLM Agent 工具集
└── common/             # 共享模块（配置、schemas）
```

### 两阶段提取设计

| 阶段 | 模块 | 职责 | 特点 |
|------|------|------|------|
| Phase 1 | extractors | 单节点元数据提取 | 直接读取源数据，生成基础元数据 |
| Phase 2 | enrichers | 跨节点语义富化 | 需要完整树结构，AI 生成摘要、检测 join 关系 |

**Enrichers**:
- `ColumnSemanticEnricher`: 为列生成 `brief`（20词内）和 `detail` 描述
- `TableSemanticEnricher`: 为表生成 `brief`（20词内）和 `detail` 描述
- `JoinRelationEnricher`: 检测外键关系，生成正向/反向 join 记录

### 完全解耦

- **extractor**: 只生成 `.pontis` 影子目录，不依赖 tool_use
- **tool_use**: 只读取 `.pontis` 目录，不依赖 extractor
- **common**: 共享 schemas 和配置

## 使用方法

### 1. 元数据提取

```bash
# 基本用法
uv run python -m extractor ./data

# 启用 LLM 语义富化（需要配置 API key）
uv run python -m extractor ./data
```

提取完成后会在 `./data/.pontis` 生成影子目录结构。

### 2. 元数据浏览

```bash
# 列出目录内容（紧凑格式）
uv run python -m tool_use ls ./data/.pontis/db/sales.db

# 查看节点详情
uv run python -m tool_use meta ./data/.pontis/db/sales.db/orders

# 查看特定属性
uv run python -m tool_use meta ./data/.pontis/db/sales.db/orders row_count
```

## 配置

在 `common/config.py` 或 `pontis.yml` 中配置：

```yaml
# LLM 设置（用于语义富化）
llm_provider: "https://api.deepseek.com"  # 或 "openai", "anthropic"
llm_model: "deepseek-chat"
llm_api_key: "your-api-key"
llm_enabled: true

# 语义描述长度限制
brief_max_words: 20

# 提取设置
sample_size: 100
top_k: 5
```

## 元数据结构

提取生成的 `.pontis` 目录结构与原始数据镜像：

```
data/.pontis/
├── db/
│   └── event.db/
│       ├── _meta.yml          # DB 元数据
│       └── event/             # 表
│           ├── _meta.yml      # 表元数据（含 brief/detail）
│           ├── event_id/      # 列
│           │   └── _meta.yml  # 列元数据（含 brief/detail）
│           └── ...
└── csv/
    └── data.csv/
        └── _meta.yml
```

所有 `_meta.yml` 采用扁平化设计，无嵌套结构。具体字段定义见 `common/schemas/` 下的 Pydantic 模型。

## License

MIT License
