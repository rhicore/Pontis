# Pontis 参赛适配方案与交接记录

本文档记录当前 Pontis 适配 KDD Cup 2026 Data Agents 的方案、已经完成的改动、验证结果和下一步接手事项。

## 当前结论

比赛提交的是 Docker 镜像，不是代码仓库，也不是 `prediction.csv` 压缩包。官方评测会启动镜像并挂载：

```text
/input   只读，包含所有 task_<id>
/output  可写，要求输出 /output/task_<id>/prediction.csv
/logs    可写，保存运行日志
```

官方还会注入：

```text
MODEL_API_URL
MODEL_API_KEY
MODEL_NAME
```

当前 Pontis 的核心能力继续保留：

```text
extractor / explorer / agent / graph / find / read / grep / jd / query
```

提交版新增一层 KDD runner，用来适配官方目录、模型环境变量、Docker 入口和每题 Neo4j 隔离。

## 关键设计

采用“容器内自启动 Neo4j”方案。

每个 task 固定一个独立 Neo4j 端口和独立运行目录，**不做端口复用**：

```text
task_11  -> bolt 7700 -> /logs/neo4j/task_11
task_19  -> bolt 7701 -> /logs/neo4j/task_19
task_22  -> bolt 7702 -> /logs/neo4j/task_22
...
```

并发数只控制同时运行的 task 数。默认并发为 4，任意时刻最多 4 个 Neo4j 进程在跑，但每个 task 的端口和目录仍然唯一。

官方 `/input` 是只读的，所以提交 runner 不直接把 `/input/task_x` 当 Pontis project。每题会先复制或硬链接到：

```text
/logs/work/task_x
```

Pontis 的 `.pontis`、extract 日志、临时配置都写在 `/logs` 下。

## 已新增文件

### `scripts/KDDCUP/run_submission.py`

Docker 提交入口 runner。

职责：

- 遍历 `/input/task_*`
- 给每个 task 固定分配端口：`7700 + task_index`
- 复制或硬链接 task 到 `/logs/work/task_id`
- 为每题生成动态 Pontis config：

```text
/logs/configs/task_id.pontis.yml
```

- 启动该 task 独立 Neo4j
- 等待 Bolt 可连接
- 调 `extract_public.extract_one(...)`
- 调 `test_public.run_task(...)`
- 写 `/output/task_id/prediction.csv`
- 停掉该 task 的 Neo4j
- task 失败时写 fallback `prediction.csv`，避免全局中断

默认参数：

```text
--input-root /input
--output-root /output
--logs-root /logs
--task-workers 4
--bolt-base 7700
--max-rounds 80
--effort max
--neo4j-heap-max 768m
--neo4j-pagecache 128m
```

注意：默认不保留 `/logs/work/task_x` 和 `/logs/neo4j/task_x/data`，防止 B-board 几百个任务撑爆 `/logs`。保留的是：

```text
/logs/runtime.log
/logs/submission_summary.json
/logs/port_assignments.json
/logs/tasks/task_x/submission.log
/logs/tasks/task_x/result.json
/logs/tasks/task_x/trace.log
/logs/tasks/task_x/extract_result.json
/logs/tasks/task_x/kdd_extract.log
```

### `scripts/KDDCUP/entrypoint.sh`

Docker ENTRYPOINT。

职责：

- 创建 `/output`、`/logs`
- 映射官方模型环境变量：

```text
MODEL_API_URL  -> OPENAI_BASE_URL
MODEL_API_KEY  -> OPENAI_API_KEY
MODEL_NAME     -> PONTIS_AGENT_MODEL
```

- 设置默认：

```text
NEO4J_PASSWORD=pontis_kdd_neo4j
PONTIS_AGENT_MAX_TOKENS=8192
KDD_TASK_WORKERS=4
```

- 启动：

```bash
python /app/scripts/KDDCUP/run_submission.py ... 2>&1 | tee /logs/runtime.log
```

### `scripts/KDDCUP/Dockerfile`

提交镜像 Dockerfile。

安装：

- Python 3.12 slim
- OpenJDK 17
- Neo4j Community
- Pontis Python 依赖

镜像入口：

```dockerfile
ENTRYPOINT ["/app/scripts/KDDCUP/entrypoint.sh"]
```

### `scripts/KDDCUP/build_submission.sh`

构建和保存镜像。

默认队伍 ID 和版本由命令传入：

```bash
scripts/KDDCUP/build_submission.sh team1569 v1
```

输出：

```text
KDDCUP/submissions/team1569_v1.tar.gz
```

如果官方给的队伍 ID 是纯 `1569` 而不是 `team1569`，构建时改成：

```bash
scripts/KDDCUP/build_submission.sh 1569 v1
```

### `.dockerignore`

避免把以下内容打进镜像：

```text
.git
.venv
.neo4j
.env
example_data
KDDCUP
rubbish
scripts/KDDCUP/kddcup2026-data-agents-starter-kit
*.tar.gz
*.zip
```

## 已修改文件

### `agent/utils.py`

增加官方模型变量支持：

```text
MODEL_API_URL
MODEL_API_KEY
MODEL_NAME
PONTIS_AGENT_MODEL
PONTIS_AGENT_MAX_TOKENS
```

提交环境优先读取 `MODEL_*`，避免硬编码 API key 或 base URL。

### `storage/config.py`

增加动态 config 支持：

```text
PONTIS_CONFIG_PATH
PONTIS_CONFIG
```

`run_submission.py` 会给每个 task 写一个独立 `pontis.yml`，并通过 `PONTIS_CONFIG_PATH` 让 Pontis 连接该 task 的 Neo4j 端口。

### 之前已修的提交相关问题

- `scripts/KDDCUP/extract_public.py` 中 `query_command(file=...)` 已改为 `ref=...`
- `tool/query/tool.py` 支持 CSV/JSON 数字列类型推断，空白字符串转 `NULL`
- CSV cache 增加格式版本，避免复用旧的全 TEXT 缓存
- `agent/config.py` 的 `reflection` mode 已改成只读工具，不再默认暴露写图工具
- `agent/tool_use/create_entity/prompt.py` 已移除 BIRD `knowledge` 示例
- `tool/grep/tool.py` 修正 `files_with_matches` 总数显示
- `scripts/tool/test_tools.py` 增加 CSV 空值 AVG 和 JSON 数字列回归测试

## Starter Kit 的作用

目录：

```text
scripts/KDDCUP/kddcup2026-data-agents-starter-kit
```

这是官方给的最小 baseline / 参考框架，不是 Pontis 的依赖。

它提供：

- 数据集 loader：遍历 `data/public/input/task_<id>`
- 简单 ReAct agent
- 文件/SQL/Python 工具：

```text
list_context
read_csv
read_json
read_doc
inspect_sqlite_schema
execute_context_sql
execute_python
answer
```

- 批量 runner
- 每题 timeout
- `trace.json` / `prediction.csv` / `summary.json` 输出组织

它不提供：

- Neo4j
- Pontis graph
- Pontis extractor / explorer
- find/read/grep/jd/query 工具
- JSON pattern / CSV summary / text chunk / DB summary

所以它的价值是参考提交结构、runner 设计和 `answer` 终止工具思路，而不是直接替换 Pontis。

### 已验证 starter kit

用它自己的 `uv run` 环境测试：

```bash
uv run dabench --help
```

可以运行。

用临时 config 指向 Pontis public 数据：

```yaml
dataset:
  root_path: /nfsdat2/home/bcchenslm/Projects/Pontis/example_data/KDDCUP/public/input
agent:
  model: dummy
  api_base: http://127.0.0.1:9/v1
  api_key: dummy
  max_steps: 2
  temperature: 0.0
run:
  output_dir: /tmp/dabench_pontis_runs
  run_id: status_check
  max_workers: 1
  task_timeout_seconds: 60
```

结果：

- `status` 能识别 public 50 个 task
- difficulty 分布：`easy=15, medium=23, hard=11, extreme=1`
- `inspect-task task_250` 能列出复杂 task 的 CSV/DB/JSON/knowledge 文件

`run-task` 在当前机器无真实模型服务时失败是正常的。另外当前 shell 有：

```text
ALL_PROXY=socks5://...
```

starter kit 的 httpx/openai 缺 `socksio`，所以报过 socks 依赖错误。这个不影响 Pontis Docker 提交方案。

## 已验证 Pontis 提交入口

已运行：

```bash
.venv/bin/python3 -m py_compile scripts/KDDCUP/run_submission.py scripts/KDDCUP/test_public.py scripts/KDDCUP/extract_public.py agent/utils.py storage/config.py
```

通过。

已运行：

```bash
scripts/tool/test_tools.py
```

结果：

```text
82 passed, 0 failed
```

已运行：

```bash
git diff --check
```

通过。

已运行一次极短超时 smoke：

```bash
.venv/bin/python3 scripts/KDDCUP/run_submission.py \
  --input-root example_data/KDDCUP/public/input \
  --output-root /tmp/pontis_submit_out \
  --logs-root /tmp/pontis_submit_logs \
  --limit 1 \
  --task-workers 1 \
  --task-timeout-seconds 5 \
  --debug
```

结果：

- runner 能发现 task
- 能生成 `port_assignments.json`
- 能生成每题动态 config
- 能写 fallback `/tmp/pontis_submit_out/task_11/prediction.csv`
- 能写 `/tmp/pontis_submit_logs/submission_summary.json`
- 超时/失败不会导致主进程崩溃

本机失败原因不是 runner 逻辑，而是本机没有 Java / Neo4j：

```text
java: command not found
```

Dockerfile 里会安装 Java 和 Neo4j，所以最终必须用 Docker dry run 验证。

## Docker 状态

当前服务器有 Docker client，但当前用户没有 Docker daemon 权限：

```text
permission denied while trying to connect to /var/run/docker.sock
```

所以本机无法实际 build。

可以在 WSL2 + Docker Desktop 上 build。建议仓库放在 WSL Linux 文件系统里，不要放 `/mnt/c/...`。

WSL 检查：

```bash
docker version
docker ps
```

如果能看到 server 信息，就可以 build。

构建：

```bash
scripts/KDDCUP/build_submission.sh team1569 v1
```

或：

```bash
bash scripts/KDDCUP/build_submission.sh team1569 v1
```

输出：

```text
KDDCUP/submissions/team1569_v1.tar.gz
```

Docker dry run，先少量任务：

```bash
mkdir -p KDDCUP/runs/docker_smoke/output KDDCUP/runs/docker_smoke/logs

docker run --rm \
  --cpus=16 \
  --memory=64g \
  -v "$PWD/example_data/KDDCUP/public/input:/input:ro" \
  -v "$PWD/KDDCUP/runs/docker_smoke/output:/output:rw" \
  -v "$PWD/KDDCUP/runs/docker_smoke/logs:/logs:rw" \
  -e MODEL_API_URL="$MODEL_API_URL" \
  -e MODEL_API_KEY="$MODEL_API_KEY" \
  -e MODEL_NAME="$MODEL_NAME" \
  team1569:v1 \
  --limit 1 \
  --task-workers 1
```

检查：

```bash
find KDDCUP/runs/docker_smoke/output -name prediction.csv -print
tail -n 100 KDDCUP/runs/docker_smoke/logs/runtime.log
cat KDDCUP/runs/docker_smoke/logs/submission_summary.json
```

再跑多题：

```bash
docker run --rm \
  --cpus=16 \
  --memory=64g \
  -v "$PWD/example_data/KDDCUP/public/input:/input:ro" \
  -v "$PWD/KDDCUP/runs/docker_v1/output:/output:rw" \
  -v "$PWD/KDDCUP/runs/docker_v1/logs:/logs:rw" \
  -e MODEL_API_URL="$MODEL_API_URL" \
  -e MODEL_API_KEY="$MODEL_API_KEY" \
  -e MODEL_NAME="$MODEL_NAME" \
  team1569:v1 \
  --task-workers 4
```

## 官方提交

提交包命名：

```text
<team_id>_v<N>.tar.gz
```

镜像名：

```text
<team_id>:v<N>
```

当前默认按：

```text
team1569:v1
team1569_v1.tar.gz
```

如果官方队伍 ID 原文是 `1569`，不要加 `team` 前缀。

邮件：

```text
To: kddcup@hkust-gz.edu.cn
Subject: [KDDCup2026 Data Agents] Submission - team1569 - v1

Team ID: team1569
Version: v1
Sharing link: <Google Drive link>
```

必须使用队长注册邮箱发送。

## 当前风险与下一步

### P0：必须 Docker dry run

当前最大未验证点是 Docker 镜像内 Neo4j 能否正常启动，以及 Pontis 是否能在容器中完成至少 1 个 task 的 extract + solve。

必须先跑：

```bash
docker run ... team1569:v1 --limit 1 --task-workers 1
```

再考虑全量。

### P0：确认 team_id

用户提供的是 `1569`。需要确认官方要求的 image name 是：

```text
team1569:v1
```

还是：

```text
1569:v1
```

提交前以官方邮件/系统分配的原文为准。

### P1：建议增加 `answer` 终止工具

当前 `scripts/KDDCUP/test_public.py` 仍是让 agent 最后输出 JSON，再由 runner parse。

starter kit 的 `answer(columns, rows)` 终止工具更稳。建议下一步给 Pontis 增加 KDD 专用 `answer` 工具，agent 调用后直接保存结构化结果，减少 JSON parse 失败。

### P1：Dockerfile 依赖下载风险

Dockerfile 当前用：

```dockerfile
curl -fsSL https://dist.neo4j.org/neo4j-community-${NEO4J_VERSION}-unix.tar.gz
```

这是 build 阶段下载，评测阶段不会联网。WSL build 时需要能访问该 URL。

如果 WSL 网络不稳定，可以先手动下载 Neo4j tarball 并改 Dockerfile 为本地 COPY。

### P1：Neo4j 资源

默认每个 Neo4j：

```text
heap max 768m
pagecache 128m
```

并发 4 理论上可控，但还要加 Python/LLM/tool 开销。Docker dry run 时如果 OOM，先降：

```bash
--task-workers 2
```

或降低：

```bash
--neo4j-heap-max 512m
--neo4j-pagecache 64m
```

### P2：starter kit 不必进入镜像

`.dockerignore` 已排除：

```text
scripts/KDDCUP/kddcup2026-data-agents-starter-kit
```

原因：它是参考框架，不是当前 Pontis 提交运行依赖；打进去只会增大镜像。

## 接手提醒

不要再误跑：

```bash
scripts/KDDCUP/test_public.py --extract-first ...
scripts/KDDCUP/extract_public.py --force --clear-task-graph ...
```

除非明确要重提取 public 数据。

当前任务重点不是再优化 public 分数，而是：

1. WSL/Docker build
2. Docker `--limit 1` dry run
3. 修 Docker/Neo4j 启动问题
4. Docker 多题 dry run
5. 生成 `team1569_v1.tar.gz`
6. 上传 Google Drive 并按官方邮件格式提交
