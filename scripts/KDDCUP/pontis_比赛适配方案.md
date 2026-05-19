# Pontis 参赛适配方案

本文档记录在当前 Pontis 代码基本定型的前提下，如何把 Pontis 包装成 KDD Cup 2026 Data Agents 可提交方案。

## 目标

比赛评测不会进入交互式命令行，也不会手动指定单题。提交镜像启动后必须自动完成：

1. 遍历 `/input/task_*`
2. 读取每题的 `task.json` 和 `context/`
3. 调用比赛注入的模型服务
4. 写出 `/output/task_<id>/prediction.csv`
5. 把运行日志写到 `/logs/runtime.log`

因此，适配重点不是重写 Pontis，而是在 Pontis 外层新增一个 KDD runner。

## 当前 Pontis 与比赛要求的差距

### 1. 入口形态不同

Pontis 当前主要入口是：

```bash
pontis <project>
pontis <project>:find ...
pontis <project>:query ...
```

这是面向人工交互或单次工具调用的入口。

比赛需要的是非交互式批处理入口：

```bash
python scripts/KDDCUP/run_kdd.py --input /input --output /output --logs /logs
```

这个入口应负责遍历所有 task，并且每完成一题就立即写出 `prediction.csv`。

### 2. 输出格式不同

Pontis agent 当前最终回答通常是自然语言。

比赛只认 CSV：

```text
/output/task_<id>/prediction.csv
```

所以 KDD runner 需要约束 agent 最终输出为结构化表格：

```json
{
  "columns": ["column_a", "column_b"],
  "rows": [
    ["value_a", "value_b"]
  ]
}
```

然后由 runner 统一写 CSV。

### 3. 模型配置变量不同

Pontis 当前主要使用：

```text
PONTIS_AGENT_API_KEY
OPENAI_API_KEY
OPENAI_BASE_URL
```

比赛评测注入：

```text
MODEL_API_URL
MODEL_API_KEY
MODEL_NAME
```

KDD 入口需要在启动时把比赛变量映射到 Pontis/OpenAI-compatible 配置：

```text
OPENAI_BASE_URL = MODEL_API_URL
OPENAI_API_KEY = MODEL_API_KEY
PONTIS_AGENT_API_KEY = MODEL_API_KEY
PONTIS_AGENT_MODEL = MODEL_NAME
```

更稳的做法是在 KDD runner 内部直接读取 `MODEL_*`，不要依赖本地开发配置文件。

### 4. Graph backend 运行环境不同

当前 Pontis 默认依赖 Neo4j：

```text
bolt://localhost:7687
```

比赛只启动一个提交容器。官方不会额外启动 Neo4j 服务。

可选方案：

| 方案 | 说明 | 建议 |
| --- | --- | --- |
| 无 Neo4j KDD adapter | 只复用 Pontis 的 source/tool/agent 思路，每题临时分析 context | 第一版推荐 |
| 容器内自启动 Neo4j | 镜像内安装 Neo4j，ENTRYPOINT 启动并等待 bolt 可用 | 可做，但工程风险高 |
| 嵌入式本地 graph backend | 为 Pontis 增加 SQLite/local graph backend | 中期更优 |

第一版参赛应优先保证能稳定写出结果，所以建议先做无外部服务版本。

### 5. 并发与隔离

hidden B-board 任务量较大，runner 需要支持并发。

要求：

- 每个 task 独立工作目录
- 每个 task 独立 trace/log
- 不修改 `/input`
- 不让多个 task 共用同一个可写 graph namespace
- task 失败不能影响其他 task

如果继续使用 Neo4j，需要额外处理数据库隔离、锁竞争和清理；无 Neo4j adapter 会简单很多。

## 推荐目录组织

当前 `KDDCUP/` 已经在 `.gitignore` 中，适合作为本地资料和数据目录：

```text
KDDCUP/
├── overview.md
├── 技术规范.md
├── public/
├── downloaded_file.zip
├── kddcup2026-data-agents-starter-kit/
├── runs/
├── logs/
├── submissions/
└── pontis_比赛适配方案.md
```

建议真正进 git 的比赛代码放在：

```text
scripts/KDDCUP/
├── run_kdd.py
├── evaluate_public.py
├── Dockerfile
├── build_image.sh
└── README.md
```

`KDDCUP/` 放数据、官方材料、运行结果和提交包；`scripts/KDDCUP/` 放我们自己写的可复现代码。

## 第一版 MVP

第一版目标是先跑通 public 50 题和 Docker 提交流程。

### 必做

1. 新增 `scripts/KDDCUP/run_kdd.py`
   - 默认读取 `/input`
   - 默认写入 `/output`
   - 默认日志目录 `/logs`
   - 支持本地参数覆盖

2. 新增 task runner
   - 读取 `task.json`
   - 扫描 `context/`
   - 给 agent 提供文件列表、CSV/JSON/doc 预览、SQLite schema、SQL 执行、Python 执行工具
   - 要求最终返回 `columns` 和 `rows`

3. 新增 CSV 写出逻辑
   - 每题完成后立即写 `/output/task_<id>/prediction.csv`
   - 即使失败也尽量写一个空表或错误 trace，避免整个进程中断

4. 新增模型配置适配
   - 官方评测读取 `MODEL_API_URL`
   - 本地调试允许 `.env` 或命令行传入
   - 不在镜像中硬编码 key

5. 新增 Dockerfile
   - `linux/amd64`
   - 设置 `ENTRYPOINT`
   - 启动命令把 stdout/stderr tee 到 `/logs/runtime.log`

6. 新增本地 public evaluator
   - 读取 `KDDCUP/public/output/task_*/gold.csv`
   - 对比本地预测结果
   - 实现列签名匹配、数值两位小数归一、空值归一

### 可延后

- Neo4j 容器内启动
- 跨题 memory
- 复杂 graph 写入
- 多 agent 协同
- 对 extreme 长文档的专门 memory manager

## Pontis 能复用的能力

第一版不需要完整启动 Pontis graph，也可以复用 Pontis 的思想和部分模块：

| 能力 | 复用方式 |
| --- | --- |
| SQLite schema/query | 复用或仿照 `tool/query` |
| 文件发现 | 复用 `find` |
| 文档搜索 | 复用 `find` / `grep` |
| agent loop | 可以复用 `agent/agent.py`，但需要结构化 answer 工具 |
| guardrails | 保留 SQL 只读、路径限制、轮数限制 |
| extractor 思路 | 每题启动前生成轻量 schema/profile |

如果复用现有 `PontusAgent`，需要避免它初始化 Neo4j workspace；否则 KDD runner 会在无 Neo4j 环境失败。

## 建议实现顺序

### Step 1: 先做纯 runner

写一个不依赖 Neo4j 的最小 ReAct runner，工具范围限制在 task 的 `context/` 内：

- `list_context`
- `read_csv`
- `read_json`
- `read_doc`
- `inspect_sqlite_schema`
- `execute_context_sql`
- `execute_python`
- `answer`

这一步可以直接参考官方 starter kit，但把 prompt 和工具策略改成 Pontis 风格。

### Step 2: 跑 public 50 题

本地运行：

```bash
python scripts/KDDCUP/run_kdd.py \
  --input KDDCUP/public/input \
  --output KDDCUP/runs/local_v1/output \
  --logs KDDCUP/runs/local_v1/logs
```

然后评分：

```bash
python scripts/KDDCUP/evaluate_public.py \
  --gold KDDCUP/public/output \
  --pred KDDCUP/runs/local_v1/output
```

### Step 3: 做 Docker dry run

```bash
docker build --platform=linux/amd64 \
  -t pontis-kdd:local \
  -f scripts/KDDCUP/Dockerfile .

docker run --rm \
  -v "$PWD/KDDCUP/public/input:/input:ro" \
  -v "$PWD/KDDCUP/runs/docker_v1/output:/output:rw" \
  -v "$PWD/KDDCUP/runs/docker_v1/logs:/logs:rw" \
  -e MODEL_API_URL="$MODEL_API_URL" \
  -e MODEL_API_KEY="$MODEL_API_KEY" \
  -e MODEL_NAME="$MODEL_NAME" \
  pontis-kdd:local
```

### Step 4: 再考虑接入 Pontis graph

如果 MVP 已经稳定，再评估是否值得把 Neo4j 带进容器。

判断标准：

- public 分数是否明显被 schema/relationship 理解限制
- Neo4j 启动和写入是否占用过多时间
- 多 worker 是否互相污染
- Docker 镜像是否接近 10GB 限制

## 提交镜像要求

比赛提交需要：

```bash
docker build --platform=linux/amd64 -t <team_id>:v<N> -f scripts/KDDCUP/Dockerfile .
docker save <team_id>:v<N> | gzip > KDDCUP/submissions/<team_id>_v<N>.tar.gz
```

注意：

- 必须用 `docker save`，不是 `docker export`
- image 名称必须是 `<team_id>:v<N>`
- archive 名称必须是 `<team_id>_v<N>.tar.gz`
- 输出路径必须是 `/output/task_<id>/prediction.csv`
- 镜像内不能硬编码 API key
- 评测时无外网，只能访问 `MODEL_API_URL`

## 风险清单

| 风险 | 处理 |
| --- | --- |
| agent 输出自然语言，无法解析成 CSV | 强制使用 `answer` 工具 |
| 某题失败导致全局退出 | per-task try/except，失败后继续 |
| 超时导致结果丢失 | 每题完成立即落盘 |
| 大 CSV 读入爆内存 | 默认 preview，精确计算交给 SQL/Python 分块 |
| SQL 写操作误改数据 | 只允许 SELECT / WITH，只读连接 |
| 文件路径逃逸 | 所有路径必须 resolve 在 task `context/` 内 |
| 模型变量不兼容 | KDD runner 直接读取 `MODEL_*` |
| Neo4j 不可用 | 第一版不依赖 Neo4j |

## 总结

当前 Pontis 不需要 fork 出一个单独项目。更合适的方式是：

- Pontis 保持为核心能力库
- `scripts/KDDCUP/` 作为比赛提交层
- `KDDCUP/` 作为本地数据、官方资料、运行产物目录

第一版先做无 Neo4j、结构化输出、Docker 可提交的 MVP。等 public 50 题和 Docker dry run 稳定后，再决定是否把完整 Pontis graph 能力接入比赛版本。
