#!/usr/bin/env bash
set -euo pipefail

mkdir -p /output /logs

if [[ -n "${MODEL_API_URL:-}" ]]; then
  export OPENAI_BASE_URL="${MODEL_API_URL}"
fi
if [[ -n "${MODEL_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="${MODEL_API_KEY}"
fi
if [[ -n "${MODEL_NAME:-}" ]]; then
  export PONTIS_AGENT_MODEL="${MODEL_NAME}"
fi

export NEO4J_PASSWORD="${NEO4J_PASSWORD:-pontis_kdd_neo4j}"
export PONTIS_AGENT_MAX_TOKENS="${PONTIS_AGENT_MAX_TOKENS:-8192}"

python /app/scripts/KDDCUP/run_submission.py \
  --input-root /input \
  --output-root /output \
  --logs-root /logs \
  --team-id team1569 \
  --version v1 \
  --task-workers "${KDD_TASK_WORKERS:-4}" \
  "$@" 2>&1 | tee /logs/runtime.log
