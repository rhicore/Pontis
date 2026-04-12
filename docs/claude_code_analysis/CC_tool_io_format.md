# Claude Code 工具 I/O 格式参考

基于 `reference/CC/` 源码分析。

---

## 1. Read (FileReadTool)

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 文件绝对路径 |
| `offset` | number | 否 | 起始行号（1-indexed），默认从第 1 行开始 |
| `limit` | number | 否 | 读取行数，默认最多 2000 行 |
| `pages` | string | 否 | PDF 页码范围（如 `"1-5"`, `"3"`, `"10-20"`），最多 20 页 |

### 输出类型（discriminated union on `type`）

| type | 返回字段 | 适用场景 |
|------|----------|----------|
| `text` | filePath, content, numLines, startLine, totalLines | 文本文件 |
| `image` | base64, type (MIME), originalSize, dimensions? | 图片 (png/jpg/jpeg/gif/webp) |
| `notebook` | filePath, cells[] | Jupyter (.ipynb) |
| `pdf` | filePath, base64, originalSize | PDF 小文件 |
| `parts` | filePath, originalSize, count, outputDir | PDF 分页提取 |
| `file_unchanged` | filePath | 重复读取同一文件（去重优化） |

### 返回给模型的文本格式

**文本文件** — `cat -n` 格式（`addLineNumbers` 函数）：

```
     1	| line content here
     2	| another line
     3	| ...
```

- 行号右对齐，与内容用 ` | ` 分隔
- 首尾可能附加 `<system-reminder>` 标签（空文件警告、偏移越界警告、安全提示等）

**图片/PDF** — 以 inline image 或 document block 形式发送，UI 只显示摘要（如 `Read image (42KB)`）

---

## 2. Grep (GrepTool)

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | string | 是 | 正则表达式（ripgrep 语法） |
| `path` | string | 否 | 搜索目录/文件，默认 cwd |
| `glob` | string | 否 | 文件名过滤（如 `"*.js"`, `"*.{ts,tsx}"`） |
| `output_mode` | enum | 否 | `"content"` / `"files_with_matches"` / `"count"`，**默认 `files_with_matches`** |
| `-B` | number | 否 | 匹配行前 N 行上下文 |
| `-A` | number | 否 | 匹配行后 N 行上下文 |
| `-C` / `context` | number | 否 | 前后 N 行上下文（优先于 -B/-A） |
| `-n` | boolean | 否 | 显示行号，默认 true |
| `-i` | boolean | 否 | 忽略大小写 |
| `type` | string | 否 | 文件类型过滤（如 `js`, `py`, `rust`） |
| `head_limit` | number | 否 | 限制输出条数，默认 250；传 0 不限 |
| `offset` | number | 否 | 跳过前 N 条，默认 0 |
| `multiline` | boolean | 否 | 跨行匹配（`. `匹配换行），默认 false |

### 输出结构

```typescript
{
  mode?: 'content' | 'files_with_matches' | 'count'
  numFiles: number
  filenames: string[]
  content?: string
  numLines?: number       // content 模式
  numMatches?: number     // count 模式
  appliedLimit?: number   // 实际截断数量
  appliedOffset?: number  // 实际偏移量
}
```

### 返回给模型的文本格式

**content 模式** — ripgrep 原始格式，路径转为相对路径：

```
relative/path/to/file.py:42:def main():
relative/path/to/file.py:43:    print("hello")
```

格式：`文件路径:行号:匹配内容`，每行一条匹配。末尾可能附加分页信息：
```
[Showing results with pagination = limit: 250, offset: 100]
```

**count 模式**：

```
relative/path/to/file.py:15
another/file.py:3

Found 18 total occurrences across 2 files.
```

**files_with_matches 模式（默认）**：

```
Found 3 files
relative/path/to/file1.py
relative/path/to/file2.js
relative/path/to/file3.ts
```

---

## 3. Glob (GlobTool)

### 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | string | 是 | glob 匹配模式（如 `"**/*.js"`, `"src/**/*.ts"`） |
| `path` | string | 否 | 搜索目录，默认 cwd |

### 输出结构

```typescript
{
  durationMs: number      // 执行耗时（ms）
  numFiles: number        // 匹配文件数
  filenames: string[]     // 相对路径文件名列表
  truncated: boolean      // 是否被截断（上限 100 个文件）
}
```

### 返回给模型的文本格式

**纯文件路径列表**，每行一个：

```
src/components/App.tsx
src/components/Button.tsx
src/utils/helpers.ts
```

如果结果被截断（超过 100 个文件），末尾追加：
```
(Results are truncated. Consider using a more specific path or pattern.)
```

无匹配时返回：`No files found`

---

## 格式对比总结

| 工具 | 定位 | 索引方式 | 路径显示 | 分隔符 |
|------|------|----------|----------|--------|
| **Read** | 读取单个文件内容 | 行号 `cat -n` | 不在内容中显示路径 | ` \| `（空竖线空） |
| **Grep** | 跨文件搜索内容 | `path:line_num:` | 每行带相对路径 | `:`（冒号） |
| **Glob** | 按文件名查找 | 无索引 | 每行一个路径 | 无（纯路径） |

### 核心设计差异

- **Read** — 已知目标文件，无需在每行重复路径；行号用 ` | ` 对齐显示
- **Grep** — 结果可能来自多个文件，必须用 `path:line:` 前缀标识来源（ripgrep 原生格式）
- **Glob** — 只返回路径列表，无内容展示，按修改时间排序
