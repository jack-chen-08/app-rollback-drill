#!/usr/bin/env bash
# 启动演练环境（api 运行在 APP_CURRENT_TAG，即"回滚前"的当前版本）。
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
  up -d "$@"

echo "==> 等待 ${DRILL_API_BASE_URL}${VERSION_CHECK_PATH} 就绪 ..."
for _ in $(seq 1 40); do
  if curl -fsS -m 2 "${DRILL_API_BASE_URL}${VERSION_CHECK_PATH}" >/dev/null 2>&1; then
    echo "==> 就绪：$(curl -fsS -m 2 "${DRILL_API_BASE_URL}${VERSION_CHECK_PATH}")"
    exit 0
  fi
  sleep 1
done
echo "==> 超时：服务未就绪，请检查 docker compose logs" >&2
exit 1
