#!/usr/bin/env bash
# 把演练环境恢复到"当前版本"（APP_CURRENT_TAG），用于重复跑演练。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
# shellcheck disable=SC1091
source ./.env
set +a

APP_IMAGE="${APP_IMAGE_REPO}:${APP_CURRENT_TAG}" \
APP_IMAGE_TAG="${APP_CURRENT_TAG}" \
APP_FORCE_UNHEALTHY=0 \
  docker compose -f "${COMPOSE_FILE}" --env-file .env --project-name "${COMPOSE_PROJECT_NAME}" \
  up -d --no-deps --force-recreate api

for _ in $(seq 1 40); do
  if curl -fsS -m 2 "${DRILL_API_BASE_URL}${VERSION_CHECK_PATH}" >/dev/null 2>&1; then
    echo "==> 已恢复 api 到 ${APP_IMAGE_REPO}:${APP_CURRENT_TAG}"
    exit 0
  fi
  sleep 1
done
echo "==> 恢复后服务未就绪" >&2
exit 1
