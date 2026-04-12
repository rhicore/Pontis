# FileReadTool 详细设计文档

> 源码路径：`src/tools/FileReadTool/`
> 工具注册名：`Read`

---

## 1. 模块结构

```
src/tools/FileReadTool/
├── FileReadTool.ts      # 主逻辑：输入/输出 schema、权限校验、核心 call 函数
├── prompt.ts            # 工具描述 prompt 模板与常量
├── limits.ts            # 读取限制配置（文件大小、token 上限）
├── imageProcessor.ts    # 图片处理模块（sharp / image-processor-napi）懒加载封装
└── UI.tsx               # 终端 UI 渲染（React 组件，用于 Ink 框架）
```

---

## 2. 输入 Schema（Input）

```typescript
z.strictObject({
  file_path: string   // 必填，文件的绝对路径
  offset:   int ≥ 0   // 可选，起始行号（默认 1）
  limit:    int > 0   // 可选，读取行数（默认读全部，上限 2000 行）
  pages:    string    // 可选，PDF 页码范围，如 "1-5"、"3"、"10-20"（最多 20 页/次）
})
```

关键点：
- `file_path` 必须是绝对路径，相对路径会在 `backfillObservableInput` 中通过 `expandPath` 展开
- `offset` 语义为"从第 N 行开始"（1-indexed），内部转换为 0-indexed 传入 `readFileInRange`
- `limit` 不提供时读取全部内容（受 maxSizeBytes 限制）
- `pages` 仅对 PDF 文件有效

---

## 3. 输出 Schema（Output）

输出是一个 **discriminated union**，通过 `type` 字段区分 6 种返回类型：

### 3.1 `text` — 普通文本文件
```typescript
{
  type: 'text',
  file: {
    filePath: string     // 原始输入路径
    content: string      // 文件内容
    numLines: number     // 本次返回的行数
    startLine: number    // 起始行号
    totalLines: number   // 文件总行数
  }
}
```
- 内容以 `cat -n` 格式（带行号）呈现给模型
- 空文件返回空 content，附带 `<system-reminder>` 警告
- offset 超出文件总行数时返回空 content + 提示信息

### 3.2 `image` — 图片文件
```typescript
{
  type: 'image',
  file: {
    base64: string                                          // Base64 编码图片数据
    type: 'image/jpeg' | 'image/png' | 'image/gif' | 'image/webp'
    originalSize: number                                    // 原始文件大小（字节）
    dimensions?: {
      originalWidth?: number       // 原始宽度 px
      originalHeight?: number      // 原始高度 px
      displayWidth?: number        // 缩放后宽度 px
      displayHeight?: number       // 缩放后高度 px
    }
  }
}
```
- 支持格式：png、jpg、jpeg、gif、webp
- 自动缩放 + 降采样，超 token 预算时执行渐进式压缩

### 3.3 `notebook` — Jupyter Notebook
```typescript
{
  type: 'notebook',
  file: {
    filePath: string
    cells: any[]        // notebook 单元格数组（code、markdown 等）
  }
}
```
- 读取 .ipynb 文件，返回所有单元格及输出
- 过大时提示使用 `jq` 按范围读取

### 3.4 `pdf` — PDF 文件（完整读取）
```typescript
{
  type: 'pdf',
  file: {
    filePath: string
    base64: string       // PDF 的 Base64 数据
    originalSize: number
  }
}
```
- 仅在模型支持 PDF 且文件不太大时使用
- PDF 内容作为 `document` 类型的 supplemental message 发送给模型

### 3.5 `parts` — PDF 页面提取
```typescript
{
  type: 'parts',
  file: {
    filePath: string
    originalSize: number
    count: number         // 提取的页数
    outputDir: string     // 提取的页面图片目录
  }
}
```
- 当 PDF 过大或模型不支持直接读取时，通过 poppler-utils 提取为图片
- 图片作为 `image` block 附带在 supplemental message 中

### 3.6 `file_unchanged` — 文件未修改（去重优化）
```typescript
{
  type: 'file_unchanged',
  file: {
    filePath: string
  }
}
```
- 当同一文件同一范围在本次会话中已读过且文件未修改时返回
- 返回一条提示让模型引用之前的读取结果，节省 token

---

## 4. 核心执行流程

```
call(input, context)
  │
  ├─ 1. 路径展开 (expandPath)
  │
  ├─ 2. 去重检查 (readFileState)
  │     比较 offset/limit/mtime，命中则返回 file_unchanged
  │     可通过 GB 开关 tengu_read_dedup_killswitch 禁用
  │
  ├─ 3. Skill 发现 (discoverSkillDirsForPaths)
  │     根据文件路径发现并激活相关 skills（异步，不阻塞）
  │
  ├─ 4. callInner() — 按文件类型分发
  │     │
  │     ├─ .ipynb → readNotebook() → 返回 notebook 类型
  │     │
  │     ├─ 图片 (.png/.jpg/.gif/.webp)
  │     │   → readImageWithTokenBudget()
  │     │   → 标准缩放 → token 检查 → 必要时渐进压缩
  │     │   → 返回 image 类型
  │     │
  │     ├─ PDF (.pdf)
  │     │   ├─ 有 pages 参数 → extractPDFPages() → 返回 parts 类型
  │     │   ├─ 超过页数限制 → 报错
  │     │   ├─ 模型不支持 / 文件过大 → extractPDFPages() 提取图片
  │     │   └─ 正常情况 → readPDF() → 返回 pdf 类型
  │     │
  │     └─ 其他 → 文本文件处理
  │         → readFileInRange(offset, limit, maxSizeBytes)
  │         → validateContentTokens(content, ext, maxTokens)
  │         → 更新 readFileState
  │         → 触发 fileReadListeners
  │         → 返回 text 类型
  │
  └─ 5. ENOENT 错误处理
        ├─ macOS 截图路径兼容（空格 vs thin space U+202F）
        ├─ 相似文件名建议 (findSimilarFile)
        └─ CWD 路径建议 (suggestPathUnderCwd)
```

---

## 5. 读取限制体系

文件读取受 **两层限制** 保护：

| 限制 | 默认值 | 检查时机 | 作用 |
|------|--------|----------|------|
| `maxSizeBytes` | 256 KB (`MAX_OUTPUT_SIZE`) | **读取前**（stat 文件大小） | 防止读取超大文件 |
| `maxTokens` | 25,000 (`DEFAULT_MAX_OUTPUT_TOKENS`) | **读取后**（实际内容 token 计数） | 防止输出过多 token |

优先级（以 maxTokens 为例）：
```
环境变量 CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS
  > GrowthBook 实验 (tengu_amber_wren)
    > 硬编码默认值 25000
```

图片文件不受 `maxSizeBytes` 限制，有独立的 token 预算压缩机制。

---

## 6. 输入验证（validateInput）

在用户授权前执行的轻量级校验（不涉及 I/O）：

1. **PDF pages 格式校验** — 验证页码格式和范围（≤20 页）
2. **路径展开 + deny 规则检查** — 检查权限配置中的 deny 规则
3. **UNC 路径放行** — `\\server\share` 路径跳过后续检查（防止 NTLM 凭据泄露）
4. **二进制文件拒绝** — 通过扩展名判断，拒绝 `.exe`、`.zip` 等（图片/PDF/SVG 除外）
5. **设备文件阻止** — 拒绝 `/dev/zero`、`/dev/random`、`/dev/stdin` 等会阻塞的设备文件

---

## 7. 图片处理流程

```
readImageWithTokenBudget(filePath, maxTokens)
  │
  ├─ readFileBytes() — 单次读取，可限制字节数防 OOM
  │
  ├─ detectImageFormatFromBuffer() — 从 buffer 检测格式
  │
  ├─ maybeResizeAndDownsampleImageBuffer() — 标准缩放
  │   → 成功 → 检查 token 预算
  │   → 失败 → 使用原始 buffer
  │
  ├─ token 预算检查（estimatedTokens ≈ base64.length × 0.125）
  │   ├─ 未超限 → 返回
  │   └─ 超限 → 渐进压缩
  │       ├─ compressImageBufferWithTokenLimit() — 按 token 预算压缩
  │       ├─ 失败 → sharp fallback: resize(400×400) + jpeg(quality=20)
  │       └─ 都失败 → 返回原始 buffer
```

图片处理模块通过 `imageProcessor.ts` 懒加载：
- **打包模式**：优先加载 `image-processor-napi`（原生模块），fallback 到 `sharp`
- **开发模式**：直接使用 `sharp`

---

## 8. PDF 处理策略

| 条件 | 处理方式 |
|------|----------|
| 提供 `pages` 参数 | `extractPDFPages()` → 提取指定页面为 JPG 图片 |
| 页数 > `PDF_AT_MENTION_INLINE_THRESHOLD` | 报错，要求使用 `pages` 参数分页读取 |
| 文件大小 > `PDF_EXTRACT_SIZE_THRESHOLD` 或模型不支持 PDF | `extractPDFPages()` → 提取所有页面为图片 |
| 正常情况 | `readPDF()` → 直接读取 PDF 内容 |

PDF 内容通过 `document` block 或 `image` blocks 以 supplemental message 形式发送给模型。

---

## 9. 去重机制（Dedup）

**目的**：避免同一文件同一范围在会话中被重复发送完整内容，节省 cache_creation token。

**条件**（全部满足才触发）：
- GB 开关 `tengu_read_dedup_killswitch` 未开启
- `readFileState` 中存在该文件的记录
- 记录来源是 Read 工具（`offset !== undefined`，排除 Edit/Write 写入的记录）
- offset 和 limit 完全匹配
- 文件 mtime 未变化

命中后返回 `file_unchanged` 类型，模型收到提示：
> "File unchanged since last read. The content from the earlier Read tool_result in this conversation is still current..."

---

## 10. 安全机制

| 机制 | 说明 |
|------|------|
| 路径展开 | `expandPath()` 处理 `~`、相对路径、Windows 路径分隔符，防止绕过权限白名单 |
| 权限检查 | `checkReadPermissionForTool()` 检查文件读写权限规则 |
| Deny 规则 | `matchingRuleForInput()` 在 validateInput 阶段拒绝被 deny 的路径 |
| 设备文件黑名单 | 阻止 `/dev/zero`、`/dev/random`、`/dev/stdin` 等危险设备文件 |
| 二进制文件拒绝 | 通过扩展名拒绝二进制文件（仅允许文本、图片、PDF、SVG） |
| UNC 路径特殊处理 | 不对 UNC 路径执行 stat，防止 NTLM 凭据泄露 |
| 恶意代码提醒 | 非 Opus 模型读取文件时附加 `CYBER_RISK_MITIGATION_REMINDER`，提醒模型审查恶意代码 |
| macOS 截图路径 | 兼容 AM/PM 前的 thin space 和 regular space |

---

## 11. 事件埋点

| 事件名 | 触发时机 |
|--------|----------|
| `tengu_file_read_limits_override` | 调用方覆盖了默认读取限制 |
| `tengu_file_read_dedup` | 去重命中 |
| `tengu_pdf_page_extraction` | PDF 页面提取（成功/失败） |
| `tengu_session_file_read` | 每次文本文件读取（记录行数、字节数、文件类型等） |

---

## 12. UI 渲染（UI.tsx）

终端 UI 使用 React + Ink 框架渲染，所有类型统一显示**摘要信息**（不显示内容本身）：

| 输出类型 | UI 显示 |
|----------|---------|
| text | `Read 42 lines` |
| image | `Read image (1.2MB)` |
| notebook | `Read 15 cells` |
| pdf | `Read PDF (5.3MB)` |
| parts | `Read 5 pages (5.3MB)` |
| file_unchanged | `Unchanged since last read`（灰色） |
| 错误 | `File not found` / `Error reading file`（红色） |

文件路径渲染为可点击链接（`<FilePathLink>`），verbose 模式下显示完整路径和行范围。

---

## 13. 其他特性

- **并发安全**：`isConcurrencySafe() = true`，多次 Read 可并行执行
- **只读工具**：`isReadOnly() = true`
- **文件读取监听器**：`registerFileReadListener()` 允许外部服务监听文件读取事件
- **内存文件新鲜度**：自动检测的 memory 文件（`.claude/` 下）附加 mtime 提示，提醒模型 memory 可能过期
- **Skill 自动发现**：读取文件时异步发现并加载相关 skills
