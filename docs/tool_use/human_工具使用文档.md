
# 指令
为Pintos智能体实现以下提到的工具调用，

体现在tool_use文件夹下面
每个工具一个单独文件夹
如果有共用部分可以放在utils下面

我之前在tool_use下面实现过一个版本，不过总体不太满意，如果有值得参考的地方可以参考，不能的话可以完全推倒重来


# 目录
- Glob: 物理文件与实体检索工具
- Search： 物理文件与实体语义检索工具
- Find: 带有元数据筛选功能的glob，暂时不实现
- Meta： 物理文件与实体元数据查看
- Read: read工具，既能读取文本对象，也能读取数据实体
- Grep：与claude code一致的文档检索工具
- Lookup: 为数据对象设计的值检索工具
- Bash

其中Glob，Grep，Read，Bash参考reference/CC下claude code的实现


# 关于文件和实体访问
所有的path 或者 path_pattern都是以 path::entity 为结构
匹配也是

例如如果 Agent 想找“所有数据库里的用户表”，它应该能写 data/**/*.db::*user*.table。系统需要先 glob 物理文件，再在匹配到的文件内部 glob 逻辑实体。


# glob
用途：基于名称模式的精确检索。用于快速定位物理文件或其关联的逻辑实体。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path_pattern` | string | 是 | 文件和实体匹配模式 |



```
[name] | [Info]
src/components/App.tsx | app的ts
src/components/Button.tsx | 按钮的ts文件
src/utils/helpers.ts | 帮助者的ts文件
```
如果结果被截断（超过 100 个文件），末尾追加：
```
(Results are truncated. Consider using a more specific path or pattern.)
```
无匹配时返回：`No objects found`



# search
用途：跨维度的语义检索。当 Agent 不确定具体路径或名称，仅有模糊意图时使用。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path_pattern` | string | 是 | glob 匹配模式 |
| `query` | string | 是 | 自然语言描述，语义检索|
| `struture_enhance` | bool | 否，默认 true | 是否通过知识图谱结构来增强语义检索结果|
| `BM25` | bool | 否，默认 true| 是否通过关键词检索来增强结果|

返回格式和glob一样

# find(暂不实现)
用途：带有元数据过滤功能的增强型 glob。

# grep
在claude code的实现之外，支持针对逻辑实体的交互，目前应该只能针对.chunk
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | string | 是 | 正则表达式（ripgrep 语法），这里只能匹配 |
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


# Lookup
值检索工具，用来进行值对齐，一般来说我们会为可以进行值检索的实体进行对于Distinct值的LSH索引
- .table/.col 值检索
- .json: 检索json中的几个非嵌套基本类型，String，Number，Bool，NULL，另外json中的key也是可以检索的，因为key都是字符串么

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_pattern` | string | 是 | glob 匹配模式（如 `"**/*.js"`, `"src/**/*.ts"`） |
| `type` | string | 是 | 需要检索的数据类型，因为在数据类型中一般一定会有一个类型，比如INT，STR |
| `predicate` | string | 是 | "逻辑筛选表达式。例如："INT > 100" "STR= 'active'"| 
| `output_mode` | enum | 否，默认distinct_count模式 | `"file_count"`，`"distinct_count"`两种输出模式 |


返回格式

distinct_count模式
```
relative/path/to/id.col:4:[符合匹配的一个值]
relative/path/to/id.col:4:[符合匹配的另一个值]
relative/path/to/user.col:4:[符合匹配的值]
...
```
file_count模式
```
relative/path/to/id.col:8
relative/path/to/user.col:4
...
```



# meta

查看一个物理文件/文件夹/逻辑实体的元信息
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 指定一个要查看元信息的文件或实体 |
| `all` | bool | 否，默认否 | 是否要查看文件的所有信息，否则只查看一部分 |
| `property` | string | 否 | 指定要查看某个特定属性 |


# read
read [物理文件路径名]::[逻辑实体名] 基本文件read的增强版，可以read逻辑实体的值

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 文件绝对路径 |
| `offset` | number | 否 | 起始行号（1-indexed），默认从第 1 行开始 |
| `limit` | number | 否 | 读取行数，默认最多 2000 行 |
| `sample` | number | 否 | 针对数据库中的col，table等无顺序个体进行随机采样，采样行数 |
| `pages` | string | 否 | PDF 页码范围（如 `"1-5"`, `"3"`, `"10-20"`），最多 20 页 |



# Bash
在最迫不得已的时候使用Bash