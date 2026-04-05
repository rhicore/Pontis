# Pontis VFS 架构文档

## 文件树结构

```
.pontis/
├── raw_data/                              # 原始数据目录（无后缀）
│
├── [数据库名].db/                          # 数据库目录 (.db)
│   ├── _meta.yml                          # 数据库元数据
│   │
│   ├── [表名].table/                       # 表目录 (.table)
│   │   ├── _meta.yml                      # 表元数据
│   │   │
│   │   ├── [列名].[数据类型].col/          # 列目录 (.col)
│   │   │   ├── _meta.yml                  # 列元数据
│   │   │   ├── .sample/                   # 采样值目录
│   │   │   │   ├── _meta.yml
│   │   │   │   └── _bin                   # 序列化的采样值
│   │   │   └── .topk/                     # Top-K 值目录
│   │   │       ├── _meta.yml
│   │   │       └── _bin                   # 序列化的 Top-K 值
│   │   │
│   │   └── [其他列].[类型].col/            # 更多列...
│   │
│   ├── [视图名].view/                      # 视图目录 (.view)
│   │   ├── _meta.yml                      # 视图元数据
│   │   │
│   │   ├── [列名].[数据类型].col/          # 视图列 (.col)
│   │   │   ├── _meta.yml
│   │   │   ├── .sample/
│   │   │   │   ├── _meta.yml
│   │   │   │   └── _bin
│   │   │   └── .topk/
│   │   │       ├── _meta.yml
│   │   │       └── _bin
│   │   │
│   │   └── [源列名]__to__[目标表].[目标列].flow   # 血缘关系 (.flow)
│   │       └── _meta.yml
│   │
│   ├── [表名].[列名]__to__[目标表].[目标列].fk    # 物理外键 (.fk)
│   │   └── _meta.yml
│   │
│   └── [表名].[列名]__to__[目标表].[目标列].rel   # 逻辑关系 (.rel)
│       └── _meta.yml
│
├── [文档名].md/                            # Markdown 文档 (.md)
│   ├── _meta.yml
│   ├── section_1.chunk/                    # 文本分片 (.chunk)
│   │   ├── _meta.yml
│   │   └── _bin                            # 文本内容
│   └── section_2.chunk/
│       ├── _meta.yml
│       └── _bin
│
├── [文档名].txt/                           # 纯文本文档 (.txt)
│   ├── _meta.yml
│   ├── paragraph_1.chunk/
│   │   ├── _meta.yml
│   │   └── _bin
│   └── paragraph_2.chunk/
│       ├── _meta.yml
│       └── _bin
│
├── [文档名].pdf/                           # PDF 文档 (.pdf)
│   ├── _meta.yml
│   ├── page_1.chunk/
│   │   ├── _meta.yml
│   │   └── _bin                            # 文本提取内容
│   └── page_2.chunk/
│       ├── _meta.yml
│       └── _bin
│
├── [表名].csv/                             # CSV 文件 (.csv)
│   ├── _meta.yml
│   ├── [列名].TEXT.col/                    # CSV 列 (.col)
│   │   ├── _meta.yml
│   │   ├── .sample/
│   │   │   ├── _meta.yml
│   │   │   └── _bin
│   │   └── .topk/
│   │       ├── _meta.yml
│   │       └── _bin
│   └── [其他列].[类型].col/
│
├── [表名].tsv/                             # TSV 文件 (.tsv)
│   ├── _meta.yml
│   └── [列名].[类型].col/                   # 同 CSV 结构
│
├── [文件名].json/                          # JSON 文件 (.json)
│   ├── _meta.yml
│   └── [key_路径].json/                     # JSON 键目录
│       ├── _meta.yml
│       └── [子键].json/                     # 嵌套结构
│           ├── _meta.yml
│           └── ...
│
├── [文件名].yaml/                          # YAML 文件 (.yaml)
│   ├── _meta.yml
│   └── [key_路径].yaml/                     # 同 JSON 结构
│       ├── _meta.yml
│       └── ...
│
├── [文件名].xml/                           # XML 文件 (.xml)
│   ├── _meta.yml
│   └── [元素路径].xml/                      # XML 元素目录
│       ├── _meta.yml
│       └── ...
│
├── [文件名].toml/                          # TOML 文件 (.toml)
│   ├── _meta.yml
│   └── [section].toml/                     # TOML section 目录
│       ├── _meta.yml
│       └── ...
│
└── [文件名].hcl/                           # HCL 文件 (.hcl)
    ├── _meta.yml
    └── [block].hcl/                        # HCL block 目录
        ├── _meta.yml
        └── ...
```

---

## 当前实现的模块架构

### 第一阶段：骨架生成

| 模块文件 | 匹配模式 | 输出内容 |
|---------|---------|---------|
| `skeleton.py` | 源文件夹遍历 | 基础VFS树，每个节点包含 `_meta.yml` (name, suffix, type, source_path, modified_at, created_at) |

**骨架展开规则：**
- `*.db` → 展开为 `[table].table/` + `[column].[type].col/`
- `*.csv/*.tsv` → 展开为 `[column].TEXT.col/`

---

### 第二阶段：单节点信息生成


| 模块文件 | 匹配模式 | 输出字段 |
|---------|---------|---------|
| `db_info.py` | `*.db` | table_count, view_count, index_count, file_size |
| `db_table_info.py` | `*.db/*.table` | row_count, column_count, primary_key |
| `db_column_stats.py` | `*/*.table/*.col` | cardinality, null_count, null_percentage, min, max, mean |
| `db_column_sample.py` | `*/*.table/*.col` | 创建 `.sample/` 文件夹，包含 `_meta.yml` + `_bin` (pickle序列化) |
| `db_column_topk.py` | `*/*.table/*.col` | 创建 `.topk/` 文件夹，包含 `_meta.yml` + `_bin` (pickle序列化) |
| `csv_info.py` | `*.csv` / `*.tsv` | row_count, column_count, file_size, delimiter |
| `csv_column_stats.py` | `*.csv/*.col` / `*.tsv/*.col` | cardinality, null_count, null_percentage, min, max, mean |
| `csv_column_sample.py` | `*.csv/*.col` | 创建 `.sample/` 文件夹，包含 `_meta.yml` + `_bin` |
| `csv_column_topk.py` | `*.csv/*.col` | 创建 `.topk/` 文件夹，包含 `_meta.yml` + `_bin` |
| `serialized_info.py` | `*.json` / `*.yaml` / `*.xml` / `*.toml` / `*.hcl` | file_size, line_count, char_count, structure_type, top_level_keys/array_length/root_element/child_elements, `_bin` (原始内容) |
| `md_info.py` | `*.md` | file_size, line_count, char_count, heading_count, code_block_count, inline_code_count, link_count, image_count, table_count, first_heading, `_bin` (原始内容) |
| `txt_info.py` | `*.txt` | file_size, encoding, char_count, line_count, paragraph_count, avg_line_length, `_bin` (原始内容) |
| `txt_chunk.py` | `*.txt` | 创建 `[paragraph_N].chunk/` 文件夹，包含 `_meta.yml` + `_bin` (段落文本) |
| `pdf_info.py` | `*.pdf` | file_size, page_count, title, author, creator, producer, creation_date, modification_date, sample_text |
| `pdf_chunk.py` | `*.pdf` | 创建 `[page_N].chunk/` 文件夹，包含 `_meta.yml` + `_bin` (页面文本) |
| `db_table_relations.py` | `*.db/*.table` | 在表下创建 `[col]__to__[target_table].[target_col].rel/` 文件夹，包含 `_meta.yml` (relation_type, from_table, from_column, to_table, to_column, confidence) |
| `db_table_semantic.py` | `*.db/*.table` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `db_column_semantic.py` | `*/*.table/*.col` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `csv_semantic.py` | `*.csv` / `*.csv/*.col` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `json_semantic.py` | `*.json` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `yaml_semantic.py` | `*.yaml` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `xml_semantic.py` | `*.xml` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `toml_semantic.py` | `*.toml` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `hcl_semantic.py` | `*.hcl` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `md_semantic.py` | `*.md` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `txt_semantic.py` | `*.txt` | `_meta.yml` 追加 semantic_summary (AI生成) |
| `pdf_semantic.py` | `*.pdf` | `_meta.yml` 追加 semantic_summary (AI生成) |

