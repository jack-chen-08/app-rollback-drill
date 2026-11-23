#!/usr/bin/env bash
# 构建回滚演练用的两个镜像标签（当前版 / 上一版），版本号在构建期烧进镜像。
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "缺少 .env，请先执行: cp .env.example .env" >&2
  exit 3
fi

set -a
# shellcheck disable=SC1091
source ./.env
set +a

: "${APP_IMAGE_REPO:?APP_IMAGE_REPO 未设置}"
: "${APP_CURRENT_TAG:?APP_CURRENT_TAG 未设置}"
: "${APP_ROLLBACK_TAG:?APP_ROLLBACK_TAG 未设置}"

for tag in "$APP_CURRENT_TAG" "$APP_ROLLBACK_TAG"; do
  echo "==> docker build ${APP_IMAGE_REPO}:${tag} (APP_VERSION_LABEL=${tag})"
  docker build \
    --build-arg "BASE_IMAGE=${APP_BASE_IMAGE:-python:3.11-slim}" \
    --build-arg "APP_VERSION_LABEL=${tag}" \
    -t "${APP_IMAGE_REPO}:${tag}" \
    drill/app
done

echo "==> 已构建镜像:"
docker images --format '  {{.Repository}}:{{.Tag}}  {{.ID}}  {{.Size}}' | grep -F "${APP_IMAGE_REPO}" || true
