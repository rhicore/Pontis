# Pontis Runtime Environment Runbook

## 结论

在这个 workspace 里运行 Pontis 脚本时，不要直接用系统 `python3`。

统一使用：

```bash
PYTHONPATH=Pontis:tools Pontis/.venv/bin/python <script>
```

原因：

- `Pontis/utils/llm.py` 依赖 `token_cache_accounting`，该文件在仓库的 `tools/token_cache_accounting.py`。
- 系统 Python 的 `sys.path` 默认不包含 `tools`，会报 `ModuleNotFoundError: token_cache_accounting`。
- Pontis graph storage 依赖 Python 包 `neo4j`。
- 系统 Python 当前没有 `neo4j` 包，会报 `RuntimeError: Neo4j storage requires the 'neo4j' Python package`。
- `Pontis/.venv/bin/python` 可以 import `neo4j`、`openai`、`yaml`。

## 正确命令模板

### BIRD SQL writer smoke

```bash
PYTHONPATH=Pontis:tools \
PONTIS_CONTEXT_RUN_ID=20260626_sql_writer_smoke \
Pontis/.venv/bin/python -m scripts.BIRD.sql_writer_cli \
  --db california_schools \
  --qids 53
```

### Dry run

用不存在的 qid 检查 import/path，不调用 LLM：

```bash
PYTHONPATH=Pontis:tools \
Pontis/.venv/bin/python -m scripts.BIRD.sql_writer_cli \
  --db california_schools \
  --qids 999999
```

### Python compile check

```bash
PYTHONPATH=Pontis:tools Pontis/.venv/bin/python -m py_compile \
  $(rg --files Pontis/sql_writer_agent Pontis/scripts/BIRD Pontis/agent | rg '\.py$')
```

## 快速环境检查

```bash
PYTHONPATH=Pontis:tools Pontis/.venv/bin/python - <<'PY'
for module in ["neo4j", "openai", "yaml", "token_cache_accounting"]:
    __import__(module)
    print(module, "ok")
PY
```

期望输出：

```text
neo4j ok
openai ok
yaml ok
token_cache_accounting ok
```

## 已确认的错误命令

### 系统 Python 缺 token_cache_accounting

```bash
PYTHONPATH=Pontis python3 -m scripts.BIRD.sql_writer_cli ...
```

典型错误：

```text
ModuleNotFoundError: No module named 'token_cache_accounting'
```

### 系统 Python 缺 neo4j

```bash
PYTHONPATH=Pontis:tools python3 -m scripts.BIRD.sql_writer_cli ...
```

典型错误：

```text
RuntimeError: Neo4j storage requires the 'neo4j' Python package.
```

## 注意

这个 runbook 只记录 Pontis runtime 环境，不记录算法设计。后续新增 Pontis 脚本时，默认使用本文的 Python 和 PYTHONPATH。
