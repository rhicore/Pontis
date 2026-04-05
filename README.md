# Pontis VFS

Pontis VFS（Virtual File System）是一个多模态数据源的元数据提取和虚拟文件系统，将复杂的数据结构（数据库、CSV/TSV、JSON、Markdown 等）转换为统一的树状虚拟文件系统，使 LLM Agent 能够使用熟悉的文件操作命令（`ls`、`glob`）来探索和理解数据。

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
├── tool_use/           # LLM Agent 工具集
└── common/             # 共享模块（配置、schemas）
```

### 完全解耦

- **extractor**: 只生成 `.pontis` 影子目录，不依赖 tool_use
- **tool_use**: 只读取 `.pontis` 目录，不依赖 extractor
- **common**: 共享 schemas 和配置
