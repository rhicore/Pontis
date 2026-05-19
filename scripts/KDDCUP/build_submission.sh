#!/usr/bin/env bash
set -euo pipefail

TEAM_ID="${1:-team1569}"
VERSION="${2:-v1}"
IMAGE="${TEAM_ID}:${VERSION}"
ARCHIVE="${TEAM_ID}_${VERSION}.tar.gz"
OUT_DIR="${3:-KDDCUP/submissions}"

mkdir -p "${OUT_DIR}"

docker build --platform=linux/amd64 \
  -t "${IMAGE}" \
  -f scripts/KDDCUP/Dockerfile .

docker save "${IMAGE}" | gzip > "${OUT_DIR}/${ARCHIVE}"

echo "image: ${IMAGE}"
echo "archive: ${OUT_DIR}/${ARCHIVE}"
